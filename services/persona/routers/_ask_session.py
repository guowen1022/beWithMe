"""Stage-2 session control for `/api/ask/stream`.

Reached only after the teacher's fast line (Stage 1) decides — with guidance,
not a rule — that the turn is *outside* the teaching loop and calls
`request_session_control`. There is no fixed pre-filter; the language model
made the routing decision.

Here we open the session-control decision tree: the small session-tool set
(`build_session_tools` — today just `end_session`) and a focused prompt, and
let the model pick which tool to call. The normal spoken reply and the
canvas-writer are suppressed by the caller — this turn is an action, not Q&A,
so it stores no Interaction.

Yields SSE dicts (status / token / answer) onto the caller's status_queue.
"""
from __future__ import annotations

from uuid import UUID

from persona.teacher.prompts.skills import load_skill
from persona.teacher.schemas import AskRequest
from persona.teacher.tools.loop import run as run_teacher_tool_loop
from persona.teacher.tools.manifest import build_session_tools


_FALLBACK_PROMPT = (
    "You are handling a session-control request — the user wants to do something "
    "with the session itself, not learn. Use the available tool to carry it out. "
    "Do not reply with text — call the tool."
)


async def run_session_control(question: str, user_id: UUID, body: AskRequest):
    """Stage 2: the model picks a session tool and acts. Yields SSE dicts."""
    system = load_skill("teacher/session_control") or _FALLBACK_PROMPT
    tools = build_session_tools(user_id, body.session_id)

    yield {"type": "status", "status": "thinking", "detail": "session control"}
    answer_parts: list[str] = []
    tools_called: list[str] = []
    try:
        async for evt in run_teacher_tool_loop(
            static_system=system,
            static_user_passage="",
            dynamic_user=question,
            prior_messages=None,
            tools=tools,
            max_tokens=256,
            max_iterations=2,
            purpose="session",
            user_id=user_id,
            disable_thinking=True,
            terminal_tools={"end_session"},
        ):
            kind = evt.get("kind")
            if kind == "delta":
                text = evt.get("text", "")
                if text:
                    answer_parts.append(text)
                    yield {"type": "token", "text": text}
            elif kind == "tool_call":
                name = evt.get("name") or ""
                if name:
                    tools_called.append(name)
                    yield {"type": "status", "status": "acting", "detail": name}
    except Exception as e:
        print(f"[ask/session] session control failed: {e}", flush=True)
        yield {
            "type": "answer",
            "answer": f"session control failed: {e}",
            "title": "session: error",
            "related_interaction_ids": [],
        }
        return

    answer = "".join(answer_parts).strip() or (
        ("Done: " + ", ".join(tools_called)) if tools_called else "No session action taken."
    )
    title = ("session: " + ", ".join(tools_called)) if tools_called else "session"
    yield {"type": "title", "title": title}
    yield {
        "type": "answer",
        "answer": answer,
        "title": title,
        "related_interaction_ids": [],
    }


__all__ = ["run_session_control"]
