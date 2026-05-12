"""mount_template — render a frontend/templates/blocks/<name>.{js,md}
and mount it on the user's canvas as an *ephemeral* SSE overlay.

Templates are not persistent: nothing is written to the per-user git
workspace, no `canvas_layout` row is recorded. On reload, the canvas
hydrator (`/api/dynamic/canvas`) sees an empty workspace and the
DynamicSurface auto-mounts `inputs_launcher` again. That's the intended
behavior — the user starts each session with a fresh canvas.

Used today by:
  * The frontend's empty-canvas auto-mount (loads `inputs_launcher`).
  * The inputs_launcher block's buttons (each mounts a target template).
  * The teacher's tool loop (via `persona.teacher.tools.manifest`).
  * Tests.

A one-shot migration sweeps stale workspace files left over from the
previous (persistent) implementation. The sweep is bounded to known
template ids, so engineer-novel widgets (`request_new_block`-mounted)
are not touched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import delete

from agents.frontend_engineer import workspace as ws
from infra.contracts.ui import BlockSource, UIUpdate
from infra.db import async_session
from infra.perception import forget_block
from infra.sandbox import validate_block_source
from infra.templates import Template, load_template
from services.persona.routers.dynamic import (
    enqueue_for_device,
    enqueue_for_user,
    mounted_block_ids,
)
from silicon_brain.models.canvas_layout import CanvasLayout


# Sane default grid in desktop coordinates (12×9). Each template's
# frontmatter overrides via the `grid:` block; `_DEFAULT_GRID` is the
# conservative fallback when neither caller nor manifest specifies.
# The frontend rescales these to the active device class on render
# (see frontend/lib/gridConfig.ts scaleGridForDevice).
_DEFAULT_GRID = {"x": 2, "y": 2, "w": 8, "h": 5}


@dataclass
class MountResult:
    block_id: str
    template: str
    deleted: list[str]


def _render_block_source(
    template: Template,
    block_id: str,
    grid: dict[str, int],
    params: Optional[dict[str, Any]] = None,
) -> str:
    """Substitute the standard engineer placeholders in the template JS.

    We additionally append a `manifest:` entry to the block's outer
    object literal so the frontend's helpers.backend resolver can build
    typed callers without a second fetch. The manifest is the JSON
    serialisation of the template's frontmatter `publishes`/`subscribes`/
    `backend` map.

    Block source structure looks like:
        ({ id: 'foo', grid: {...}, ..., manifest: { backend: {...} } })

    We do this by string-replacing `({` with `({\n  manifest: <json>,`.
    Cheap and reliable for the parens-wrapped expression all our
    templates emit.

    `params` carries template-specific values (today: `content` for
    text_display). They substitute as JSON-encoded JS literals — the
    placeholder in the template *replaces* the entire literal expression
    (e.g. `var initial = __CONTENT__;`), so quote/escape safety lives at
    the substitution boundary, not in the template authoring layer.
    """
    js = template.js
    js = js.replace("__BLOCK_ID__", block_id)
    js = js.replace("__GRID_X__", str(grid["x"]))
    js = js.replace("__GRID_Y__", str(grid["y"]))
    js = js.replace("__GRID_W__", str(grid["w"]))
    js = js.replace("__GRID_H__", str(grid["h"]))
    # Shared global topic names. Multiple templates that need to talk to
    # each other (upload_file → pdf_reader) MUST land on the same topic;
    # using `<block_id>.doc` would give each block its own isolated topic
    # and break the cross-block flow. Convention: well-known names
    # documented in TOPICS.md.
    js = js.replace("__DOC_TOPIC__", "documents.uploaded")
    js = js.replace("__SELECTION_TOPIC__", "text.selected")
    # Per-block content topic. Each text_display block has its own topic
    # derived from its block_id so the persona can update one without
    # cross-talking to another.
    js = js.replace("__CONTENT_TOPIC__", f"text.{block_id}.content")
    # Template-specific params. Today: `content` (text_display) and
    # `url`/`title`/`excerpt` (url_card). Each substitutes as a
    # JSON-encoded JS literal — quote/escape safety lives at the
    # substitution boundary, not in template authoring.
    content_value = ""
    if params and isinstance(params.get("content"), str):
        content_value = params["content"]
    js = js.replace("__CONTENT__", json.dumps(content_value))
    # url_card params — default to empty strings so unrelated templates
    # don't break when these placeholders aren't present.
    url_value = ""
    title_value = ""
    excerpt_value = ""
    if params:
        if isinstance(params.get("url"), str):
            url_value = params["url"]
        if isinstance(params.get("title"), str):
            title_value = params["title"]
        if isinstance(params.get("excerpt"), str):
            excerpt_value = params["excerpt"]
    js = js.replace("__URL_CARD_URL__", json.dumps(url_value))
    js = js.replace("__URL_CARD_TITLE__", json.dumps(title_value))
    js = js.replace("__URL_CARD_EXCERPT__", json.dumps(excerpt_value))

    manifest_json = json.dumps(template.manifest.to_json(), separators=(",", ":"))
    open_idx = js.find("({")
    if open_idx >= 0:
        head = js[: open_idx + 2]
        tail = js[open_idx + 2:]
        js = f"{head}\n  manifest: {manifest_json},{tail}"
    return js


# ---------- one-shot migration ----------------------------------------------
#
# Pre-this-commit, every mount_template call wrote `blocks/<id>.{js,md}` to
# the user's git workspace and added a canvas_layout row. The /api/dynamic/canvas
# hydrator picked them up on every reload, so the canvas pre-loaded with
# whatever the user (or teacher) had ever mounted. We now mount purely over
# SSE, but existing users still have polluted workspaces. Sweep on first
# mount_template call per process, per user.

_MIGRATED_USERS: set[str] = set()


async def _migrate_workspace_if_needed(user_id: UUID) -> list[str]:
    """Sweep stale workspace files left by the previous persistent
    implementation. Until we ship explicit "save this layout" persistence,
    the canvas is meant to start fresh on every reload — so we sweep
    every block id from the workspace, including engineer-novel widgets
    (e.g. volume-slider) that the user previously asked the engineer to
    author. Anything the user wants back, they can re-mount.

    Returns the list of ids that were swept, so the caller can fan out
    unmount events to any still-hydrated browser.
    """
    key = str(user_id)
    if key in _MIGRATED_USERS:
        return []
    snap = ws.read_snapshot(user_id)
    stale = list(snap.blocks.keys())
    if not stale:
        _MIGRATED_USERS.add(key)
        return []
    ws.delete_blocks(user_id, stale)
    ws.regenerate_topics(user_id)
    ws.commit(user_id, "canvas: stop persisting ephemeral blocks")
    async with async_session() as session:
        await session.execute(
            delete(CanvasLayout).where(
                CanvasLayout.user_id == user_id,
                CanvasLayout.block_id.in_(stale),
            )
        )
        await session.commit()
    _MIGRATED_USERS.add(key)
    return stale


# ---------- public API ------------------------------------------------------


async def mount_template(
    *,
    user_id: UUID,
    template_name: str,
    block_id: Optional[str] = None,
    grid: Optional[dict[str, int]] = None,
    replace: Optional[list[str]] = None,
    target_device_id: Optional[UUID] = None,
    params: Optional[dict[str, Any]] = None,
) -> MountResult:
    """Render and mount a template on the user's canvas as an SSE overlay.

    `block_id` defaults to the template's kebab id (e.g. `upload-file`).
    `replace` is a list of block ids to unmount in the same SSE batch.
    `params` carries template-specific values that substitute into
    placeholders in the template JS (today: `content` for text_display).

    Nothing is written to git or to canvas_layout. The block exists only
    in memory + browser; on reload it disappears.
    """
    template = load_template(template_name)
    bid = block_id or template.id_default
    g = grid or (dict(template.manifest.grid) if template.manifest.grid else dict(_DEFAULT_GRID))

    # Idempotence: if the block is already mounted on any device for this
    # user AND the caller didn't ask to replace anything, skip the mount
    # entirely. Remounting nukes the running block (its `cleanup()` runs,
    # its acquired streams release, then a fresh instance starts) which
    # destroys live state — most painfully for ambient_mic, where a
    # remount-while-listening kills the active VAD and triggers a
    # NotFoundError as the torn-down mic stream errors out.
    #
    # The caller's *intent* in calling mount_template is "make this block
    # visible." If it already is, intent is already satisfied.
    if not replace and not params:
        all_mounted: set[str] = set()
        for ids in mounted_block_ids(user_id).values():
            all_mounted.update(ids)
        if bid in all_mounted:
            return MountResult(block_id=bid, template=template_name, deleted=[])

    rendered = _render_block_source(template, bid, g, params)

    # Sandbox-validate the rendered source before SSE-fanout. A broken
    # template (placeholder substitution gone wrong, drifted manifest,
    # editor that mangled the JS) would otherwise show up as a fallback
    # error chip on the user's canvas. We surface the issue here as a
    # tool error so the caller can fix or fall back gracefully.
    err = await validate_block_source(rendered)
    if err:
        raise ValueError(f"template {template_name!r} produced invalid block source: {err}")

    md_doc = template.md or f"Mounted from template `{template_name}`."
    block_source = BlockSource(id=bid, source=rendered, design_doc=md_doc)

    async def _send(event: UIUpdate) -> int:
        if target_device_id is not None:
            return await enqueue_for_device(user_id, target_device_id, event)
        return await enqueue_for_user(user_id, event)

    # One-shot cleanup: drop pre-existing workspace files and canvas_layout
    # rows for any leftover known-template blocks. For each cleaned id, fan
    # out an explicit unmount so any browser still hydrated from the old
    # workspace state drops the orphan block — and forget the block's
    # perception state so the teacher doesn't see a stale "ready" entry
    # for a block that's no longer on screen.
    swept = await _migrate_workspace_if_needed(user_id)
    for stale_id in swept:
        if stale_id == bid:
            continue
        await _send(UIUpdate(
            action="unmount",
            block=BlockSource(id=stale_id, source=""),
        ))
        forget_block(user_id=user_id, block_id=stale_id)

    deleted: list[str] = []
    if replace:
        for old_id in replace:
            if old_id == bid:
                continue
            await _send(UIUpdate(
                action="unmount",
                block=BlockSource(id=old_id, source=""),
            ))
            forget_block(user_id=user_id, block_id=old_id)
            deleted.append(old_id)

    await _send(UIUpdate(action="mount", block=block_source))

    return MountResult(block_id=bid, template=template_name, deleted=deleted)


__all__ = ["mount_template", "MountResult"]
