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
import re
from typing import Any, Dict, List, Literal
from uuid import UUID


# Lane tags for tool filtering. See `build_tools(lane=...)`.
#   "answer"      — full toolset, used by /api/ask (typed Q&A).
#   "user_facing" — Lane A reflect: the teacher is replying to the user via
#                   speech. Only `speak` is exposed; structural / slow tools
#                   (read_media, mount_template, engineer delegation) are
#                   hidden so the LLM cannot waste the single allowed
#                   iteration on something that should be a Lane B task.
#   "background"  — Lane B work: everything except `speak`. The background
#                   pool does NOT talk to the user — its results surface
#                   via the notice queue that Lane A drains next turn.
Lane = Literal["answer", "user_facing", "background"]

from infra.contracts.ui import BlockSpec
from infra.model.tools import ToolSpec

from tools.read_document import read_document
from tools.speak import speak
from workshop.canvas.tools.block_action import block_action
from workshop.canvas.tools.interactive_graph import interactive_graph
from workshop.canvas.tools.layout_blocks import layout_blocks
from workshop.canvas.tools.list_media import list_media
from workshop.canvas.tools.mount_template import mount_template
from workshop.canvas.tools.point_arrow import point_arrow
from workshop.canvas.tools.push_block_content import push_block_content
from workshop.canvas.tools.read_media import read_media
from workshop.canvas.tools.request_ui_block import request_ui_block


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


# Tripwire: descriptions matching any of these patterns are diagram-shaped
# requests and must go through `interactive_graph`, not the engineer LLM.
# We catch this server-side so the teacher gets immediate, deterministic
# feedback even if its prompt-side discipline slips.
_DIAGRAM_HINTS = re.compile(
    r"\b(flow ?chart|flow diagram|sequence diagram|class diagram|"
    r"er diagram|state diagram|"
    r"mind ?map|gantt|sankey|timeline|"
    r"step\s*\d|step[s]?\s*->|->\s*step|"
    r"hierarchy|tree of|relation(ship)?s? between|"
    r"diagram (of|showing|for)|chart (of|showing))\b",
    re.IGNORECASE,
)
# A separate check: arrow chains in the description are almost always a flow.
_ARROW_CHAIN = re.compile(r"(->|→|=>|-->).*?(->|→|=>|-->)")


