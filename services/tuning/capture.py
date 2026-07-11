"""Convert real writer-turn cases into replayable skillforge scenarios.

The writer hands this sidecar every attributable turn's CONTENT (question,
transcript, what it opened, what it authored, the observed outcome). The
capture POLICY lives here, off the hot path:

  * **failure** (outcome 0.0 — authored a fence it never opened): ALWAYS
    captured. Rare, and each one is refinement headroom.
  * **success** (outcome 1.0): SAMPLED (``_SUCCESS_SAMPLE_RATE``) and CAPPED
    (``_SUCCESS_CAP`` stored ``from_traffic`` rows) — real-traffic regression
    anchors. Every stored scenario costs a full writer replay (~10s) on every
    future evaluate/gate/refine, so successes must stay a bounded sample.
    Never guards: replay is stochastic, and a noisy auto-guard could randomly
    veto every promotion.

Survivors go to skillforge's capture endpoint (M8, `POST
/api/eval/{host}/{tunable}/capture`), which dedups by input and stores them as
`origin="from_failure"` / `origin="from_traffic"` scenarios. From there the
existing evaluate/gate/refine/drift loop replays them — real production
traffic becomes the eval set.

Ground-truth note: `expect_guide` is auto-labeled from the writer's revealed
choice — on failure, the modality it AUTHORED without opening (the guide the
menu should have steered it to open); on success, the modality it opened and
authored. Exactly what the online outcome metric scores, so the offline
replay optimizes the same target. Curators can re-label rows in the store.
"""
from __future__ import annotations

import random
from typing import Optional

import httpx

from infra.config import settings
from persona.teacher.prompts.canvas_guides import GUIDE_TREE, MENU_TUNABLE_ID

_TIMEOUT_S = 10.0
_QUESTION_MAX = 2000
_TRANSCRIPT_MAX = 4000

_SUCCESS_SAMPLE_RATE = 0.10
_SUCCESS_CAP = 20


def _count_traffic_rows(eval_svc: str, tunable_id: str, client: httpx.Client) -> int:
    r = client.get(f"{eval_svc}/api/eval/{settings.skillforge_host}/{tunable_id}/scenarios")
    r.raise_for_status()
    return sum(1 for row in r.json().get("scenarios", []) if row.get("origin") == "from_traffic")


def forward_case(case: dict, client: Optional[httpx.Client] = None) -> dict:
    """Apply the capture policy to a writer-turn case and forward survivors
    to skillforge. Returns skillforge's `{captured, scenario_id, ...}` or a
    local `{captured: False, reason}`. Raises httpx errors upward; the route
    catches them."""
    eval_svc = settings.skillforge_eval_svc_url.rstrip("/")
    if not eval_svc:
        return {"captured": False, "reason": "SKILLFORGE_EVAL_SVC_URL unset"}

    tunable_id = str(case.get("tunable_id") or MENU_TUNABLE_ID)
    question = str(case.get("question") or "").strip()
    transcript = str(case.get("transcript") or "").strip()
    selected = {str(s) for s in (case.get("selected") or [])}
    authored = [str(a) for a in (case.get("authored") or []) if str(a) in GUIDE_TREE]
    outcome = case.get("outcome")
    is_success = outcome is not None and float(outcome) > 0.0

    if is_success:
        # The revealed RIGHT pick: it opened the guide and authored its fence.
        labeled = sorted(a for a in authored if a in selected)
        origin = "from_traffic"
        rubric_shape = (
            "captured from a real successful turn: the menu steered this "
            "request to open '{g}' before drawing — a candidate menu must "
            "keep steering it there"
        )
    else:
        # The failure is the modality it drew WITHOUT opening — the guide the
        # menu should have steered it to open first.
        labeled = sorted(a for a in authored if a not in selected)
        origin = "from_failure"
        rubric_shape = (
            "captured from a real turn: the writer authored '{g}' without "
            "opening it — the menu must steer this request to open '{g}' "
            "before drawing"
        )
    if not question or not labeled:
        return {"captured": False, "reason": "not a replayable menu case"}
    expect = labeled[0]

    own_client = client is None
    if own_client:
        client = httpx.Client(trust_env=False, timeout=_TIMEOUT_S)
    try:
        if is_success:
            if random.random() >= _SUCCESS_SAMPLE_RATE:
                return {"captured": False, "reason": "success sampled out"}
            if _count_traffic_rows(eval_svc, tunable_id, client) >= _SUCCESS_CAP:
                return {"captured": False, "reason": "success cap reached"}

        spec = {
            "input": question[:_QUESTION_MAX],
            "transcript": transcript[:_TRANSCRIPT_MAX],
            "expect_guide": expect,
            "rubric": [rubric_shape.format(g=expect)],
        }
        r = client.post(
            f"{eval_svc}/api/eval/{settings.skillforge_host}/{tunable_id}/capture",
            json={
                "spec": spec,
                "correlation_id": case.get("correlation_id"),
                "outcome": float(outcome) if outcome is not None else 0.0,
                "guard": False,
                "origin": origin,
            },
        )
        r.raise_for_status()
        return r.json()
    finally:
        if own_client:
            client.close()


__all__ = ["forward_case"]
