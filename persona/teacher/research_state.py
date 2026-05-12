"""Per-user in-memory state for Lane R (research mode).

The teacher's research lane needs an externalized scratchpad — a plan
the LLM can re-read and update across many tool-call rounds, plus
findings keyed by step index. This module owns that state.

State is per-user and per-process: a research run lives only as long as
the FastAPI process that started it. That's fine — research turns are
finite (~90 s) and not meant to survive a restart.

Concurrency: only one research run per user at a time. `start_research`
should refuse to spawn a second run while one is in flight.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID


StepStatus = Literal["pending", "doing", "done", "error"]


@dataclass
class ResearchStep:
    text: str
    status: StepStatus = "pending"
    note: Optional[str] = None


@dataclass
class ResearchState:
    goal: str = ""
    steps: List[ResearchStep] = field(default_factory=list)
    finished: bool = False
    block_id: str = "research-progress"

    def to_payload(self) -> Dict[str, Any]:
        """Shape consumed by the research_progress block on canvas."""
        return {
            "goal": self.goal,
            "steps": [asdict(s) for s in self.steps],
            "finished": self.finished,
        }

    def to_llm_view(self) -> Dict[str, Any]:
        """Compact shape returned to the LLM after each plan/note call.

        Includes step indices so the model can address them precisely
        in the next research_note call.
        """
        return {
            "goal": self.goal,
            "steps": [
                {
                    "index": i,
                    "text": s.text,
                    "status": s.status,
                    "note": s.note,
                }
                for i, s in enumerate(self.steps)
            ],
            "finished": self.finished,
        }


# In-flight runs, keyed by user_id. Presence means a research turn is
# currently executing for that user. The state survives until the run
# tears down — `clear` is called from the run's finally block.
_active: Dict[UUID, ResearchState] = {}


def is_active(user_id: UUID) -> bool:
    return user_id in _active


def begin(user_id: UUID, goal: str) -> ResearchState:
    """Create a fresh state for a new research run. Idempotent if a
    run is already active (returns the existing state)."""
    existing = _active.get(user_id)
    if existing is not None:
        return existing
    state = ResearchState(goal=goal)
    _active[user_id] = state
    return state


def get(user_id: UUID) -> Optional[ResearchState]:
    return _active.get(user_id)


def set_plan(user_id: UUID, steps: List[str]) -> Optional[ResearchState]:
    """Replace the plan with a new list of steps. The first step is
    marked 'doing'; the rest are 'pending'. If existing notes line up
    by index, they're preserved on a re-plan (the LLM may revise the
    plan mid-run; we don't want to lose findings already recorded)."""
    state = _active.get(user_id)
    if state is None:
        return None
    prior = state.steps
    new_steps: List[ResearchStep] = []
    for i, text in enumerate(steps):
        prior_step = prior[i] if i < len(prior) else None
        new_steps.append(ResearchStep(
            text=text,
            status="doing" if i == 0 else "pending",
            note=(prior_step.note if prior_step is not None else None),
        ))
    # Preserve completion on the first step if it carried over from a
    # prior plan with the same text — small UX nicety on revision.
    if new_steps and prior and prior[0].text == new_steps[0].text and prior[0].status == "done":
        new_steps[0].status = "done"
        # Advance "doing" to the first non-done step.
        for s in new_steps:
            if s.status != "done":
                s.status = "doing"
                break
    state.steps = new_steps
    return state


def record_note(
    user_id: UUID,
    step_index: int,
    finding: str,
    *,
    error: bool = False,
) -> Optional[ResearchState]:
    """Mark a step done (or error) and attach a finding. Auto-advances
    the next pending step to 'doing' so the ribbon shows progress."""
    state = _active.get(user_id)
    if state is None:
        return None
    if step_index < 0 or step_index >= len(state.steps):
        return state
    step = state.steps[step_index]
    step.note = finding[:280] if finding else step.note
    step.status = "error" if error else "done"
    # Move the next pending step into 'doing' so the ribbon's animated
    # dot tracks the current focus.
    for s in state.steps:
        if s.status == "pending":
            s.status = "doing"
            break
    return state


def finish(user_id: UUID) -> Optional[ResearchState]:
    state = _active.get(user_id)
    if state is None:
        return None
    state.finished = True
    # Any still-pending or still-doing steps at finish time are stragglers;
    # leave their status alone so the user can see what wasn't completed.
    return state


def clear(user_id: UUID) -> None:
    """Drop the active state. Called from the research run's finally
    block; safe to call multiple times."""
    _active.pop(user_id, None)


__all__ = [
    "ResearchState",
    "ResearchStep",
    "StepStatus",
    "is_active",
    "begin",
    "get",
    "set_plan",
    "record_note",
    "finish",
    "clear",
]
