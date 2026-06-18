"""Teacher's tool manifest — assembles the per-request `ToolSpec` list.

Generic verbs (canvas mutations, perception, web reading) live next to
their implementations and expose `build_spec(user_id)`. This module
combines those with teacher-only verbs (research lane) and applies the
lane filter.

Each call to `build_tools(user_id, lane=...)` returns a fresh list with
executors bound to this user_id. The LLM cannot supply a different
user_id — that's enforced by closure.

Tool results returned to the LLM should stay compact — they re-enter the
context on every subsequent turn. We summarise (count + ids) rather than
echoing back the full payload.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from infra.model.tools import ToolSpec

# Generic verbs — each tool exposes `build_spec(user_id)` next to its impl.
# Imported individually (not via aggregators) so this file controls the
# exact order they appear in the final list; the LLM provider's prompt
# cache is keyed on the serialized tools array, so a stable order matters.
from tools import (
    browser_set as _browser_set,
    look_at_image as _look_at_image,
    look_at_video as _look_at_video,
    read_captures as _read_captures,
    read_document as _read_document,
    read_url as _read_url,
    read_world_knowledge as _read_world_knowledge,
    search_notes as _search_notes,
    set_talk_channel as _set_talk_channel,
    speak as _speak,
    stream_emit as _stream_emit,
    stream_projection as _stream_projection,
    stream_query as _stream_query,
    web_view as _web_view,
    write_to_inbox as _write_to_inbox,
)
# read_concept_mastery is a teacher tool (reads persona.teacher.knowledge), so it
# lives under the teacher persona, not in the generic tools/ package.
from persona.teacher.tools import read_concept_mastery as _read_concept_mastery
# end_session is a teacher tool — a thin wrapper over the existing
# /api/sessions/{id}/end API + the go_home app-action. It is NOT a teaching
# verb; it lives in the session-tool set (build_session_tools), reached via
# the heuristic dispatcher, not the answer lane.
from persona.teacher.tools import end_session as _end_session
from workshop.canvas.tools import (
    block_action as _block_action,
    edit_note as _edit_note,
    interactive_graph as _interactive_graph,
    layout_blocks as _layout_blocks,
    list_media as _list_media,
    mount_template as _mount_template_tool,
    point_arrow as _point_arrow,
    push_block_content as _push_block_content_tool,
    read_media as _read_media,
    request_ui_block as _request_ui_block,
)
from workshop.canvas.tools.mount_template import mount_template
from workshop.canvas.tools.push_block_content import push_block_content


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


def _build_research_specs(user_id: UUID) -> List[ToolSpec]:
    """Teacher-only research-lane tools. Live here (not in `tools/` or
    `workshop/`) because they orchestrate `persona.teacher.research_state`
    — a per-persona piece of state, not a generic verb."""
    return [
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


def _build_guide_spec(user_id: UUID) -> ToolSpec:
    """Canvas-writer visual-guide loader. Pulls ONE modality's fence syntax
    (plot | mermaid) into context on demand so the writer's base prompt stays
    thin and a flowchart turn never pays for the plot syntax. Non-authoring —
    it mounts nothing. See `persona.teacher.prompts.canvas_guides`."""
    from persona.teacher.prompts import canvas_guides

    async def executor(args: Dict[str, Any]) -> str:
        return canvas_guides.get_guide(args.get("ids"))

    return ToolSpec(
        name="load_guide",
        description=(
            "Open a VISUAL GUIDE to get the exact fence syntax for a diagram "
            "or plot BEFORE authoring it. The guide menu in your prompt lists "
            "the ids (e.g. 'plot', 'mermaid'). Call with the one id you need; "
            "it returns that guide's full syntax + examples. This is NOT your "
            "authoring call — after reading the guide, emit your single "
            "mount_template/edit_note with the fence embedded in the markdown. "
            "Open only what this turn needs."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Guide ids to open, e.g. ["plot"]. Usually one.',
                },
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
        executor=executor,
    )


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
    # Fast HTTP write (no LLM) — user can flip voice/caption mid-conversation.
    "set_talk_channel":   {"answer", "user_facing", "background", "research"},
    "mount_template":     {"answer", "user_facing", "background", "research", "writer"},
    "edit_note":     {"answer", "user_facing", "background", "research", "writer"},
    "block_action":       {"answer", "user_facing", "background", "research"},
    "push_block_content": {"answer", "user_facing", "background", "research"},
    "point_arrow":        {"answer", "user_facing", "background", "research"},
    "layout_blocks":      {"answer", "user_facing", "background", "research"},
    "interactive_graph":  {"answer", "user_facing", "background", "research"},
    # Canvas-writer visual-guide loader (Layer-2 lazy skill loading).
    "load_guide":         {"writer"},
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
    # Maestro-era stream + domain READ tools (PR-2).
    # stream_emit is WRITE — keep it off user_facing so a spoken Lane A
    # turn doesn't burn its single iteration on observation-writing
    # when speak/start_research are the calls that matter.
    "stream_emit":          {"answer", "background", "research"},
    "stream_query":         {"answer", "background", "research"},   # heavier read; like read_document
    "stream_projection":    {"answer", "user_facing", "background", "research"},  # cheap, projection-cached
    "read_concept_mastery": {"answer", "user_facing", "background", "research"},  # local DB, fast
    "read_world_knowledge": {"answer", "user_facing", "background", "research"},  # parity with search_notes
    "read_captures":        {"answer", "user_facing", "background", "research"},  # enumeration only, look_at_image does the work
    # PR-5 — ACT tool. Write the user-visible inbox card from a kickoff
    # candidate. Off user_facing because Lane A's spoken turn shouldn't
    # be writing proactive proposals — those come from the maestro path.
    "write_to_inbox":       {"answer", "background", "research"},
}


def build_tools(user_id: UUID, lane: Lane = "answer") -> List[ToolSpec]:
    """Return the per-request tool list for the teacher.

    `lane` filters the toolset (see Lane comments above). Each tool's
    `build_spec(user_id)` lives next to its implementation; this module
    orders them and adds the teacher-only research-lane verbs.

    The order matches the historical hand-authored order so the LLM
    provider's prompt cache stays warm across this refactor.
    """
    full = [
        _read_media.build_spec(user_id),
        _read_document.build_spec(user_id),
        _search_notes.build_spec(user_id),
        _look_at_image.build_spec(user_id),
        _look_at_video.build_spec(user_id),
        _read_url.build_spec(user_id),
        _browser_set.build_spec(user_id),
        _web_view.build_spec(user_id),
        _list_media.build_spec(user_id),
        _mount_template_tool.build_spec(user_id),
        _edit_note.build_spec(user_id),
        _request_ui_block.build_spec(user_id),
        _interactive_graph.build_spec(user_id),
        _push_block_content_tool.build_spec(user_id),
        _point_arrow.build_spec(user_id),
        _speak.build_spec(user_id),
        _set_talk_channel.build_spec(user_id),
        _layout_blocks.build_spec(user_id),
        _block_action.build_spec(user_id),
        # Maestro-era stream + domain READ tools (PR-2). Appended at
        # the end so the existing tool order — which the LLM provider's
        # prompt cache is keyed on — is preserved.
        _stream_emit.build_spec(user_id),
        _stream_query.build_spec(user_id),
        _stream_projection.build_spec(user_id),
        _read_concept_mastery.build_spec(user_id),
        _read_world_knowledge.build_spec(user_id),
        _read_captures.build_spec(user_id),
        # PR-5 — kickoff realization ACT tool.
        _write_to_inbox.build_spec(user_id),
        *_build_research_specs(user_id),
        # Canvas-writer visual-guide loader — appended last so existing tool
        # order (the provider prompt-cache key) is unchanged.
        _build_guide_spec(user_id),
    ]
    return [
        t for t in full
        if lane in _TOOL_LANES.get(t.name, {"answer", "user_facing", "background"})
    ]


def build_session_tools(
    user_id: UUID, session_id: Optional[UUID] = None,
) -> List[ToolSpec]:
    """The session-control tool set — invoked by the session-handling pass
    (reached via the heuristic dispatcher), separate from the teaching tools.

    Today: just `end_session`. Add `pause` / `save & summarize` / etc. here as
    they land; the dispatcher decides *that* a turn is session-control, this
    set + the session prompt decide *which* action.
    """
    return [_end_session.build_spec(user_id, session_id)]


__all__ = ["build_tools", "build_session_tools", "Lane"]
