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
#                   speech. Only `speak` + fast structural tools + `start_research`
#                   are exposed; slow IO and the planning tools are hidden
#                   so Lane A's small iteration budget isn't wasted on
#                   investigation work that belongs in Lane R.
#   "background"  — Lane B work: structural follow-ups after a block
#                   completes. Does NOT talk to the user — its results
#                   surface via the notice queue.
#   "research"    — Lane R: a long-running multi-step investigation
#                   spawned by `start_research`. Has the full browser
#                   toolkit, the planning tools (research_plan /
#                   research_note), `speak` for the final synthesis,
#                   and the structural tools so it can mount the
#                   progress ribbon and any diagrams it produces. ~25
#                   iterations, ~90 s wall clock, larger token budget.
#   "writer"      — Voice-leads canvas writer: the second pass of a
#                   voice turn, runs after the spoken answer is done.
#                   Only `mount_template` is exposed — its single job
#                   is to render a note derived from the
#                   transcript.
Lane = Literal["answer", "user_facing", "background", "research", "writer"]

from infra.contracts.ui import BlockSpec
from infra.model.tools import ToolSpec

from tools.browser_set import browser_set
from tools.look_at_image import look_at_image
from tools.look_at_video import look_at_video
from tools.read_document import read_document
from tools.read_url import read_url
from tools.search_notes import search_notes
from tools.speak import speak
from tools.web_view import web_view
from workshop.canvas.tools.block_action import block_action
from workshop.canvas.tools.edit_note import edit_note
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
        # Pretty-print incoming args so we can see exactly what the LLM
        # emitted when a mount appears to "succeed" but no block lands.
        # Tee to the perception trace file so it shows up alongside the
        # llm-response lines (backend stdout is hard to capture).
        try:
            _arg_preview = json.dumps(args, default=str)[:600]
        except Exception:
            _arg_preview = repr(args)[:600]
        _trace_line = f"[mount_template/exec] args={_arg_preview}"
        print(_trace_line, flush=True)
        try:
            import time as _t
            with open("/tmp/bewithme-perception-trace.log", "a") as _f:
                _f.write(f"{_t.strftime('%H:%M:%S')} {_trace_line}\n")
        except Exception:
            pass

        def _trace(msg: str) -> None:
            print(msg, flush=True)
            try:
                import time as _t
                with open("/tmp/bewithme-perception-trace.log", "a") as _f:
                    _f.write(f"{_t.strftime('%H:%M:%S')} {msg}\n")
            except Exception:
                pass

        # `_raw_arguments` is the LLM provider's fallback shape when the
        # tool-arg stream didn't get parsed into structured fields. Some
        # providers (DeepSeek's tool channel observed in 2026-05) emit a
        # COMPLETE valid JSON object inside this string even on successful
        # calls. Try to recover: if it parses to a dict, treat it as the
        # real args. Only bail when it's truly unparseable (truncation).
        if "_raw_arguments" in args:
            raw = args["_raw_arguments"]
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        _trace("[mount_template/exec] recovered args from _raw_arguments JSON string")
                        args = parsed
                    else:
                        _trace("[mount_template/exec] BAILING: _raw_arguments parsed to non-dict")
                        return json.dumps({"error": "tool arguments were truncated mid-stream — retry with shorter content"})
                except json.JSONDecodeError:
                    _trace("[mount_template/exec] BAILING: _raw_arguments not valid JSON (truncated)")
                    return json.dumps({"error": "tool arguments were truncated mid-stream — retry with shorter content"})
            else:
                _trace("[mount_template/exec] BAILING: _raw_arguments not a string")
                return json.dumps({"error": "tool arguments were truncated mid-stream — retry with shorter content"})

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
        # If the persona didn't pick a target device but the request carries
        # an X-Output-Device-Id (e.g. phone routing answers to desktop), use
        # it as the default. Persona's explicit choice still wins.
        if target_uuid is None:
            from infra.contracts.output_routing import get_output_device_id
            ctx_target = get_output_device_id()
            if ctx_target is not None:
                target_uuid = ctx_target
                _trace(f"[mount_template/exec] using ctx target_device_id={ctx_target}")

        # Be lenient about `params`: some LLM providers emit nested
        # objects as JSON-encoded strings instead of structured objects.
        # Accept either; only reject genuinely-broken shapes.
        params = args.get("params")
        if isinstance(params, str):
            try:
                params = json.loads(params)
                _trace("[mount_template/exec] params arrived as string; decoded to dict")
            except json.JSONDecodeError:
                return json.dumps({"error": "params was a string but not valid JSON"})
        if params is not None and not isinstance(params, dict):
            return json.dumps({"error": f"params must be an object, got {type(params).__name__}"})

        # `slug` is the canonical name for note templates — it doubles as
        # the canvas block_id, the on-disk filename, and the note_id in
        # `note_chunks`. For other templates, `slug` is ignored (their
        # block_id stays the template's id_default).
        raw_slug = args.get("slug")
        slug = raw_slug.strip() if isinstance(raw_slug, str) else None
        try:
            result = await mount_template(
                user_id=user_id,
                template_name=template_name,
                block_id=slug or None,
                replace=replace,
                target_device_id=target_uuid,
                params=params,
            )
        except FileNotFoundError:
            _trace(f"[mount_template/exec] unknown template {template_name!r}")
            return json.dumps({
                "error": f"unknown template {template_name!r}",
            })
        except ValueError as e:
            _trace(f"[mount_template/exec] ValueError: {e}")
            return json.dumps({"error": str(e)})
        _trace(f"[mount_template/exec] OK bid={result.block_id} template={result.template} deleted={result.deleted}")
        return json.dumps({
            "block_id": result.block_id,
            "template": result.template,
            "deleted": result.deleted,
        })
    return executor


