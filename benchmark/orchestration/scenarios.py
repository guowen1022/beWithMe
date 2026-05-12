"""Tool-orchestration scenarios.

Each scenario fires a single user message and grades whether the persona
picked the right tool (and reasonable arguments). The grader uses
predicate functions on the collected tool_call events from /api/ask/stream.

Pass rule: ANY tool call observed during the turn satisfies the
scenario's `predicate(name, args)`. (Tool turns can chain — we accept
the first match.)

A scenario can mark `expected_tool="none"` to assert the persona did
NOT call a tool — useful for "this is a knowledge question, just talk"
shapes. In that case the predicate is ignored.

Scenarios run with `X-Device-Class: desktop` so the persona is in
voice-or-both mode (default). Action intents should still mount
templates per `canvas_persona.md` regardless of voice mode.
"""
from __future__ import annotations

from typing import Any, Callable


def _str_args(args: dict[str, Any]) -> str:
    """Lowercased blob of every string value in args. Used for
    permissive substring checks on descriptions/queries."""
    parts: list[str] = []
    for v in args.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts.append(_str_args(v))
    return " ".join(parts).lower()


def _has(args: dict, *terms: str) -> bool:
    """True if every term appears in the lowercased arg blob."""
    blob = _str_args(args)
    return all(t.lower() in blob for t in terms)


# Tools that don't count as "real" orchestration — the persona may
# call these to gather context before its real action. We ignore them
# for grading purposes.
_NO_OP_TOOLS = {"read_media", "look_at_image"}


SCENARIOS: list[dict] = [
    {
        "id": "upload_pdf",
        "question": "I want to upload a PDF",
        "expected_tool": "mount_template",
        "predicate": lambda name, args: name == "mount_template"
            and args.get("template") == "upload_file",
        "rationale": "Classic action intent — canvas_persona example #1.",
    },
    {
        "id": "paste_passage",
        "question": "Let me paste a passage of text I want to read",
        "expected_tool": "mount_template",
        "predicate": lambda name, args: name == "mount_template"
            and args.get("template") == "passage_reader",
        "rationale": "Action intent — passage_reader is the canonical template.",
    },
    {
        "id": "screen_share",
        "question": "Watch me debug — let me share my screen with you",
        "expected_tool": "mount_template",
        "predicate": lambda name, args: name == "mount_template"
            and args.get("template") == "screen_share",
        "rationale": "Action intent — screen_share template explicitly exists.",
    },
    {
        "id": "ambient_mic",
        "question": "I want to just talk to you instead of typing, can you listen?",
        "expected_tool": "mount_template",
        "predicate": lambda name, args: name == "mount_template"
            and args.get("template") == "ambient_mic",
        "rationale": "Action intent — ambient_mic template handles continuous voice input.",
    },
    {
        "id": "open_web_url",
        "question": "Open the Wikipedia page on transformer neural networks",
        "expected_tool": "mount_template",
        "predicate": lambda name, args: (
            (name == "mount_template" and args.get("template") in ("web_view", "url_card"))
            # Some flows mount web_view directly as its own verb.
            or name == "web_view"
        ),
        "rationale": "Action intent — open external URL on canvas.",
    },
    {
        "id": "custom_block",
        "question": (
            "Build me a unit converter block that converts Celsius to "
            "Fahrenheit. Two input fields and a result."
        ),
        # The actual delegation tool is `request_new_block` (NOT
        # `request_ui_block` despite the internal API name; the LLM
        # sees `request_new_block`).
        "expected_tool": "request_new_block",
        "predicate": lambda name, args: name == "request_new_block"
            and (
                _has(args, "celsius")
                or _has(args, "converter")
                or _has(args, "fahrenheit")
            ),
        "rationale": "Custom block — must delegate to the engineer.",
    },
    {
        "id": "research_question",
        "question": (
            "I want you to research the current scientific consensus on "
            "whether dark energy is constant or evolving over time. "
            "Look it up online."
        ),
        # The persona's research-launch tool is `start_research` (it
        # routes the question to Lane R for full investigation). web_search
        # / browser_set inside Lane A also count as reasonable.
        "expected_tool": "start_research",
        "predicate": lambda name, args: name in (
            "start_research", "web_search", "browser_set"
        ),
        "rationale": "Research-grade query — should trigger a research-flavor tool.",
    },
    {
        "id": "knowledge_question_voice",
        "question": "What is a mitochondria?",
        # Voice mode: the right answer is JUST STREAM PROSE — the
        # auto-speak buffer handles audio. read_media is harmless
        # canvas-state probing and is filtered out by _NO_OP_TOOLS.
        # A mount_template here would be the wrong call.
        "expected_tool": "none",
        "predicate": None,
        "rationale": "Voice knowledge Q — should stream prose, not mount text_display.",
    },
]


__all__ = ["SCENARIOS", "_has", "_str_args", "_NO_OP_TOOLS"]
