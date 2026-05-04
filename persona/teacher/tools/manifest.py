"""Teacher's tool manifest — one `ToolSpec` per verb.

The teacher's LLM sees these as callable functions. Each spec binds an
executor that closes over `user_id` so the LLM never has to (and can't)
forge a different one.

Tool results returned to the LLM should stay compact — they re-enter the
context on every subsequent turn. We summarise (count + ids) rather than
echoing back the full payload.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List
from uuid import UUID

from infra.contracts.ui import BlockSpec
from infra.model.tools import ToolSpec

from tools.block_action import block_action
from tools.interactive_graph import interactive_graph
from tools.list_media import list_media
from tools.mount_template import mount_template
from tools.point_arrow import point_arrow
from tools.push_block_content import push_block_content
from tools.read_media import read_media
from tools.request_ui_block import request_ui_block
from tools.speak import speak


def _make_list_media(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        inv = await list_media(user_id)
        # Compact — full DTO would balloon context across turns.
        canvases = [
            {
                "device_id": str(c.device_id),
                "device_class": c.device_class,
                "online": c.online,
                "block_ids": [b.id for b in c.blocks],
            }
            for c in inv.canvases
        ]
        voices = [
            {
                "device_id": str(v.device_id),
                "device_class": v.device_class,
                "online": v.online,
            }
            for v in inv.voices
        ]
        return json.dumps({"canvases": canvases, "voices": voices})
    return executor


def _make_read_media(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        block_ids = args.get("block_ids") or None
        device_ids_raw = args.get("device_ids") or None
        device_ids = None
        if device_ids_raw:
            try:
                device_ids = [UUID(d) for d in device_ids_raw]
            except (ValueError, TypeError):
                return json.dumps({"error": "device_ids must be valid UUIDs"})
        perc = await read_media(user_id, block_ids=block_ids, device_ids=device_ids)

        # Compact serialisation — keep only what the persona reasons over.
        canvases = []
        for c in perc.canvases:
            canvases.append({
                "device_id": str(c.device_id),
                "device_class": c.device_class,
                "online": c.online,
                "blocks": [
                    {
                        "id": b.id,
                        "title": b.title,
                        "state": (b.state.model_dump() if b.state else None),
                        "last_updated_s_ago": (
                            round(b.last_updated_s_ago, 1)
                            if b.last_updated_s_ago is not None else None
                        ),
                    }
                    for b in c.blocks
                ],
            })
        voices = []
        for v in perc.voices:
            voices.append({
                "device_id": str(v.device_id),
                "device_class": v.device_class,
                "online": v.online,
                "recent_utterances": [
                    {
                        "text": u.text,
                        "voice": u.voice,
                        "played_at": u.played_at.isoformat(),
                    }
                    for u in v.recent_utterances[-5:]   # last 5 — context-friendly
                ],
            })
        return json.dumps({"canvases": canvases, "voices": voices})
    return executor


def _make_mount_template(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        template_name = (args.get("template") or "").strip()
        if not template_name:
            return json.dumps({"error": "template is required"})
        replace = args.get("replace") or None
        if replace is not None and not isinstance(replace, list):
            return json.dumps({"error": "replace must be a list of block ids"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        try:
            result = await mount_template(
                user_id=user_id,
                template_name=template_name,
                replace=replace,
                target_device_id=target_uuid,
            )
        except FileNotFoundError:
            return json.dumps({
                "error": f"unknown template {template_name!r}",
            })
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps({
            "block_id": result.block_id,
            "template": result.template,
            "deleted": result.deleted,
        })
    return executor


def _make_request_new_block(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        description = (args.get("description") or "").strip()
        if not description:
            return json.dumps({"error": "description is required"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        spec = BlockSpec(description=description)
        blocks = await request_ui_block(spec, user_id, target_device_id=target_uuid)
        return json.dumps({"mounted_block_ids": [b.id for b in blocks]})
    return executor


def _make_push_block_content(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        block_id = (args.get("block_id") or "").strip()
        topic = (args.get("topic") or "").strip()
        if not block_id or not topic:
            return json.dumps({"error": "block_id and topic are required"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        delivered = await push_block_content(
            user_id=user_id,
            block_id=block_id,
            topic=topic,
            value=args.get("value"),
            target_device_id=target_uuid,
        )
        return json.dumps({"delivered_to": delivered})
    return executor


def _make_interactive_graph(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        mermaid_raw = args.get("mermaid")
        if mermaid_raw is not None and not isinstance(mermaid_raw, str):
            return json.dumps({"error": "mermaid must be a string"})
        mermaid = mermaid_raw.strip() if isinstance(mermaid_raw, str) else None
        if mermaid == "":
            mermaid = None

        highlight_raw = args.get("highlight_node")
        if highlight_raw is not None and not isinstance(highlight_raw, str):
            return json.dumps({"error": "highlight_node must be a string"})
        highlight_node = highlight_raw.strip() if isinstance(highlight_raw, str) else None
        if highlight_node == "":
            highlight_node = None

        clear = bool(args.get("clear") or False)

        if mermaid is None and highlight_node is None and not clear:
            return json.dumps({
                "error": "pass at least one of mermaid, highlight_node, or clear=true",
            })

        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})

        result = await interactive_graph(
            user_id=user_id,
            mermaid=mermaid,
            highlight_node=highlight_node,
            clear=clear,
            target_device_id=target_uuid,
        )
        return json.dumps(result)
    return executor


def _make_point_arrow(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        from_id = (args.get("from_block_id") or "").strip()
        to_id = (args.get("to_block_id") or "").strip()
        # Allow both empty to mean "clear the arrow".
        if (bool(from_id) ^ bool(to_id)):
            return json.dumps({"error": "from_block_id and to_block_id must both be set, or both empty to clear"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        result = await point_arrow(
            user_id=user_id,
            from_block_id=from_id,
            to_block_id=to_id,
            label=args.get("label"),
            target_device_id=target_uuid,
        )
        return json.dumps(result)
    return executor


def _make_speak(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        text = (args.get("text") or "").strip()
        if not text:
            return json.dumps({"error": "text is required"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        try:
            delivered = await speak(
                user_id=user_id,
                text=text,
                voice=args.get("voice"),
                speed=args.get("speed"),
                lang=args.get("lang"),
                target_device_id=target_uuid,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"delivered_to": delivered})
    return executor


def _make_block_action(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        block_id = (args.get("block_id") or "").strip()
        action = (args.get("action") or "").strip()
        if not block_id or not action:
            return json.dumps({"error": "block_id and action are required"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        try:
            delivered = await block_action(
                user_id=user_id,
                block_id=block_id,
                action=action,
                options=args.get("options") or {},
                target_device_id=target_uuid,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"delivered_to": delivered})
    return executor


def build_tools(user_id: UUID) -> List[ToolSpec]:
    """Return the per-request tool list for the teacher.

    Each call gets a fresh list with executors bound to this user_id. The
    LLM cannot supply a different user_id — that's enforced by closure.
    """
    return [
        ToolSpec(
            name="read_media",
            description=(
                "Read what the user is currently receiving — every canvas's "
                "mounted blocks (with each block's current self-reported "
                "state: what it shows, whether the user has it focused) and "
                "every voice device (with what you've recently said on it). "
                "Use this whenever your next action depends on what the user "
                "is actually looking at, hearing, or has highlighted. Pass "
                "no arguments to read everything; pass block_ids/device_ids "
                "to narrow the response. Each block's state has fields: "
                "kind (e.g. 'pdf', 'snapshot', 'browser'), content (one-line "
                "summary), focus ('active' = user attention here, 'visible', "
                "'background'), extra (block-specific structured data)."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "block_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional. Only return state for these block ids.",
                    },
                    "device_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional. Only return canvases/voices for these device UUIDs.",
                    },
                },
                "additionalProperties": False,
            },
            executor=_make_read_media(user_id),
        ),
        ToolSpec(
            name="list_media",
            description=(
                "DEPRECATED: prefer read_media, which returns the same "
                "inventory plus per-block state. Kept for backward "
                "compatibility. Inventory the user's currently connected "
                "canvases and voice outputs. Takes no arguments."
            ),
            params_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            executor=_make_list_media(user_id),
        ),
        ToolSpec(
            name="mount_template",
            description=(
                "Materialize a known UI template onto the user's canvas. "
                "FAST and DETERMINISTIC — no engineer LLM in the loop. "
                "This is your PRIMARY tool for satisfying user intents that "
                "involve a UI surface. "
                "Available templates: "
                "`upload_file` (PDF picker — use whenever the user wants to "
                "upload, attach, share, or open a PDF/document), "
                "`passage_reader` (textarea for pasting/typing — use when the "
                "user wants to paste, type, or edit text directly), "
                "`pdf_reader` (renders a PDF that was uploaded via "
                "upload_file — auto-mounts via the engineer in most flows; "
                "you only need to call this if the upload happened but the "
                "reader didn't appear), "
                "`inputs_launcher` (the two-button starter — usually the "
                "frontend mounts this on first paint; rarely useful for you "
                "to mount manually). "
                "Pass `replace: [block_id]` to atomically swap out an "
                "existing block (e.g., replace inputs_launcher when you "
                "mount upload_file)."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "description": "Template filename stem (e.g. 'upload_file').",
                    },
                    "replace": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Block ids to unmount in the same batch.",
                    },
                    "target_device_id": {
                        "type": "string",
                        "description": "Optional UUID; mount on this device only.",
                    },
                },
                "required": ["template"],
                "additionalProperties": False,
            },
            executor=_make_mount_template(user_id),
        ),
        ToolSpec(
            name="request_new_block",
            description=(
                "Ask the frontend_engineer to author a NOVEL UI block — one "
                "no template covers. SLOW (engineer LLM authors code). "
                "Prefer `mount_template` when an existing template fits. "
                "Provide a short, concrete description of what the block "
                "should do or display. Optionally target a single device's "
                "canvas via target_device_id (use list_media to discover ids)."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What the new block should do. 1-3 short sentences.",
                    },
                    "target_device_id": {
                        "type": "string",
                        "description": "Optional UUID; mount on this device only. Omit to fan out.",
                    },
                },
                "required": ["description"],
                "additionalProperties": False,
            },
            executor=_make_request_new_block(user_id),
        ),
        ToolSpec(
            name="interactive_graph",
            description=(
                "Render or update the canonical interactive diagram on the "
                "canvas (block id `interactive-graph`). The diagram is "
                "authored in Mermaid syntax — flowcharts, UML (class / "
                "sequence / state / ER / C4), mindmaps, gantt charts, pie "
                "charts, sankey, timeline, xychart (bar/line), kanban, "
                "journey, requirement, gitgraph, and more. Use this for "
                "ANY relational/structural visualization: \"step 1 → step "
                "2 → step 3\", \"class A inherits from B\", \"compare "
                "options as a tree\", etc. FAST and DETERMINISTIC — no "
                "engineer LLM in the loop, the update lands in tens of "
                "milliseconds, perfect for narrating step-by-step while "
                "the diagram grows. "
                "INCREMENTAL EXPLANATION PATTERN: each call REPLACES the "
                "diagram. To grow it alongside your narration, send a "
                "fuller Mermaid string each turn — e.g. first turn just "
                "step 1, next turn step 1 + step 2 + edge between them, "
                "next turn step 1 + 2 + 3, etc. Pair with `speak` so the "
                "diagram and your voice land together. "
                "Use `highlight_node` to flash a specific node id while "
                "you're talking about it (uses Mermaid's node ids — `A`, "
                "`B`, `C` in `flowchart`, the class/actor name in UML). "
                "Use `clear=true` to wipe between unrelated topics. "
                "The block publishes user clicks on `graph.selected` and "
                "parse errors on `graph.error`; both surface via "
                "`read_media` (state.kind='graph', state.extra has "
                "node_ids and selected_node_id)."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "mermaid": {
                        "type": "string",
                        "description": (
                            "Full Mermaid source. Replaces the prior diagram. "
                            "Examples: 'flowchart TD\\n  A[Step 1] --> B[Step 2]'; "
                            "'classDiagram\\nclass User { +String name }'; "
                            "'sequenceDiagram\\nAlice->>Bob: Hi'; "
                            "'xychart-beta\\ntitle \"Q1 sales\"\\nbar [10,20,30]'."
                        ),
                    },
                    "highlight_node": {
                        "type": "string",
                        "description": (
                            "Optional. Node id to flash for ~1.6s after the "
                            "render lands. Use the same id you used in the "
                            "Mermaid source (e.g. 'A', 'Step1')."
                        ),
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "If true, wipe the diagram. Use between unrelated topics.",
                    },
                    "target_device_id": {
                        "type": "string",
                        "description": "Optional UUID; update on this device only.",
                    },
                },
                "additionalProperties": False,
            },
            executor=_make_interactive_graph(user_id),
        ),
        ToolSpec(
            name="push_block_content",
            description=(
                "Publish a value to a topic on a mounted block's bus. The "
                "block must be subscribed to that topic for the update to "
                "land. Use this to drive live content (counters, text "
                "updates, list rows) into an existing block."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "value": {
                        "description": "Any JSON value the block expects on this topic.",
                    },
                    "target_device_id": {"type": "string"},
                },
                "required": ["block_id", "topic"],
                "additionalProperties": False,
            },
            executor=_make_push_block_content(user_id),
        ),
        ToolSpec(
            name="point_arrow",
            description=(
                "Draw an arrow on the canvas pointing from one block to another, "
                "with an optional label. Use to visually link two ideas the user "
                "is comparing or to direct attention from a question to its "
                "answer. Pass both ids empty to clear a previously-drawn arrow."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "from_block_id": {
                        "type": "string",
                        "description": "Source block id (the arrow's tail).",
                    },
                    "to_block_id": {
                        "type": "string",
                        "description": "Target block id (the arrow's head).",
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional short label rendered near the midpoint.",
                    },
                    "target_device_id": {"type": "string"},
                },
                "required": ["from_block_id", "to_block_id"],
                "additionalProperties": False,
            },
            executor=_make_point_arrow(user_id),
        ),
        ToolSpec(
            name="speak",
            description=(
                "Speak text aloud through the user's connected speakers. "
                "Use sparingly — only when the user has agreed to voice output, "
                "or when the visual surface is occupied and audio is the right "
                "channel. Voice / speed / lang default to the user's saved "
                "preferences; override only if the request is explicit."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What to speak. 1-3 sentences works best for live audio.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Optional kokoro voice id (e.g., 'af_heart').",
                    },
                    "speed": {
                        "type": "number",
                        "description": "Optional 0.5-2.0 multiplier on speaking rate.",
                    },
                    "lang": {
                        "type": "string",
                        "description": "Optional language tag (e.g., 'en-us').",
                    },
                    "target_device_id": {"type": "string"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            executor=_make_speak(user_id),
        ),
        ToolSpec(
            name="block_action",
            description=(
                "Invoke a standard handle on an existing block: "
                "'highlight' (flash a glow), 'focus' (move keyboard focus), "
                "or 'scroll_to' (scroll into view). Use to draw the user's "
                "attention to a specific block."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["highlight", "focus", "scroll_to"],
                    },
                    "options": {
                        "type": "object",
                        "description": "Action-specific options (e.g., highlight duration ms).",
                    },
                    "target_device_id": {"type": "string"},
                },
                "required": ["block_id", "action"],
                "additionalProperties": False,
            },
            executor=_make_block_action(user_id),
        ),
    ]


__all__ = ["build_tools"]
