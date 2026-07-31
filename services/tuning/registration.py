"""Idempotent self-registration with the local skillforge instance.

Runs on every sidecar boot (and via POST /register). Safe to repeat:

  1. host + eval_url        — server-side upsert; every boot re-points the
                              live record at THIS process (fixes any stale
                              placeholder from earlier onboarding demos);
  2. tunable declaration    — upserted on EVERY boot; this is what carries
                              `oracle_regime` (see _ORACLE_REGIME below);
  2b. v1 baseline + off     — seeded only when the tunable has no champion
                              yet (skillforge's own reference host re-adds a
                              variant per boot; ours must not);
  3. scenarios              — deduped by spec["input"] against what the store
                              already holds (the store never dedups server-side);
  4. snapshot publish       — makes 1–3 visible to resolve().

Scenario `spec` is forwarded whole minus `guard`, so the `region`/`split`
labels added in `scenarios.py` reach the store with no plumbing of their own.

**Re-registration caveat (2026-07-19).** Dedup is by `spec["input"]` and the
eval service exposes only add/delete — there is NO update endpoint. Rows
registered before region/split existed (the live dev store holds 10 such) will
therefore be skipped by the dedup on every boot and keep serving UNLABELED
forever. Booting this code against that store adds nothing and fixes nothing.
Re-labeling them is a deliberate operator action: delete the untagged rows,
then re-register. Until that happens, expect a mix of labeled and unlabeled
scenarios, and treat unlabeled rows as whatever default partition skillforge
assigns them — not as a silent member of any region.
`_warn_on_partial_tagging` now makes that state LOUD on every boot instead of
leaving it to be rediscovered; it never deletes anything.

All HTTP goes through a `trust_env=False` client: the dev proxy must never
see (or cache) localhost skillforge traffic — same rule as
infra/skillforge_client.py. Any failure raises; the caller decides whether
that's fatal (the startup hook logs and serves anyway — registration is
fail-open, only GATING is fail-closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from infra.config import settings
from infra.topology import upstream_url
from persona.teacher.prompts.canvas_guides import MENU_TUNABLE_ID, _MENU_PREAMBLE
from services.tuning.scenarios import SCENARIOS
from services.tuning.scenarios_grid import GRID_TUNABLE_ID
from services.tuning.scenarios_grid import SCENARIOS as GRID_SCENARIOS
from workshop.canvas.tools.present_coordinate_grid import _DESCRIPTION as _GRID_BASELINE_BODY

_TIMEOUT_S = 10.0

_TUNABLE_DESCRIPTION = (
    "Canvas-writer visual-guide menu: which guides are offered, in what "
    "order, with what summaries and lead-in (select_prompt)."
)

# skillforge's promotion switch (INTEGRATION.md, "oracle_regime"): `verify` and
# `reference` let a gate-passing candidate auto-promote; `validate` sends every
# one to the human review queue instead.
#
# `validate` here is a deliberate call, NOT boilerplate. This tunable's
# `quality` is produced by an LLM judge scoring how well a candidate menu
# STEERS the canvas writer — pedagogical taste, which a judge only
# approximates. We measured that judge's run-to-run variance against the live
# model (identical body/config/scenario, repeated calls): p(deviate) ran from
# 0.00 on the clear-cut scenarios to 0.33 on the borderline ones. A signal that
# can land differently a third of the time on the very scenarios that carry the
# refinement headroom is a PROXY for what we care about, not ground truth for
# it — promoting a champion automatically on it is not defensible. `validate`
# keeps the loop proposing, evaluating and gating exactly as before; it only
# puts a person on the final promotion.
#
# Revisit only if the outcome signal stops being a judgement call (e.g. a
# mechanical steering check replaces the judge), not because the queue is slow.
#
# Spell this EXACTLY: skillforge treats an unrecognized regime string as
# non-gated, so a typo silently buys back the auto-promotion we are declining.
_ORACLE_REGIME = "validate"

_GRID_DESCRIPTION = (
    "present_coordinate_grid's tool description: the contract the teacher "
    "reads when deciding whether to reach for an animated grid and how to "
    "fill its spec."
)

# Fields every scenario spec must carry for region-aware gating.
_LABEL_FIELDS = ("region", "split")


def _warn_on_partial_tagging(remote_rows: list, local_tagged: bool) -> list:
    """Surface remote scenarios that predate the region/split labels.

    A PARTLY-tagged set is worse than an untagged one: skillforge files the
    untagged remainder under the `"*"` region next to the real ones, where it
    reads as a legitimate slice and quietly skews per-region coverage. Because
    registration dedups by `spec["input"]` and the eval service exposes only
    add/delete (no update endpoint), those rows are skipped on every boot and
    stay untagged forever — nothing else in the system says so out loud.

    Warn only, never delete. The untagged rows include captured production
    failures, which are the only genuine headroom this loop has; throwing them
    away stays a deliberate operator action. Returns the offending rows for the
    summary dict and never raises — registration is fail-open, and a
    diagnostic must not be what blocks boot.
    """
    try:
        if not local_tagged:
            return []
        untagged = []
        for row in remote_rows:
            spec = row.get("spec") or {}
            missing = [f for f in _LABEL_FIELDS if not spec.get(f)]
            if missing:
                untagged.append({
                    "id": row.get("id"),
                    "input": str(spec.get("input") or "")[:60],
                    "missing": missing,
                })
        if not untagged:
            return []
        ids = ", ".join(str(u["id"]) for u in untagged)
        print(
            f"[tuning] WARNING: partly-tagged scenario set — {len(untagged)} of "
            f"{len(remote_rows)} scenarios already in the store are missing "
            f"region/split (ids: {ids}), while this boot registers tagged ones. "
            f"skillforge buckets untagged rows under region \"*\" alongside the "
            f"real regions, so coverage will read healthier than it is. There is "
            f"NO update endpoint: the remedy is to DELETE those ids "
            f"(DELETE /api/eval/{{host}}/{{tunable}}/scenarios) and let the next "
            f"boot re-register them tagged. Not automatic — captured rows are "
            f"real production failures, so pruning them is your call.",
            flush=True,
        )
        return untagged
    except Exception as e:  # a diagnostic must never break registration
        print(f"[tuning] partial-tagging check skipped: {e}", flush=True)
        return []


@dataclass(frozen=True)
class TunableSpec:
    """Everything registration needs to declare one tunable and seed its evals.

    Adding a tunable is adding a row to TUNABLES below — the registration steps
    themselves are identical for all of them.
    """
    tunable_id: str
    kind: str                 # skillforge Tunable kind (selection | prompt | ...)
    description: str
    oracle_regime: str
    baseline_body: str        # the v1 variant body, seeded on first onboarding
    baseline_config: dict
    scenarios: list
    # The declared contract. Names are SCENARIO SPEC KEYS, not Python parameter names —
    # skillforge checks them against `spec`, so declaring `voice_transcript` here would
    # reject every scenario that correctly carries `transcript`.
    inputs: tuple = ()
    calls: tuple = ()
    expected: tuple = ()


# Both tunables are `validate`: each one's `quality` is ultimately a judgement
# call an LLM only approximates, so a gate-passing candidate goes to human
# review rather than auto-promoting. See the _ORACLE_REGIME note above.
TUNABLES: tuple = (
    TunableSpec(
        tunable_id=MENU_TUNABLE_ID,
        kind="selection",
        description=_TUNABLE_DESCRIPTION,
        oracle_regime=_ORACLE_REGIME,
        baseline_body=_MENU_PREAMBLE,
        baseline_config={"select_prompt": _MENU_PREAMBLE},
        scenarios=SCENARIOS,
        # `transcript` is required because the writer's whole job is mirroring a spoken
        # answer onto the canvas — its prompt ends "(or do nothing if the spoken answer
        # is complete on its own)". An eval that passed "" therefore measured a writer
        # correctly declining, scored it 0.0, and recorded it as a wrong guide, for a
        # month. Declaring it makes skillforge refuse such a case before spending
        # anything, instead of returning a defensible zero.
        inputs=(
            {"name": "input", "required": True,
             "describes": "the user's request the menu must steer"},
            {"name": "transcript", "required": True,
             "describes": "the spoken answer this note mirrors"},
            {"name": "canvas_state", "required": False,
             "describes": "what is already mounted on the target device"},
            {"name": "existing_notes", "required": False,
             "describes": "cached markdown of mounted notes — enables the EDIT branch"},
            {"name": "related_notes", "required": False,
             "describes": "semantically near notes from prior teaching"},
        ),
        # A tool call is not a diagnostic: it is the boundary between two tunables, this
        # menu's output and the guide's input on one edge. `load_guide(ids)` IS what the
        # menu exists to cause, which is why ground truth is compared against it.
        calls=(
            {"name": "load_guide", "args": ["ids"],
             "describes": "which guide(s) the menu caused the writer to open"},
            {"name": "mount_template", "args": ["params.markdown"],
             "describes": "the note authored to the canvas"},
            {"name": "edit_note", "args": ["ops"],
             "describes": "edits applied to a note already on the canvas"},
        ),
        expected=(
            {"name": "expect_guide", "matches": "load_guide.ids",
             "describes": "the guide this request should have opened"},
        ),
    ),
    TunableSpec(
        tunable_id=GRID_TUNABLE_ID,
        # `prompt`, not `selection`: what's tuned is the tool DESCRIPTION the
        # teacher reads when deciding how to fill the spec — free text, not a
        # menu of options. The manifest tuning gate injects it for every
        # tunable tool (persona/teacher/tools/manifest.py).
        kind="prompt",
        description=_GRID_DESCRIPTION,
        oracle_regime=_ORACLE_REGIME,
        baseline_body=_GRID_BASELINE_BODY,
        baseline_config={"description": _GRID_BASELINE_BODY},
        scenarios=GRID_SCENARIOS,
    ),
)


def _register_one(client: httpx.Client, t: TunableSpec, *,
                  host: str, store: str, eval_svc: str) -> dict:
    """Declare one tunable, seed its baseline if new, and add missing scenarios."""
    # 2. tunable declaration — UNCONDITIONAL, unlike 2b below. skillforge's
    #    register_tunable is an upsert that only tightens `oracle_regime` on
    #    an existing row (champion, enabled and description are left alone),
    #    so re-declaring every boot is safe and idempotent. It has to be
    #    unconditional: a tunable that already has a champion in the live
    #    store would never re-run anything nested under `if not champion` —
    #    the regime declaration would be dead code and the tunable would keep
    #    auto-promoting under the `reference` default.
    #    The contract rides along on the same upsert: re-declaring in place is the point,
    #    so a decision that learns it needs another input can say so and have every stale
    #    scenario complain on the next run rather than scoring a degenerate one.
    client.post(
        f"{store}/api/tunables",
        json={"host": host, "tunable_id": t.tunable_id, "kind": t.kind,
              "description": t.description, "oracle_regime": t.oracle_regime,
              "inputs": list(t.inputs), "calls": list(t.calls),
              "expected": list(t.expected)},
    ).raise_for_status()

    # 2b. v1 baseline + default-OFF — first-time onboarding only. The baseline
    #     is byte-identical to what the code serves, so enabling a fresh
    #     tunable changes nothing until a challenger is promoted.
    r = client.get(f"{store}/api/tunables/{host}/{t.tunable_id}/champion")
    champion = r.json().get("champion_version") if r.status_code == 200 else None
    created = False
    if not champion:
        client.post(
            f"{store}/api/tunables/{host}/{t.tunable_id}/variants",
            json={"body": t.baseline_body, "config": t.baseline_config,
                  "origin": "human"},
        ).raise_for_status()
        client.post(
            f"{store}/api/tunables/{host}/{t.tunable_id}/enabled",
            json={"enabled": False},
        ).raise_for_status()
        created = True

    # 3. scenarios, deduped by input.
    r = client.get(f"{eval_svc}/api/eval/{host}/{t.tunable_id}/scenarios")
    r.raise_for_status()
    # Non-dict rows should be impossible, but a malformed store response
    # must not take registration down (fail-open) — drop them once here so
    # both the dedup and the label check below read a clean list.
    remote_rows = [row for row in (r.json().get("scenarios", []) or [])
                   if isinstance(row, dict)]
    existing = {(row.get("spec") or {}).get("input") for row in remote_rows}
    # Dedup means rows already in the store are skipped forever, so a
    # pre-label row can never be fixed by booting again — say so loudly.
    untagged = _warn_on_partial_tagging(
        remote_rows,
        local_tagged=any(any(sc.get(f) for f in _LABEL_FIELDS)
                         for sc in t.scenarios),
    )
    added = 0
    for sc in t.scenarios:
        if sc["input"] in existing:
            continue
        spec = {k: v for k, v in sc.items() if k != "guard"}
        client.post(
            f"{eval_svc}/api/eval/{host}/{t.tunable_id}/scenarios",
            json={"spec": spec, "guard": bool(sc.get("guard")),
                  "origin": "curated"},
        ).raise_for_status()
        added += 1

    return {
        "tunable_created": created,
        "oracle_regime": t.oracle_regime,
        "scenarios_added": added,
        "scenarios_untagged": untagged,
    }


def register(client: Optional[httpx.Client] = None) -> dict:
    """Register this sidecar as beWithMe's eval endpoint. Returns a summary
    dict; raises httpx errors upward. No-op when the SKILLFORGE_* URLs are
    unset (fail-open default-off, same contract as the serving adapter)."""
    edge = settings.skillforge_edge_url.rstrip("/")
    store = settings.skillforge_store_url.rstrip("/")
    eval_svc = settings.skillforge_eval_svc_url.rstrip("/")
    if not (edge and store and eval_svc):
        return {"skipped": True, "reason": "SKILLFORGE_EDGE/STORE/EVAL_SVC_URL not all set"}

    host = settings.skillforge_host
    eval_url = f"{upstream_url('tuning')}/eval"

    own_client = client is None
    if own_client:
        client = httpx.Client(trust_env=False, timeout=_TIMEOUT_S)
    try:
        # 1. host upsert — re-points the live eval_url at this process. ONE
        #    eval_url serves every tunable below; `main.py` dispatches on the
        #    `tunable_id` skillforge carries in the eval payload.
        client.post(
            f"{edge}/api/hosts/register",
            json={"host": host, "eval_url": eval_url,
                  "meta": {"tunables": [t.tunable_id for t in TUNABLES]}},
        ).raise_for_status()

        results = {t.tunable_id: _register_one(client, t, host=host,
                                               store=store, eval_svc=eval_svc)
                   for t in TUNABLES}

        # 4. publish — nothing above is served until it lands in the snapshot.
        #    Once, after every tunable: the snapshot is recomposed whole.
        client.post(
            f"{edge}/api/snapshot/publish", params={"host": host}
        ).raise_for_status()

        return {
            "skipped": False,
            "host": host,
            "eval_url": eval_url,
            "tunables": results,
            "published": True,
        }
    finally:
        if own_client:
            client.close()


__all__ = ["register", "_ORACLE_REGIME"]
