"""mount_template — materialize a frontend/templates/blocks/<name>.{js,md}
into a user's per-user-git workspace and mount it on the canvas.

This is the deterministic, no-LLM counterpart to `tools/request_ui_block`:
the engineer agent isn't asked to write code; we just substitute the
template's placeholders, commit, and ship a mount UIUpdate.

Used today by:
  * The frontend's empty-canvas auto-mount (loads `inputs_launcher`).
  * The inputs_launcher block's buttons (each mounts a target template).
  * Tests.

The persona's tool loop could also use this in a follow-up, but that's
not in this PR's scope.

Placeholder substitution is conservative — only the well-known engineer
placeholders (`__BLOCK_ID__`, `__GRID_X__`, etc.) get filled. Anything
custom is the caller's responsibility.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.frontend_engineer import workspace as ws
from infra.contracts.ui import BlockSource, UIUpdate
from infra.db import async_session
from infra.devices import registry as device_registry
from infra.templates import Template, load_template
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user
from silicon_brain.models.canvas_layout import CanvasLayout


# Sane default grid bounds. Each template's frontmatter could override
# these in the future; for now we hand back a centered card and let the
# user / next mount call rearrange.
_DEFAULT_GRID = {"x": 30, "y": 30, "w": 100, "h": 30}


@dataclass
class MountResult:
    block_id: str
    template: str
    deleted: list[str]


def _render_block_source(template: Template, block_id: str, grid: dict[str, int]) -> str:
    """Substitute the standard engineer placeholders in the template JS.

    We additionally append a `manifest:` entry to the block's outer
    object literal so the frontend's helpers.backend resolver can build
    typed callers without a second fetch. The manifest is the JSON
    serialisation of the template's frontmatter `publishes`/`subscribes`/
    `backend` map.

    Block source structure looks like:
        ({ id: 'foo', grid: {...}, ..., manifest: { backend: {...} } })

    We do this by string-replacing the closing `})` with
    `,\n  manifest: <json>,\n})`. Cheap and reliable for the parens-wrapped
    expression all our templates emit.
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

    # Inject the manifest JSON as the first field of the block object
    # literal. We can't inject before the *closing* `})` because the run()
    # method's body contains arbitrary nested `})` patterns (e.g.
    # `appendChild(makeButton(...))`). Inserting at the open is unambiguous
    # because every template starts with `({` (a parens-wrapped object
    # literal).
    manifest_json = json.dumps(template.manifest.to_json(), separators=(",", ":"))
    open_idx = js.find("({")
    if open_idx >= 0:
        head = js[: open_idx + 2]
        tail = js[open_idx + 2:]
        js = f"{head}\n  manifest: {manifest_json},{tail}"
    return js


async def _online_device_ids(user_id: UUID) -> list[UUID]:
    devices = await device_registry.list_for_user(user_id)
    return [d.device_id for d in devices if d.online]


async def _record_mount(user_id: UUID, block_id: str, device_ids: list[UUID]) -> None:
    if not device_ids:
        return
    async with async_session() as session:
        for did in device_ids:
            stmt = (
                pg_insert(CanvasLayout)
                .values(user_id=user_id, device_id=did, block_id=block_id)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "device_id", "block_id"],
                )
            )
            await session.execute(stmt)
        await session.commit()


async def _record_unmount(
    user_id: UUID, block_id: str, device_ids: list[UUID] | None
) -> None:
    async with async_session() as session:
        stmt = CanvasLayout.__table__.delete().where(
            CanvasLayout.user_id == user_id,
            CanvasLayout.block_id == block_id,
        )
        if device_ids is not None:
            stmt = stmt.where(CanvasLayout.device_id.in_(device_ids))
        await session.execute(stmt)
        await session.commit()


async def mount_template(
    *,
    user_id: UUID,
    template_name: str,
    block_id: Optional[str] = None,
    grid: Optional[dict[str, int]] = None,
    replace: Optional[list[str]] = None,
    target_device_id: Optional[UUID] = None,
) -> MountResult:
    """Materialize and mount a template into the user's workspace.

    `block_id` defaults to the template's kebab id (e.g. `upload-file`).
    `replace` is a list of block ids to unmount + delete (and remove from
    canvas_layout) in the same SSE batch.
    """
    template = load_template(template_name)
    bid = block_id or template.id_default
    # Caller-provided grid wins; otherwise use the template's manifest
    # `grid:` if it declared one; otherwise the conservative default.
    g = grid or (dict(template.manifest.grid) if template.manifest.grid else dict(_DEFAULT_GRID))

    # Render + persist to git so reloads rehydrate.
    rendered = _render_block_source(template, bid, g)
    md_doc = template.md or f"Mounted from template `{template_name}`."
    ws.write_files(
        user_id,
        [
            ws.FileWrite(path=f"blocks/{bid}.js", content=rendered),
            ws.FileWrite(path=f"blocks/{bid}.md", content=md_doc),
        ],
    )
    ws.regenerate_topics(user_id)
    ws.commit(user_id, f"mount-template: {template_name} as {bid}")

    # Decide who receives the SSE events + canvas_layout writes.
    if target_device_id is not None:
        mount_targets = [target_device_id]
        unmount_targets: list[UUID] | None = [target_device_id]
        async def _send(event: UIUpdate) -> int:
            return await enqueue_for_device(user_id, target_device_id, event)
    else:
        mount_targets = await _online_device_ids(user_id)
        unmount_targets = None
        async def _send(event: UIUpdate) -> int:
            return await enqueue_for_user(user_id, event)

    deleted: list[str] = []
    if replace:
        for old_id in replace:
            if old_id == bid:
                continue
            # 1) Delete files from the workspace + commit so reloads stay clean.
            ws_deleted = ws.delete_blocks(user_id, [old_id])
            if ws_deleted:
                ws.regenerate_topics(user_id)
                ws.commit(user_id, f"mount-template: replace {old_id}")
            # 2) Remove from canvas_layout.
            await _record_unmount(user_id, old_id, unmount_targets)
            # 3) SSE unmount event.
            await _send(UIUpdate(
                action="unmount",
                block=BlockSource(id=old_id, source=""),
            ))
            deleted.append(old_id)

    # Mount the new block. We must enqueue the SSE event AND write
    # canvas_layout — the perception subsystem keys off layout rows.
    block_source = BlockSource(id=bid, source=rendered, design_doc=md_doc)
    await _send(UIUpdate(action="mount", block=block_source))
    await _record_mount(user_id, bid, mount_targets)

    return MountResult(block_id=bid, template=template_name, deleted=deleted)


__all__ = ["mount_template", "MountResult"]
