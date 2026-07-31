"""Canvas-writer pass — voice-leads Phases 1 and 2.

Runs *after* the spoken pass of a voice turn completes. Given the user's
question (or trigger summary) and the spoken transcript, builds a small
prompt and runs one short tool loop.

Phase 1 exposed only `mount_template` — every visual turn mounted a
fresh note, replacing any prior card.

Phase 2 also exposes `edit_note` and injects the full cached HTML
of every currently-mounted note into the writer's prompt. The
writer chooses among:
  * mount a new card (`mount_template`) — no note on canvas, or
    the topic has shifted entirely;
  * evolve the existing card (`edit_note`) — append a paragraph,
    revise a fact, highlight what voice just referenced;
  * do nothing — voice answer is self-contained.

Detached from the SSE stream of the originating request: callers spawn
this as `asyncio.create_task(run_canvas_writer(...))` and forget.

Shared between the typed path (`services/persona/routers/ask.py`) and
the perception/Lane A path (`persona/teacher/triggers.py`).
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from infra import skillforge_client
from infra.event_log import log_event
from persona.teacher.canvas_writer_pass import (
    WriterContractError, WriterInputs, run_writer_pass,
)
from persona.teacher.prompts import canvas_guides
from infra.silicon_brain_client import SiliconBrainClient
from workshop.canvas.tools import _note_cache
from workshop.canvas.tools.read_media import read_media


def _collect_existing_notes(canvas_state, user_id: UUID) -> Dict[str, str]:
    """For every note block visible in `canvas_state`, fetch the
    cached MARKDOWN (Phase 2.5) so we can inject it into the writer
    prompt. Falls back to HTML when md is unavailable (legacy mounts).

    Returns a dict of block_id → source-of-truth content (md preferred,
    html fallback). Blocks whose state.kind isn't `'rich'` are skipped;
    blocks present in canvas_state but missing from the cache (race or
    pre-cache mount) are also skipped — the writer just won't see
    their content this turn.
    """
    out: Dict[str, str] = {}
    if canvas_state is None:
        return out
    canvases = getattr(canvas_state, "canvases", None) or []
    seen: set[str] = set()
    for canvas in canvases:
        for block in getattr(canvas, "blocks", None) or []:
            bid = getattr(block, "id", None)
            if not bid or bid in seen:
                continue
            seen.add(bid)
            state = getattr(block, "state", None)
            kind = getattr(state, "kind", None) if state is not None else None
            if kind != "rich":
                continue
            source = _note_cache.get_md(user_id, bid) or _note_cache.get_html(user_id, bid)
            if source:
                out[bid] = source
    return out


async def run_canvas_writer(
    *,
    question: str,
    transcript: str,
    user_id: UUID,
    req_id: Optional[str] = None,
    origin: Optional[float] = None,
    source: str = "ask",
) -> None:
    """Voice-leads canvas pass — runs after the spoken answer completes.

    Builds the writer prompt with the full HTML of any existing
    note, exposes `mount_template` + `edit_note`, and runs
    a short tool loop. Errors are caught and logged; we never raise —
    the SSE stream that birthed this task is already closed.
    """
    writer_t0 = time.perf_counter()

    # Filter canvas state to the originating/output device so the writer's
    # view of "what's already mounted" matches what that specific device
    # will see. Without this, a card mounted on desktop in a prior session
    # would make the writer skip mount on a fresh mobile turn — the mobile
    # device never sees the card. OUTPUT_DEVICE_ID is set by ask.py from
    # X-Device-Id (or X-Output-Device-Id) and inherited via contextvars.
    from infra.contracts.output_routing import get_output_device_id
    target_device_id = get_output_device_id()

    canvas_state = None
    try:
        canvas_state = await read_media(
            user_id,
            device_ids=[target_device_id] if target_device_id else None,
        )
    except Exception as e:
        print(f"[writer] read_media error: {e}", flush=True)

    existing_cards = _collect_existing_notes(canvas_state, user_id)
    existing_slugs = set(existing_cards.keys())

    # Semantically related notes from the user's prior teaching — even if
    # they're not currently on canvas. Lets the writer decide between
    # `edit_note` (slug already exists in storage) vs `mount_template`
    # (topic is genuinely new). Drop hits already in `existing_cards` so
    # we don't double-show them.
    related_notes: List[dict] = []
    try:
        sb = SiliconBrainClient()
        try:
            probe = f"{question}\n{transcript}".strip()
            if probe:
                hits = await sb.search_notes(user_id, probe, top_k=3)
                related_notes = [
                    {"slug": h.note_id, "score": h.score, "text": h.text}
                    for h in hits
                    if h.score >= 0.40 and h.note_id not in existing_slugs
                ]
        finally:
            await sb.aclose()
    except Exception as e:
        print(f"[writer] search_notes error: {e}", flush=True)

    # Resolve the visual-guide menu tunable ONCE for this turn: its config
    # shapes the menu the writer sees, and its version stamps the telemetry
    # below. Resolving twice could straddle a background snapshot refresh and
    # mis-attribute the outcome (see collect_result's variant_version note).
    menu_tuned = skillforge_client.resolve(canvas_guides.MENU_TUNABLE_ID)

    # The decision point itself. The SAME call the tuning sidecar's scorer makes — one
    # function, two callers — so a change to the prompt, the tool surface or any loop knob
    # cannot land in production without the thing that measures it seeing the change too.
    try:
        result = await run_writer_pass(
            inputs=WriterInputs(
                question=question,
                voice_transcript=transcript,
                canvas_state=canvas_state,
                existing_notes=existing_cards,
                related_notes=related_notes,
                menu_config=menu_tuned.config,
            ),
            user_id=user_id,
            purpose="canvas-writer",
        )
    except WriterContractError as e:
        # Both spawn sites already gate on a non-empty question and spoken answer, so this
        # is a broken caller rather than a quiet turn. Say so and stop — a pass with nothing
        # to mirror would author nothing and bank a misleading 0.0 against the menu.
        print(f"[writer] refusing to run: {e}", flush=True)
        log_event("ask.writer_done", req_id=req_id, user_id=str(user_id), source=source,
                  wall_ms=round((time.perf_counter() - writer_t0) * 1000, 2),
                  mount_fired=False, transcript_len=len(transcript),
                  error=f"missing_required_input:{e.name}")
        return

    mount_fired = result.mount_fired
    edit_ops = result.edit_ops
    selected_guides = result.selected_guides
    authored_parts = result.authored_parts
    error = result.failed_because
    if error:
        print(f"[writer] tool loop error: {error}", flush=True)

    log_event(
        "ask.writer_done",
        req_id=req_id,
        user_id=str(user_id),
        source=source,
        wall_ms=round((time.perf_counter() - writer_t0) * 1000, 2),
        since_request_ms=(
            round((time.perf_counter() - origin) * 1000, 2)
            if origin is not None
            else None
        ),
        mount_fired=mount_fired,
        edit_ops=edit_ops or None,
        cached_cards_seen=len(existing_cards),
        transcript_len=len(transcript),
        error=error,
    )

    # skillforge: attribute this turn to the visual-guide menu variant that ran.
    # Outcome = did the modality the writer OPENED from the menu match the fence
    # it AUTHORED (1.0 match / 0.0 wrong-modality / neutral when it peeked then
    # answered in prose). Fail-open + no-op when skillforge is disabled.
    authored = canvas_guides.authored_modalities("\n".join(authored_parts))
    emit, ok, scalar = canvas_guides.menu_outcome(selected_guides, authored)
    if emit:
        correlation_id = uuid4().hex
        skillforge_client.collect_result(
            canvas_guides.MENU_TUNABLE_ID,
            ok=ok,
            outcome_scalar=scalar,
            correlation_id=correlation_id,
            variant_version=menu_tuned.version,
        )
        # Hand attributable turns' CONTENT to the tuning sidecar — telemetry
        # above is digest-only, never replayable. The sidecar applies the
        # capture policy (failures = authored-a-fence-it-never-opened, always;
        # successes sampled + capped as regression anchors) and forwards the
        # survivors to skillforge as replayable scenarios (same correlation_id
        # links scenario to telemetry row).
        if ok and scalar is not None:
            skillforge_client.capture_case(
                canvas_guides.MENU_TUNABLE_ID,
                {
                    "question": question,
                    "transcript": transcript,
                    "selected": sorted(selected_guides),
                    "authored": sorted(authored),
                    "outcome": scalar,
                    "correlation_id": correlation_id,
                },
            )


__all__ = ["run_canvas_writer"]