def _make_edit_note(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        # `_raw_arguments` fallback: same shape as mount_template — some
        # providers wrap a complete JSON object inside this string. Recover
        # if it parses to a dict; bail only on truly unparseable truncation.
        if "_raw_arguments" in args:
            raw = args["_raw_arguments"]
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        args = parsed
                    else:
                        return json.dumps({"error": "tool arguments were truncated mid-stream — retry with fewer or shorter ops"})
                except json.JSONDecodeError:
                    return json.dumps({"error": "tool arguments were truncated mid-stream — retry with fewer or shorter ops"})
            else:
                return json.dumps({"error": "tool arguments were truncated mid-stream — retry with fewer or shorter ops"})

        block_id = (args.get("block_id") or "").strip()
        if not block_id:
            return json.dumps({"error": "block_id is required"})

        ops = args.get("ops")
        if isinstance(ops, str):
            # Some providers emit nested arrays as JSON strings. Accept.
            try:
                ops = json.loads(ops)
            except json.JSONDecodeError:
                return json.dumps({"error": "ops was a string but not valid JSON"})
        if not isinstance(ops, list):
            return json.dumps({
                "error": f"ops must be a list, got {type(ops).__name__}"
            })

        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        if target_uuid is None:
            from infra.contracts.output_routing import get_output_device_id
            ctx_target = get_output_device_id()
            if ctx_target is not None:
                target_uuid = ctx_target

        import time as _t_mod
        t0 = _t_mod.perf_counter()
        try:
            result = await edit_note(
                user_id=user_id,
                block_id=block_id,
                ops=ops,
                target_device_id=target_uuid,
            )
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        wall_ms = round((_t_mod.perf_counter() - t0) * 1000, 2)

        # Log even on error so we can see misses too.
        from infra.event_log import log_event
        log_event(
            "ask.edit_note",
            user_id=str(user_id),
            block_id=block_id,
            wall_ms=wall_ms,
            ops_count=len(ops) if isinstance(ops, list) else 0,
            op_names=(result.get("op_names") if isinstance(result, dict) else None),
            error=(result.get("error") if isinstance(result, dict) else None),
        )
        return json.dumps(result)
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


def _make_read_url(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        url = (args.get("url") or "").strip()
        if not url:
            return json.dumps({"error": "url is required"})
        try:
            result = await read_url(user_id=user_id, url=url)
        except Exception as e:
            return json.dumps({"error": f"read_url failed: {e}"})
        return json.dumps(result)
    return executor


def _make_browser_set(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        action = (args.get("action") or "").strip().lower()
        if not action:
            return json.dumps({"error": "action is required"})
        # Numeric coercion for fields that may arrive as strings
        timeout = args.get("timeout")
        delay = args.get("delay")
        x = args.get("x")
        y = args.get("y")
        try:
            timeout = int(timeout) if timeout is not None else None
            delay = int(delay) if delay is not None else None
            x = int(x) if x is not None else None
            y = int(y) if y is not None else None
        except (TypeError, ValueError):
            return json.dumps({"error": "timeout/delay/x/y must be integers"})

        try:
            result = await browser_set(
                user_id=user_id,
                action=action,
                url=args.get("url") if isinstance(args.get("url"), str) else None,
                selector=args.get("selector") if isinstance(args.get("selector"), str) else None,
                value=args.get("value") if isinstance(args.get("value"), str) else None,
                text=args.get("text") if isinstance(args.get("text"), str) else None,
                key=args.get("key") if isinstance(args.get("key"), str) else None,
                expression=args.get("expression") if isinstance(args.get("expression"), str) else None,
                state=args.get("state") if isinstance(args.get("state"), str) else None,
                wait_until=args.get("wait_until") if isinstance(args.get("wait_until"), str) else None,
                timeout=timeout,
                delay=delay,
                full_page=bool(args.get("full_page") or False),
                drain=bool(args.get("drain", True)),
                x=x,
                y=y,
            )
        except Exception as e:
            return json.dumps({"error": f"browser_set failed: {e}"})
        # Snapshot results carry a ref→locator map that doesn't serialize;
        # strip the locator-internal field so we send a clean payload back.
        # (the sidecar already does this, but be defensive.)
        if isinstance(result, dict) and isinstance(result.get("refs"), list):
            for r in result["refs"]:
                if isinstance(r, dict):
                    r.pop("locator", None)
        return json.dumps(result)
    return executor


def _make_web_view(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        action = (args.get("action") or "").strip().lower()
        if not action:
            return json.dumps({"error": "action is required"})
        url = args.get("url")
        selector = args.get("selector")
        text = args.get("text")
        direction = args.get("direction") or "down"
        amount_raw = args.get("amount")
        try:
            amount = int(amount_raw) if amount_raw is not None else 400
        except (TypeError, ValueError):
            return json.dumps({"error": "amount must be an integer"})
        timeout_raw = args.get("timeout_ms")
        try:
            timeout_ms = int(timeout_raw) if timeout_raw is not None else 5000
        except (TypeError, ValueError):
            return json.dumps({"error": "timeout_ms must be an integer"})
        include_screenshot = bool(args.get("include_screenshot") or False)
        x = args.get("x")
        y = args.get("y")
        try:
            x = int(x) if x is not None else None
            y = int(y) if y is not None else None
        except (TypeError, ValueError):
            return json.dumps({"error": "x and y must be integers"})
        try:
            result = await web_view(
                user_id=user_id,
                action=action,
                url=url if isinstance(url, str) else None,
                selector=selector if isinstance(selector, str) else None,
                text=text if isinstance(text, str) else None,
                direction=direction,
                amount=amount,
                timeout_ms=timeout_ms,
                include_screenshot=include_screenshot,
                x=x,
                y=y,
            )
        except Exception as e:
            return json.dumps({"error": f"web_view call failed: {e}"})
        return json.dumps(result)
    return executor


def _make_look_at_image(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        image = (args.get("image") or "").strip()
        if not image:
            return json.dumps({"error": "image is required"})
        question_raw = args.get("question")
        question = (
            question_raw.strip()
            if isinstance(question_raw, str) and question_raw.strip()
            else None
        )
        try:
            result = await look_at_image(image, question)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"vision call failed: {e}"})
        return json.dumps(result)
    return executor


def _make_look_at_video(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        video = (args.get("video") or "").strip()
        if not video:
            return json.dumps({"error": "video is required"})
        question_raw = args.get("question")
        question = (
            question_raw.strip()
            if isinstance(question_raw, str) and question_raw.strip()
            else None
        )
        max_frames_raw = args.get("max_frames")
        try:
            max_frames = int(max_frames_raw) if max_frames_raw is not None else 24
        except (TypeError, ValueError):
            return json.dumps({"error": "max_frames must be an integer"})
        max_frames = max(1, min(max_frames, 64))
        try:
            result = await look_at_video(video, question, max_frames=max_frames)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"video call failed: {e}"})
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
        # Cross-device output routing: default to request's X-Output-Device-Id
        # when persona didn't pick a target.
        if target_uuid is None:
            from infra.contracts.output_routing import get_output_device_id
            ctx_target = get_output_device_id()
            if ctx_target is not None:
                target_uuid = ctx_target
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


def _make_search_notes(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return json.dumps({"error": "query is required"})
        top_k_raw = args.get("top_k")
        try:
            top_k = int(top_k_raw) if top_k_raw is not None else 5
        except (TypeError, ValueError):
            return json.dumps({"error": "top_k must be an integer"})
        result = await search_notes(
            user_id=user_id,
            query=query,
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


# ---- Research-mode tools -------------------------------------------------
#
# `start_research` is the gate: Lane A calls it when the user asks an
# open-ended question that needs multi-step investigation. The executor
# spawns a Lane R turn via `_execute_research` and returns immediately,
# so Lane A doesn't block on the long-running work.
#
# `research_plan` and `research_note` are the planning scaffold the
# Lane R LLM uses inside the research loop. Both update the in-memory
# `research_state` and push the new state to the canvas's progress
# ribbon via the workshop's push_block_content.

async def _push_research_state_to_canvas(user_id: UUID) -> None:
    """Mount the progress ribbon if it's not yet up, then push the
    latest state to it. Failures are non-fatal — the LLM keeps making
    progress even if the user's canvas is offline."""
    from persona.teacher import research_state
    state = research_state.get(user_id)
    if state is None:
        return
    block_id = state.block_id
    payload = state.to_payload()

    # Mount once. If the block already exists the engineer-side mount
    # would error; we ignore that and proceed straight to push so the
    # block updates regardless of which path created it.
    try:
        await mount_template(
            user_id=user_id,
            template_name="research_progress",
            block_id=block_id,
        )
    except Exception:
        pass

    try:
        await push_block_content(
            user_id=user_id,
            block_id=block_id,
            topic=f"text.{block_id}.content",
            value=payload,
        )
    except Exception as e:
        print(f"[research] push_block_content failed: {e}", flush=True)


def _make_research_plan(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        from persona.teacher import research_state
        steps = args.get("steps")
        if not isinstance(steps, list) or not steps:
            return json.dumps({"error": "steps must be a non-empty list of strings"})
        cleaned = [str(s).strip() for s in steps if str(s).strip()]
        if not cleaned:
            return json.dumps({"error": "steps must contain at least one non-empty step"})
        if len(cleaned) > 7:
            return json.dumps({"error": "max 7 steps; narrow the plan"})
        if len(cleaned) < 3:
            return json.dumps({
                "error": (
                    "min 3 steps. If you cannot enumerate 3 steps, this is "
                    "not a research question — call speak with a normal "
                    "answer instead."
                ),
            })
        # If begin() hasn't been called yet (defensive — the trigger
        # already calls it), do it now so the plan tool always works.
        if research_state.get(user_id) is None:
            research_state.begin(user_id, goal="")
        state = research_state.set_plan(user_id, cleaned)
        if state is None:
            return json.dumps({"error": "no active research state"})
        await _push_research_state_to_canvas(user_id)
        return json.dumps(state.to_llm_view())
    return executor


def _make_research_note(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        from persona.teacher import research_state
        idx_raw = args.get("step_index")
        finding = args.get("finding")
        if not isinstance(finding, str) or not finding.strip():
            return json.dumps({"error": "finding is required (non-empty string)"})
        try:
            step_index = int(idx_raw) if idx_raw is not None else -1
        except (TypeError, ValueError):
            return json.dumps({"error": "step_index must be an integer"})
        is_error = bool(args.get("error") or False)
        state = research_state.record_note(
            user_id, step_index, finding.strip(), error=is_error
        )
        if state is None:
            return json.dumps({"error": "no active research state — call research_plan first"})
        if step_index < 0 or step_index >= len(state.steps):
            return json.dumps({"error": f"step_index {step_index} out of range"})
        await _push_research_state_to_canvas(user_id)
        return json.dumps(state.to_llm_view())
    return executor


def _make_start_research(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        from persona.teacher import research_state
        # Late imports — triggers.py imports manifest.py, so direct imports
        # at module load would cycle.
        from persona.teacher.triggers import (
            _execute_research,
            _execute_research_from_recipe,
        )
        from workshop.research import recipes as _recipes
        from workshop.research import recipe_store as _recipe_store
        import asyncio as _asyncio

        goal = (args.get("goal") or "").strip()
        if not goal:
            return json.dumps({"error": "goal is required"})
        if research_state.is_active(user_id):
            return json.dumps({
                "status": "already_running",
                "message": "a research turn is already in flight for this user",
            })

        # URL resolution: LLM-provided `page_url` wins; canvas autodetect
        # as fallback. Used to derive the host for recipe lookup; we keep
        # `goal_url` available to pass into the replay path.
        goal_url = (args.get("page_url") or "").strip() or None
        if not goal_url:
            try:
                goal_url = await _recipes.infer_url_from_canvas(user_id)
            except Exception as e:
                print(f"[start_research] infer_url_from_canvas failed: {e}", flush=True)
                goal_url = None

        # Recipe lookup: per-user, same host, cosine sim ≥ 0.85. Failures
        # along the way degrade silently to the fresh research path.
        match = None
        host = _recipes.host_from_url(goal_url) if goal_url else None
        if host:
            try:
                from infra.rag.embedding import embed_text
                emb = await embed_text(goal)
                if emb:
                    match = await _recipe_store.lookup(
                        user_id, host=host, goal_embedding=emb,
                    )
            except Exception as e:
                print(f"[start_research] recipe lookup failed: {e}", flush=True)

        # Initialize state up front so the ribbon mounts with the goal
        # before the loop's first iteration adds steps.
        research_state.begin(user_id, goal=goal)
        await _push_research_state_to_canvas(user_id)

        # Fire-and-forget dispatch. Both paths own their own lifecycle.
        if match is not None and goal_url:
            print(
                f"[start_research] replay hit: recipe={match.id} "
                f"host={host} goal={goal[:60]!r}",
                flush=True,
            )
            _asyncio.create_task(
                _execute_research_from_recipe(user_id, goal, goal_url, match)
            )
            return json.dumps({"status": "started", "goal": goal, "via": "recipe"})

        # Pass goal_url through so the research prompt can pull in the
        # per-host navigation note (workshop/research/per_host_skills).
        _asyncio.create_task(_execute_research(user_id, goal, goal_url))
        return json.dumps({"status": "started", "goal": goal, "via": "fresh"})
    return executor


# Tool-name → set of lanes it appears on. Anything not listed defaults to
# the full set. Keep this map narrow — adding a tool to a wrong lane can
# cause Lane A to spend its single iteration on a structural call.
_TOOL_LANES: Dict[str, set[Lane]] = {
    # Lane A talks to the user AND can perform fast structural actions
    # (mount/unmount blocks, scroll, push content) so a request like
    # "open the uploader" actually mounts the widget instead of just
    # claiming it did. The defining rule: tools that are pure SSE
    # fan-outs (no extra LLM call, complete in ms) stay on Lane A;
    # tools that themselves invoke the LLM, do RAG, or duplicate
    # context that's already in the prompt go to Lane B only.
    "speak":              {"answer", "user_facing", "research"},
    "mount_template":     {"answer", "user_facing", "background", "research", "writer"},
    "edit_note":     {"answer", "user_facing", "background", "research", "writer"},
    "block_action":       {"answer", "user_facing", "background", "research"},
    "push_block_content": {"answer", "user_facing", "background", "research"},
    "point_arrow":        {"answer", "user_facing", "background", "research"},
    "layout_blocks":      {"answer", "user_facing", "background", "research"},
    "interactive_graph":  {"answer", "user_facing", "background", "research"},
    # Slow / redundant — Lane A would burn its single iteration here.
    # Lane R needs all of these — that's the point of research mode.
    "read_media":         {"answer", "background", "research"},   # canvas state already in prompt
    "read_document":      {"answer", "background", "research"},   # vector RAG, slow
    # search_notes IS exposed to user_facing (Lane A) even though it's vector
    # RAG: the whole point is voice-driven recall ("remind me what we covered…").
    # ~50–100ms is acceptable inside Lane A's budget when the LLM actually
    # decides to call it; the LLM only invokes it when relevant.
    "search_notes":       {"answer", "user_facing", "background", "research"},
    "list_media":         {"answer", "background", "research"},   # deprecated
    "request_new_block":  {"answer", "background"},               # engineer LLM, too slow for Lane R
    "look_at_image":      {"answer", "background", "research"},   # remote vision call, ~5–6s
    "look_at_video":      {"answer", "background", "research"},   # ffmpeg + N vision calls + Whisper; slow
    "web_view":           {"answer", "background", "research"},   # drives Electron BrowserView, slow IO
    "read_url":           {"answer", "background", "research"},   # silent Playwright fetch, ~3–5s
    "browser_set":        {"answer", "background", "research"},   # full headless Playwright; slow IO
    # Research-mode entry + scaffold.
    "start_research":     {"answer", "user_facing"},              # Lane A spawns Lane R
    "research_plan":      {"research"},                            # only inside the research loop
    "research_note":      {"research"},                            # only inside the research loop
}


def build_tools(user_id: UUID, lane: Lane = "answer") -> List[ToolSpec]:
    """Return the per-request tool list for the teacher.

    Each call gets a fresh list with executors bound to this user_id. The
    LLM cannot supply a different user_id — that's enforced by closure.

    `lane` filters the toolset:
    - "answer" (default): full set, for /api/ask (typed Q&A).
    - "user_facing": Lane A reflect — only `speak`.
    - "background": Lane B work — everything except `speak`.
    - "writer": voice-leads canvas writer — only `mount_template`.
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
            name="search_notes",
            description=(
                "Recall passages from notes YOU previously authored with "
                "this user. When the user mentions a topic you've taught "
                "before — even loosely — call this to surface the relevant "
                "note(s) so you can build on what was already covered "
                "instead of starting from scratch. Vector search runs "
                "across all of this user's notes, not just the current "
                "session.\n"
                "\n"
                "Returns up to `top_k` hits, each tagged with: "
                "`note_id` (the block id the note was originally mounted "
                "under — pass to `mount_template(replace=[...])` or "
                "`edit_note(block_id=...)` if you want to re-surface or "
                "extend it), `block_start`/`block_end` (0-based indices "
                "into the note's top-level markdown blocks — useful to "
                "cite a specific span), `text` (the chunk's markdown, "
                "prefixed with the nearest preceding heading for "
                "context), and `score` (cosine similarity).\n"
                "\n"
                "Worked examples: user asks 'remind me how attention "
                "works?' → `search_notes(query='self-attention "
                "transformer')`. User says 'going back to that thing "
                "about gradients' → `search_notes(query='gradient "
                "descent backprop')`. Use a topic phrase, not a verbatim "
                "user sentence — terser queries embed better."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic phrase to match against your notes.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Max hits to return. Default 5.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            executor=_make_search_notes(user_id),
        ),
        ToolSpec(
            name="look_at_image",
            description=(
                "Delegate visual perception to a vision model. You are "
                "text-only; this tool is your eyes. Pass `image` (a "
                "`data:image/png;base64,...` URL is preferred — http(s) "
                "URLs may fail due to provider region restrictions) plus "
                "an optional `question` to steer the description (e.g. "
                "'is the loading spinner still visible?', 'what does the "
                "error banner say?', 'are there non-blank pixels in the "
                "video region?'). Returns `{description: str}` — plain "
                "text you reason over as if the user had described the "
                "image themselves. Costs ~5–6s per call; use sparingly. "
                "Minimum image dimension is 14×14 pixels."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": (
                            "Image as a data URL (data:image/png;base64,...) "
                            "or http(s) URL."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Optional. What to look for. Defaults to a "
                            "general description."
                        ),
                    },
                },
                "required": ["image"],
                "additionalProperties": False,
            },
            executor=_make_look_at_image(user_id),
        ),
        ToolSpec(
            name="look_at_video",
            description=(
                "Delegate video perception to the vision pipeline. Use this "
                "for video files (mp4/mov/webm/mkv) or audio files (mp3/wav) "
                "— NOT for single still images, which go through "
                "`look_at_image`. Pass `video` as a local file path or "
                "http(s) URL. Optionally pass `question` to steer the "
                "per-frame prompt (e.g. 'what is the person writing?'). "
                "Returns `{description: str}` where the description is a "
                "chronological timeline interleaving visual descriptions "
                "and speech transcripts, e.g. "
                "`[00:00.0] vision: ...\\n[00:00.4–00:03.2] speech: \"...\"`. "
                "Slow: ffmpeg + many vision calls + Whisper transcription; "
                "expect 10–60s depending on clip length. Caps at "
                "`max_frames` vision calls (default 24, max 64)."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "video": {
                        "type": "string",
                        "description": (
                            "Local file path or http(s) URL to the video "
                            "or audio source."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Optional. What to look for in each frame. "
                            "Defaults to a general description."
                        ),
                    },
                    "max_frames": {
                        "type": "integer",
                        "description": (
                            "Optional. Cap on vision calls. Default 24, "
                            "max 64."
                        ),
                        "minimum": 1,
                        "maximum": 64,
                    },
                },
                "required": ["video"],
                "additionalProperties": False,
            },
            executor=_make_look_at_video(user_id),
        ),
        ToolSpec(
            name="read_url",
            description=(
                "Convenience shortcut: browser_set(goto) + close. One-shot "
                "headless read of a URL — no popup, no canvas mutation, no "
                "visible window. Loads the URL in headless Chromium, "
                "captures the visible text + the XHR/fetch responses the "
                "page made during load, then closes the page. Returns "
                "{url, title, text, length, truncated, responses}. Use "
                "this for the common 'what's on this URL' pattern; for "
                "anything more (interaction, observation over time, "
                "screenshots, evaluating JS) use browser_set directly. "
                "Do NOT echo the URL or raw text back at the user — "
                "extract meaning, then respond via speak."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to read.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            executor=_make_read_url(user_id),
        ),
        ToolSpec(
            name="browser_set",
            description=(
                "Comprehensive headless browser toolkit. Use whenever you "
                "need to read, interact with, or capture state from a "
                "web page WITHOUT showing it to the user. The page runs "
                "in a long-lived headless Chromium tab on the sidecar; "
                "one global session per process. Action names match "
                "Playwright's Page API verbatim — if you know "
                "Playwright, you already know how to use this:\n"
                "  - goto(url, wait_until?): page.goto. Loads URL, waits, "
                "captures XHRs. Returns {url, title, text, html, responses}.\n"
                "  - observe(drain=true): drain XHR responses captured "
                "since last observe + re-read text/html. Use after "
                "click/fill/wait, or while polling a live feed.\n"
                "  - SNAPSHOT / @REF (the cheap path for 'find a section'): "
                "snapshot() walks the ARIA tree and returns a compact list "
                "of @e1, @e2, ... refs for every heading / link / button / "
                "section / paragraph. Then call text(selector='@e42') to "
                "read just that section, or click(selector='@e7') to click "
                "it, or scroll(selector='@e42') to bring it into view. "
                "PREFER snapshot+@ref over evaluate for navigating long "
                "pages — it's faster, doesn't require writing JS, and "
                "doesn't blow up your context. Refs invalidate on any "
                "goto/reload/back/forward; re-snapshot after navigation.\n"
                "  - text(selector | @ref): return the inner text of one "
                "element. Use after snapshot to read a specific section "
                "instead of dragging the whole page through read_url.\n"
                "  - scroll(selector | @ref): scroll an element into view.\n"
                "  - click(selector | @ref | x,y, timeout?).\n"
                "  - fill(selector | @ref, value): set input value.\n"
                "  - type(selector | @ref, text, delay?): keystrokes.\n"
                "  - press(selector | @ref, key): e.g. key='Enter'.\n"
                "  - screenshot(full_page?): returns base64 PNG.\n"
                "  - screenshot_describe(full_page?): screenshot piped "
                "through the vision model; returns a textual description "
                "(you never see raw bytes). Costs ~5–6s.\n"
                "  - evaluate(expression): page.evaluate — JS, returns "
                "JSON-serialised result. RESERVED for reading window "
                "globals (window.__INITIAL_STATE__) or computed values "
                "that snapshot can't expose. DO NOT use evaluate to grep "
                "page text or scroll to anchors — snapshot + text/scroll "
                "do that better.\n"
                "  - wait_for_selector(selector | @ref, timeout?), "
                "wait_for_load_state(state), wait_for_timeout(timeout).\n"
                "  - reload, go_back, go_forward (all invalidate @refs).\n"
                "  - content (HTML), title, url.\n"
                "  - close(): close the page when done.\n"
                "Default flow for 'read this URL' is just read_url (a "
                "shortcut for goto+close). For 'find a specific section' "
                "on an already-loaded page: snapshot → text @eN. For "
                "interactive flows: goto → snapshot → click @eN → "
                "snapshot (refs invalidated by nav) → observe → close. "
                "Use web_view(open) instead ONLY when the user explicitly "
                "asks to SEE the page (replays, login walls, manual "
                "interaction)."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "goto", "observe", "click", "fill", "type",
                            "press", "screenshot", "screenshot_describe",
                            "evaluate", "wait_for_selector",
                            "wait_for_load_state", "wait_for_timeout",
                            "reload", "go_back", "go_forward",
                            "content", "title", "url", "close",
                            "snapshot", "text", "scroll",
                        ],
                    },
                    "url": {"type": "string", "description": "For goto."},
                    "selector": {
                        "type": "string",
                        "description": (
                            "CSS selector OR @e<n> ref from a prior "
                            "snapshot. For click/fill/type/press/"
                            "wait_for_selector/text/scroll."
                        ),
                    },
                    "value": {"type": "string", "description": "For fill."},
                    "text": {"type": "string", "description": "For type."},
                    "key": {"type": "string", "description": "For press, e.g. 'Enter'."},
                    "expression": {"type": "string", "description": "JS expression for evaluate."},
                    "state": {
                        "type": "string",
                        "enum": ["load", "domcontentloaded", "networkidle"],
                        "description": "For wait_for_load_state.",
                    },
                    "wait_until": {
                        "type": "string",
                        "enum": ["load", "domcontentloaded", "networkidle"],
                        "description": "For goto / reload / go_back / go_forward.",
                    },
                    "timeout": {"type": "integer", "description": "Milliseconds."},
                    "delay": {"type": "integer", "description": "ms between keystrokes for type."},
                    "full_page": {
                        "type": "boolean",
                        "description": "For screenshot / screenshot_describe.",
                    },
                    "drain": {
                        "type": "boolean",
                        "description": "For observe; if false, keep responses in buffer.",
                    },
                    "x": {"type": "integer", "description": "Click x-coord (alternative to selector)."},
                    "y": {"type": "integer", "description": "Click y-coord."},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            executor=_make_browser_set(user_id),
        ),
        ToolSpec(
            name="web_view",
            description=(
                "Visible web pane — the desktop's real Chromium "
                "BrowserView, mounted on the canvas as a draggable / "
                "resizable / closeable block. NOT the default for shared "
                "URLs — for 'explain this article', 'what is this', "
                "'summarise this paper', use read_url instead, then speak "
                "the answer. Reach for web_view ONLY when the user "
                "explicitly wants to see / play / interact with the page: "
                "'show me', 'open it', 'play this replay', 'let me watch', "
                "or when read_url failed to extract text (canvas / video / "
                "image-only SPAs). The page loads first-party (real "
                "cookies, real Referer), so anti-embed sites, DRM "
                "players, session-bound SPAs, video/canvas replays that "
                "'just don't work' inside request_new_block work here. "
                "Available actions: "
                "'open' (loads url, returns perception), 'observe' (re-checks "
                "current page without re-navigating — call twice ~2s apart "
                "to see if a video is actually playing via "
                "video.current_time advancing), 'click' (selector OR x+y), "
                "'type' (text into a focused or selected element), 'scroll' "
                "(direction up/down + pixel amount), 'wait_for' (selector "
                "appears within timeout_ms), 'close' (hide the pane). The "
                "perception report includes title, url, ready_state, "
                "loader_visible, video state (current_time, duration, "
                "paused), canvas presence, visible text excerpt, console "
                "errors, and failed network requests. Set "
                "include_screenshot=true on open/observe ONLY when DOM "
                "probes aren't enough — the screenshot is delegated to a "
                "vision model and the textual description is added to the "
                "report as `screenshot_description` (the persona never sees "
                "raw image bytes). include_screenshot adds ~5–6s. If the "
                "desktop isn't running, returns "
                "{\"error\": \"desktop_not_running\"} so you can speak the "
                "limitation back to the user."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "open", "observe", "click", "type",
                            "scroll", "wait_for", "close",
                        ],
                    },
                    "url": {
                        "type": "string",
                        "description": "Required for action='open'.",
                    },
                    "selector": {
                        "type": "string",
                        "description": (
                            "CSS selector. For click/type, alternative to x+y. "
                            "Required for wait_for."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type. Required for action='type'.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Scroll direction. Default 'down'.",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Scroll distance in pixels. Default 400.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Timeout for wait_for. Default 5000.",
                    },
                    "include_screenshot": {
                        "type": "boolean",
                        "description": (
                            "If true, capture a screenshot, run it through "
                            "the vision model, and add the textual "
                            "description to the report. Adds ~5–6s."
                        ),
                    },
                    "x": {
                        "type": "integer",
                        "description": "Click x-coordinate (alternative to selector).",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Click y-coordinate (alternative to selector).",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            executor=_make_web_view(user_id),
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
                "Display a known surface on the user's canvas. Templates: "
                "`upload_file` (PDF/video/audio/image picker), "
                "`passage_reader` (USER pastes/types their own text — "
                "input widget, never use for prose you author), "
                "`pdf_reader` (rendered PDF), `note` "
                "(YOUR PRIMARY EXPLANATION SURFACE — a Wikipedia-like "
                "card with prose, embedded Mermaid diagrams, inline "
                "images, highlights, and inline revision marks. Use this "
                "for ANY explanation longer than a sentence: definitions, "
                "comparisons, walkthroughs, illustrated concepts. **Pass "
                "`params: {markdown: '## H\\n\\nprose…'}`** — the server "
                "renders markdown to HTML using markdown-it with the "
                "==highlight== extension. Use `## Heading` for sections, "
                "`**bold**`, `==hi==` for highlights, `- bullet` for "
                "lists, and ` ```mermaid ` fenced code blocks for "
                "diagrams. Don't author raw `<div class=\"card-…\">` — "
                "headings + paragraphs get styled automatically. Legacy "
                "`params.content` (raw HTML) still works for back-compat "
                "but is discouraged.), `text_display` "
                "(short authored prose / voice transcripts — cheaper "
                "tokens than note. Use for one- or two-sentence "
                "answers only; reach for note the moment you want "
                "a heading, a list with structure, or any diagram.), "
                "`screen_share` (live screen-share — click START to "
                "begin streaming the user's screen + audio into "
                "perception; use when the user says 'watch me', "
                "'share my screen', 'see what I'm doing'), "
                "`inputs_launcher` (three-button starter, auto-mounted on "
                "empty canvas, rarely needed manually). "
                "Fast and deterministic. Pass `replace: [...]` to "
                "atomically swap out an existing surface.\n\n"
                "NOTE MARKDOWN WORKED EXAMPLE "
                "(author into `params.markdown`):\n"
                "```\n"
                "## Quicksort\n"
                "\n"
                "Divide-and-conquer with a ==pivot==.\n"
                "\n"
                "```mermaid\n"
                "graph TD; A[unsorted]-->B[pivot]; B-->C[left]; B-->D[right]\n"
                "```\n"
                "\n"
                "Then recurse on each half.\n"
                "\n"
                "- Best: **O(n log n)**\n"
                "- Worst: **O(n²)**\n"
                "```\n"
                "Syntax: `## H2` / `### H3` for sections, `**bold**`, "
                "`*italic*`, `==highlight==` (renders as <mark>), bullet "
                "and numbered lists, ` ```mermaid ` fenced code blocks "
                "for diagrams. Inline HTML is allowed for special cases "
                "(`<mark>`, `<ins>`, `<del>`, `<strong>` etc.) but you "
                "should reach for markdown syntax first. Legacy "
                "`params.content` (HTML grammar) still works.\n\n"
                "**NAMING NOTES (`slug`, required for `note`):** Each note "
                "you mount needs a stable topic-derived name so it can be "
                "recalled later via `search_notes` and re-edited via "
                "`edit_note`. Pass `slug` as a kebab-case identifier "
                "(lowercase letters, digits, '-'; e.g. `sumer-mesopotamia`, "
                "`transformer-attention`, `quicksort-algorithm`). "
                "Guidelines: 2–4 words, derived from the topic — NOT the "
                "user's question, NOT the date, NOT a number. If you "
                "forget to pass `slug`, it auto-derives from your first "
                "markdown heading.\n\n"
                "**THREE WAYS TO MOUNT A NOTE:**\n"
                "1. **Author new** — pass `slug` + `params.markdown`. "
                "Writes a fresh note. WARNING: if `slug` already exists "
                "in storage, this OVERWRITES the prior content — only do "
                "this when intentionally rewriting.\n"
                "2. **Re-display stored** — pass `slug` ONLY, no `params`. "
                "Hydrates the saved HTML from disk and brings the note "
                "back to canvas. Use this when `search_notes` (or the "
                "`=== RELATED STORED NOTES ===` section) shows a stored "
                "slug that already covers the topic. Cheap (no LLM "
                "authoring), no overwrite, the user sees their prior "
                "note again.\n"
                "3. **Extend in place** — use `edit_note(block_id=slug, "
                "ops=[…])` to append/revise/highlight a note that's "
                "already on canvas. Does NOT use `mount_template`."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "description": "Template filename stem (e.g. 'note').",
                    },
                    "slug": {
                        "type": "string",
                        "description": (
                            "Topic-derived kebab-case identifier for `note` "
                            "templates (e.g. 'sumer-mesopotamia'). Doubles "
                            "as block_id, filename, and note_id. Reusing a "
                            "slug REPLACES the prior note's content — use "
                            "`edit_note` to extend instead. Auto-derived "
                            "from the first markdown heading if omitted."
                        ),
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
                    "params": {
                        "type": "object",
                        "description": (
                            "Template-specific values.\n"
                            "• `note`: prefer `{markdown: '## H\\n\\n…'}` "
                            "(see worked example above). Legacy `{content: "
                            "'<html>...'}` still accepted.\n"
                            "• `text_display`: `{content: 'markdown'}`.\n"
                            "• Other templates: see their own docs."
                        ),
                    },
                },
                "required": ["template"],
                "additionalProperties": False,
            },
            executor=_make_mount_template(user_id),
        ),
        ToolSpec(
            name="edit_note",
            description=(
                "Apply a list of animated edits to an already-mounted "
                "note. Use this — NOT `mount_template` — when an "
                "existing note is on the same topic and should "
                "EVOLVE rather than be wiped and re-mounted. The user "
                "sees each op animate in place: new content slides in, "
                "highlights pulse, revisions flash diff colors.\n"
                "\n"
                "Op types:\n"
                "  • `append`  {md: '### New section\\n\\nprose'} — "
                "add markdown at the end of the card body. Animates "
                "slide+fade-in. (Legacy `html` field still accepted.)\n"
                "  • `prepend` {md: '…'} — add at the start.\n"
                "  • `replace_section` {anchor_text, md} — find the "
                "first heading whose text contains anchor_text, replace "
                "the section (heading + body until next equal-or-higher "
                "heading) with new markdown. Animates cross-fade.\n"
                "  • `revise` {target_text, new_text} — replace inline "
                "text matching target_text with new_text, marked with "
                "<del>/<ins>. Animates revision-flash. Use for "
                "corrections: 'cuneiform was ~3200 BCE, not 4000'.\n"
                "  • `highlight` {target_text, duration_ms?} — pulse-"
                "animate matching text. NO structural change. Use when "
                "the spoken answer just referenced something already "
                "shown: 'as I said about the Sumerians'.\n"
                "  • `arrow_to_text` {target_text, label?, direction?} "
                "— float a small arrow chip pointing at the matching "
                "text. Hangs ~3s.\n"
                "  • `annotate` {target_text, note} — attach a small "
                "caption near the matching text. Persists until next "
                "edit turn.\n"
                "\n"
                "`md` fields use the same markdown grammar as "
                "`mount_template`'s `params.markdown` (## headings, "
                "**bold**, ==hi==, lists, ```mermaid fences). The server "
                "applies ops to the cached markdown and re-renders to "
                "HTML. `target_text` / `anchor_text` must match a "
                "substring of the card's visible text exactly "
                "(case-sensitive). Match against the RENDERED text, not "
                "the markdown source — strip `**`/`==` etc. when "
                "specifying.\n"
                "\n"
                "You can mix ops in one call (e.g. append a new "
                "section AND highlight a related earlier phrase in "
                "the same call). The client animates them roughly "
                "simultaneously. **Cap: 3 ops per call; at most 1 "
                "highlight per turn.**\n"
                "\n"
                "Returns {block_id, ops_applied, op_names, mode} on "
                "success, {error: '...'} on validation failure. On "
                "error, fix and retry — DO NOT fall back to "
                "mount_template, which would wipe the card."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": (
                            "Slug of the note to edit (the same slug that "
                            "was passed to `mount_template` when this note "
                            "was created, e.g. 'sumer-mesopotamia')."
                        ),
                    },
                    "ops": {
                        "type": "array",
                        "description": "List of operations to apply in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": [
                                        "append", "prepend", "replace_section",
                                        "revise", "highlight", "arrow_to_text",
                                        "annotate",
                                    ],
                                },
                                "md": {"type": "string"},
                                "html": {"type": "string"},
                                "target_text": {"type": "string"},
                                "anchor_text": {"type": "string"},
                                "new_text": {"type": "string"},
                                "duration_ms": {"type": "integer"},
                                "label": {"type": "string"},
                                "direction": {
                                    "type": "string",
                                    "enum": ["left", "right", "up", "down"],
                                },
                                "note": {"type": "string"},
                            },
                            "required": ["op"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                    },
                    "target_device_id": {
                        "type": "string",
                        "description": "Optional UUID; route edits to this device only.",
                    },
                },
                "required": ["block_id", "ops"],
                "additionalProperties": False,
            },
            executor=_make_edit_note(user_id),
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
        ToolSpec(
            name="start_research",
            description=(
                "Enter research mode for an open-ended question that "
                "needs multi-step investigation. Examples: 'what's your "
                "opinion of this stock?', 'summarize this article and "
                "tell me what to focus on', 'compare these two papers', "
                "'look into X and tell me what you find'. Calling this "
                "spawns a dedicated research turn (Lane R) with the full "
                "browser toolkit, ~25 tool-call rounds, and ~90 s of "
                "wall-clock time. A progress ribbon mounts at the top of "
                "the canvas so the user sees the planned steps and "
                "watches them tick off. The research turn synthesizes "
                "and speaks the answer when done — DO NOT also call "
                "speak yourself in the same Lane A turn. If you pass "
                "`page_url` and the user has researched that URL's host "
                "before, the system replays the saved procedure in ~5-10 s "
                "instead of running the full ~90 s investigation. Returns "
                "immediately with {status: 'started', via: 'recipe'|'fresh'} "
                "or {status: 'already_running'} if a prior research turn "
                "is still in flight."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "The user's question, restated in your own "
                            "words. The research turn uses this verbatim "
                            "as the investigation target."
                        ),
                    },
                    "page_url": {
                        "type": "string",
                        "description": (
                            "Optional. If the user's question is about a "
                            "specific URL already visible on canvas (a "
                            "web_view block or a recently read article), "
                            "pass it here. The research subsystem uses "
                            "the URL's host to look up saved procedures "
                            "— a hit replays the synthesis in ~5-10 s "
                            "instead of ~90 s. Forgetting this is fine "
                            "(the system also tries to infer it from "
                            "canvas state) but explicit is faster."
                        ),
                    },
                    "why_this_is_multi_step": {
                        "type": "string",
                        "description": (
                            "Optional. One sentence explaining why a "
                            "single-tool reply isn't enough. Helps you "
                            "audit your own decision; not seen by the "
                            "research loop."
                        ),
                    },
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
            executor=_make_start_research(user_id),
        ),
        ToolSpec(
            name="research_plan",
            description=(
                "Inside research mode ONLY. Record (or revise) the plan "
                "of 3–7 steps you'll execute to answer the goal. The "
                "first step you list is marked 'doing'; the rest "
                "'pending'. The progress ribbon on the user's canvas "
                "updates immediately. Call this exactly once at the "
                "start; revise mid-run only when you discover a step is "
                "unnecessary or one is missing. Returns the current "
                "plan with each step's index — use those indices in "
                "research_note calls."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 7,
                        "description": (
                            "Ordered list of step descriptions. Each "
                            "should be concrete and executable with one "
                            "or two tool calls (e.g. 'Read price + "
                            "key stats from page', 'Scan the headlines "
                            "in the news section')."
                        ),
                    },
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
            executor=_make_research_plan(user_id),
        ),
        ToolSpec(
            name="research_note",
            description=(
                "Inside research mode ONLY. After completing a step, "
                "call this to record the takeaway. Marks the step as "
                "done in the canvas ribbon and auto-advances the next "
                "step to 'doing'. Keep findings concrete: numbers, "
                "headlines, dates, quotes you actually observed — not "
                "hand-waving. ≤ 280 chars (longer findings are "
                "truncated). Set error=true if the step failed (the "
                "ribbon shows ✕ and you should re-plan or skip)."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "step_index": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "0-based index from the current plan.",
                    },
                    "finding": {
                        "type": "string",
                        "description": "≤ 280 chars. Concrete takeaway from the step.",
                    },
                    "error": {
                        "type": "boolean",
                        "description": "If true, mark the step as failed (✕).",
                    },
                },
                "required": ["step_index", "finding"],
                "additionalProperties": False,
            },
            executor=_make_research_note(user_id),
        ),
    ]
    # Filter by lane. Any tool not listed in _TOOL_LANES is treated as
    # available everywhere (forward-compat for tools added without
    # remembering to update the map).
    return [t for t in full if lane in _TOOL_LANES.get(t.name, {"answer", "user_facing", "background"})]


__all__ = ["build_tools"]