def _make_request_new_block(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        description = (args.get("description") or "").strip()
        if not description:
            return json.dumps({"error": "description is required"})

        # Diagram-shaped requests must go through interactive_graph. The
        # engineer must never end up authoring per-step JS for a flow,
        # sequence, hierarchy, etc. — that's content, not code, and per-
        # step JS does not belong in the user's git workspace.
        if _DIAGRAM_HINTS.search(description) or _ARROW_CHAIN.search(description):
            return json.dumps({
                "error": (
                    "diagram-shaped request — use interactive_graph(name='...', "
                    "mermaid='flowchart LR ...') instead. request_new_block is "
                    "for novel interactive widgets only (sliders, simulations, "
                    "custom inputs); diagrams are content rendered by the "
                    "ephemeral interactive_graph surface, not code in the "
                    "user's workspace."
                )
            })

        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        spec = BlockSpec(description=description)
        try:
            blocks = await request_ui_block(spec, user_id, target_device_id=target_uuid)
        except ValueError as e:
            # Sandbox validation rejected the engineer's output. Surface
            # the message so the teacher's LLM can refine the description
            # (or the engineer agent rewrites on the next call).
            return json.dumps({"error": str(e)})
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
        name_raw = args.get("name")
        if name_raw is not None and not isinstance(name_raw, str):
            return json.dumps({"error": "name must be a string"})
        name = (name_raw or "main").strip() or "main"

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
            name=name,
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
        channel = (args.get("channel") or "").strip()
        if channel not in ("voice", "text", "both"):
            return json.dumps({
                "error": "channel must be 'voice', 'text', or 'both'"
            })
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        try:
            delivered = await speak(
                user_id=user_id,
                text=text,
                channel=channel,
                voice=args.get("voice"),
                speed=args.get("speed"),
                lang=args.get("lang"),
                target_device_id=target_uuid,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(delivered)
    return executor


def _make_read_document(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        action = (args.get("action") or "").strip()
        if not action:
            return json.dumps({"error": "action is required"})
        document_id_raw = args.get("document_id")
        document_id = None
        if document_id_raw:
            try:
                document_id = UUID(document_id_raw)
            except (ValueError, TypeError):
                return json.dumps({"error": "invalid document_id"})
        page = args.get("page")
        query = args.get("query")
        top_k_raw = args.get("top_k")
        try:
            top_k = int(top_k_raw) if top_k_raw is not None else 5
        except (TypeError, ValueError):
            return json.dumps({"error": "top_k must be an integer"})
        result = await read_document(
            user_id=user_id,
            action=action,
            document_id=document_id,
            page=page,
            query=query if isinstance(query, str) else None,
            top_k=max(1, min(20, top_k)),
        )
        return json.dumps(result)
    return executor


def _make_layout_blocks(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        layouts = args.get("layouts")
        if not isinstance(layouts, list) or not layouts:
            return json.dumps({"error": "layouts must be a non-empty list"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        device_class = args.get("device_class")
        if device_class is not None and device_class not in ("phone", "tablet", "desktop"):
            return json.dumps({"error": "device_class must be 'phone', 'tablet', or 'desktop'"})
        try:
            result = await layout_blocks(
                user_id=user_id,
                layouts=layouts,
                target_device_id=target_uuid,
                device_class=device_class,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result)
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


# Tool-name → set of lanes it appears on. Anything not listed defaults to
# the full set. Keep this map narrow — adding a tool to a wrong lane can
# cause Lane A to spend its single iteration on a structural call.
_TOOL_LANES: Dict[str, set[Lane]] = {
    # Lane A says one thing and stops. `speak` is the verb. Lane B never
    # talks to the user directly — its results surface via the notice
    # queue that Lane A drains next turn.
    "speak":              {"answer", "user_facing"},
    # Lane B does structural / slow work. Hide from Lane A entirely so
    # the single allowed iteration isn't spent on a structural call.
    "read_media":         {"answer", "background"},
    "read_document":      {"answer", "background"},
    "list_media":         {"answer", "background"},
    "mount_template":     {"answer", "background"},
    "request_new_block":  {"answer", "background"},
    "push_block_content": {"answer", "background"},
    "interactive_graph":  {"answer", "background"},
    "point_arrow":        {"answer", "background"},
    "layout_blocks":      {"answer", "background"},
    "block_action":       {"answer", "background"},
}


def build_tools(user_id: UUID, lane: Lane = "answer") -> List[ToolSpec]:
    """Return the per-request tool list for the teacher.

    Each call gets a fresh list with executors bound to this user_id. The
    LLM cannot supply a different user_id — that's enforced by closure.

    `lane` filters the toolset:
    - "answer" (default): full set, for /api/ask (typed Q&A).
    - "user_facing": Lane A reflect — only `speak`.
    - "background": Lane B work — everything except `speak`.
    """
    full = [
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
            name="read_document",
            description=(
                "Actively read content from a PDF that's loaded in the "
                "user's pdf_reader. Three actions: "
                "(1) `action='outline'` returns the document's table of "
                "contents + page count — call this first when the user "
                "asks about a paper to learn its structure; "
                "(2) `action='page', page=N` returns the full text of "
                "page N (1-indexed) — use to read the abstract on page 1, "
                "the methods on whatever page they're on, etc; "
                "(3) `action='query', query='...'` runs a vector search "
                "across the document's chunks — use to find a specific "
                "concept (e.g. query='positional encoding'). Returned "
                "chunks include their page_number so you can cite. "
                "`document_id` is optional — when omitted, the tool resolves "
                "to whichever PDF is currently on canvas (error if 0 or "
                "2+ PDFs). Worked examples: "
                "`read_document(action='outline')` to map the paper; "
                "`read_document(action='page', page=1)` for the abstract; "
                "`read_document(action='query', query='self-attention')` "
                "to find that section."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["outline", "page", "query"],
                    },
                    "document_id": {
                        "type": "string",
                        "description": (
                            "Optional. Doc UUID. Defaults to the single PDF "
                            "currently on canvas."
                        ),
                    },
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-indexed page number. Required when action='page'.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search phrase. Required when action='query'.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of chunks to return for action='query'. Default 5.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            executor=_make_read_document(user_id),
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
                "Display a known reading surface — `upload_file` (PDF "
                "picker), `passage_reader` (paste/type text), `pdf_reader` "
                "(rendered PDF), `inputs_launcher` (two-button starter, "
                "auto-mounted on empty canvas — rarely needed manually). "
                "Use when the user wants to upload, paste, or read. Fast "
                "and deterministic. Pass `replace: [...]` to atomically "
                "swap out an existing surface (e.g. replace the launcher "
                "when you bring up the upload picker)."
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
                "Use **only** for novel *interactive widgets* — sliders, "
                "custom inputs, simulations, anything that needs fresh "
                "JavaScript. **DO NOT** use for diagrams (flows, "
                "sequences, charts, hierarchies, classes, mind maps, "
                "timelines): those go to `interactive_graph`. **DO NOT** "
                "use to display text or a passage: those go to "
                "`mount_template`. The engineer LLM authors code here — "
                "slow, and only justified for genuinely novel UI."
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
                "Draw or update a diagram on the canvas — flowcharts, "
                "sequence diagrams, classes (UML), mindmaps, charts "
                "(bar / line / pie), gantt, sankey, timelines, ER, "
                "state machines, and more. Each diagram has a `name` you "
                "choose (e.g. \"steps\", \"protocol\"). Pass the SAME "
                "name to update an existing diagram in place; pass a "
                "DIFFERENT name to add a second diagram alongside. "
                "Diagrams are written in Mermaid syntax — concise text. "
                "The CURRENTLY ON CANVAS section in your prompt tells "
                "you which diagrams are already up. Diagrams are "
                "EPHEMERAL: they appear, illustrate the concept, and "
                "disappear when the user reloads. Don't worry about "
                "saving them. Pair with `speak` to narrate while the "
                "diagram grows; use `highlight_node` to flash a node "
                "while you're talking about it; use `clear=true` to "
                "wipe a diagram. For step-by-step explanation, send a "
                "fuller Mermaid each turn under the same name and the "
                "diagram grows with your words."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Semantic name for the diagram instance — "
                            "kebab-case (e.g. \"steps\", \"tls-handshake\", "
                            "\"krebs-cycle\"). Same name = update existing; "
                            "different name = add alongside. Defaults to "
                            "\"main\" if omitted."
                        ),
                    },
                    "mermaid": {
                        "type": "string",
                        "description": (
                            "Full Mermaid source. Replaces the prior content "
                            "of the named diagram. Examples: "
                            "'flowchart LR\\n  A[Step 1] --> B[Step 2]'; "
                            "'classDiagram\\nclass User { +String name }'; "
                            "'sequenceDiagram\\nAlice->>Bob: Hi'; "
                            "'xychart-beta\\ntitle \"Q1 sales\"\\nbar [10,20,30]'."
                        ),
                    },
                    "highlight_node": {
                        "type": "string",
                        "description": (
                            "Optional. Node id to flash for ~1.6s. Use the "
                            "same id you used in the Mermaid source "
                            "(e.g. 'A', 'Step1', or a class/actor name)."
                        ),
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "If true, wipe the named diagram.",
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
                "Send a value into a topic that an existing surface "
                "listens on. Use to drive live data (counters, list rows, "
                "text updates) into something already up — no remount."
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
                "Deliver an utterance to the user via voice (Kokoro audio), "
                "an on-screen caption (a borderless, always-on-top floating "
                "strip near the bottom of the screen, like YouTube CC, that "
                "reveals left-to-right at reading speed and auto-fades), or "
                "both. Pick `channel` based on TALK PREFERENCE in the "
                "system context plus the active device class (see "
                "CURRENTLY ON CANVAS). Voice / speed / lang default to the "
                "user's saved preferences; override only if the user is "
                "explicit."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What to say. 1-3 sentences works best for both audio latency and an on-screen line that is readable at a glance.",
                    },
                    "channel": {
                        "type": "string",
                        "enum": ["voice", "text", "both"],
                        "description": "How to deliver this utterance. 'voice' plays audio only; 'text' shows it in the teacher-speech block only; 'both' does both.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Optional kokoro voice id (e.g., 'af_heart'). Only used when channel includes voice.",
                    },
                    "speed": {
                        "type": "number",
                        "description": "Optional 0.5-2.0 multiplier on speaking rate. Only used when channel includes voice.",
                    },
                    "lang": {
                        "type": "string",
                        "description": "Optional language tag (e.g., 'en-us'). Only used when channel includes voice.",
                    },
                    "target_device_id": {"type": "string"},
                },
                "required": ["text", "channel"],
                "additionalProperties": False,
            },
            executor=_make_speak(user_id),
        ),
        ToolSpec(
            name="layout_blocks",
            description=(
                "Resize and reposition blocks on the canvas to fill empty "
                "space or arrange blocks side-by-side. The canvas is a "
                "Bootstrap-style grid whose width depends on the device: "
                "12 cols on desktop, 8 cols on tablet, 4 cols on phone. "
                "Rows are always 9. Pass an array of layouts "
                "`[{block_id, x, y, w, h}, ...]` and every listed block "
                "reflows in place — no remount, no reload, PDFs stay on "
                "the same page. Read the `(at x:.. y:.. w:.. h:..)` "
                "annotations in CURRENTLY ON CANVAS to know each block's "
                "starting position. Common layouts on DESKTOP (12×9): "
                "full-bleed `{x:0,y:0,w:12,h:9}`; left-half "
                "`{x:0,y:0,w:6,h:9}`; right-half `{x:6,y:0,w:6,h:9}`; "
                "top-third `{x:0,y:0,w:12,h:3}`; bottom two-thirds "
                "`{x:0,y:3,w:12,h:6}`; thirds "
                "`{x:0,y:0,w:4,h:9} | {x:4,y:0,w:4,h:9} | {x:8,y:0,w:4,h:9}`. "
                "On TABLET (8×9) halve the desktop col counts; on PHONE "
                "(4×9) quarter them. Pass `device_class` to validate "
                "against the right grid — when omitted, validation uses "
                "the desktop bounds. Use this tool when a block is "
                "leaving empty space, the user wants two surfaces "
                "side-by-side, or the user explicitly asks to make "
                "something bigger or smaller."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "layouts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "block_id": {"type": "string"},
                                "x": {"type": "integer", "minimum": 0, "maximum": 11},
                                "y": {"type": "integer", "minimum": 0, "maximum": 8},
                                "w": {"type": "integer", "minimum": 1, "maximum": 12},
                                "h": {"type": "integer", "minimum": 1, "maximum": 9},
                            },
                            "required": ["block_id", "x", "y", "w", "h"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                    },
                    "target_device_id": {
                        "type": "string",
                        "description": "Optional UUID; reflow on this device only.",
                    },
                    "device_class": {
                        "type": "string",
                        "enum": ["phone", "tablet", "desktop"],
                        "description": (
                            "Which grid the layouts target. Read the "
                            "device_class of the canvas in CURRENTLY ON "
                            "CANVAS and pass it here. Omit to validate "
                            "against the desktop grid (12×9)."
                        ),
                    },
                },
                "required": ["layouts"],
                "additionalProperties": False,
            },
            executor=_make_layout_blocks(user_id),
        ),
        ToolSpec(
            name="block_action",
            description=(
                "Draw the user's attention to a surface already on the "
                "canvas. Actions: "
                "'highlight' (flash a glow), "
                "'focus' (move keyboard focus), "
                "'scroll_to' (scroll into view), "
                "'raise' (bring the surface to the front of the stack — "
                "use when one block is hidden behind another, e.g. you "
                "drew a diagram while a PDF was open and the user "
                "asks to see the PDF again, or you want to put a "
                "particular surface in front for emphasis). Newly-"
                "mounted surfaces are auto-raised, so you only need "
                "'raise' to flip the user back to a previously-mounted "
                "surface. Use the block_id you see in CURRENTLY ON "
                "CANVAS."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["highlight", "focus", "scroll_to", "raise"],
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
    # Filter by lane. Any tool not listed in _TOOL_LANES is treated as
    # available everywhere (forward-compat for tools added without
    # remembering to update the map).
    return [t for t in full if lane in _TOOL_LANES.get(t.name, {"answer", "user_facing", "background"})]


__all__ = ["build_tools"]
