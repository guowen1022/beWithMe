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

All HTTP goes through a `trust_env=False` client: the dev proxy must never
see (or cache) localhost skillforge traffic — same rule as
infra/skillforge_client.py. Any failure raises; the caller decides whether
that's fatal (the startup hook logs and serves anyway — registration is
fail-open, only GATING is fail-closed).
"""
from __future__ import annotations

from typing import Optional

import httpx

from infra.config import settings
from infra.topology import upstream_url
from persona.teacher.prompts.canvas_guides import MENU_TUNABLE_ID, _MENU_PREAMBLE
from services.tuning.scenarios import SCENARIOS

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
        # 1. host upsert — re-points the live eval_url at this process.
        client.post(
            f"{edge}/api/hosts/register",
            json={"host": host, "eval_url": eval_url,
                  "meta": {"tunable": MENU_TUNABLE_ID}},
        ).raise_for_status()

        # 2. tunable declaration — UNCONDITIONAL, unlike 2b below. skillforge's
        #    register_tunable is an upsert that only tightens `oracle_regime` on
        #    an existing row (champion, enabled and description are left alone),
        #    so re-declaring every boot is safe and idempotent. It has to be
        #    unconditional: our tunable already has a champion in the live
        #    store, so anything nested under `if not champion` would never run
        #    again — the regime declaration would be dead code and the tunable
        #    would keep auto-promoting under the `reference` default.
        client.post(
            f"{store}/api/tunables",
            json={"host": host, "tunable_id": MENU_TUNABLE_ID,
                  "kind": "selection", "description": _TUNABLE_DESCRIPTION,
                  "oracle_regime": _ORACLE_REGIME},
        ).raise_for_status()

        # 2b. v1 baseline + default-OFF — first-time onboarding only.
        r = client.get(f"{store}/api/tunables/{host}/{MENU_TUNABLE_ID}/champion")
        champion = r.json().get("champion_version") if r.status_code == 200 else None
        tunable_created = False
        if not champion:
            client.post(
                f"{store}/api/tunables/{host}/{MENU_TUNABLE_ID}/variants",
                json={"body": _MENU_PREAMBLE,
                      "config": {"select_prompt": _MENU_PREAMBLE},
                      "origin": "human"},
            ).raise_for_status()
            client.post(
                f"{store}/api/tunables/{host}/{MENU_TUNABLE_ID}/enabled",
                json={"enabled": False},
            ).raise_for_status()
            tunable_created = True

        # 3. scenarios, deduped by input.
        r = client.get(f"{eval_svc}/api/eval/{host}/{MENU_TUNABLE_ID}/scenarios")
        r.raise_for_status()
        existing = {
            (row.get("spec") or {}).get("input")
            for row in r.json().get("scenarios", [])
        }
        added = 0
        for sc in SCENARIOS:
            if sc["input"] in existing:
                continue
            spec = {k: v for k, v in sc.items() if k != "guard"}
            client.post(
                f"{eval_svc}/api/eval/{host}/{MENU_TUNABLE_ID}/scenarios",
                json={"spec": spec, "guard": bool(sc.get("guard")),
                      "origin": "curated"},
            ).raise_for_status()
            added += 1

        # 4. publish — nothing above is served until it lands in the snapshot.
        client.post(
            f"{edge}/api/snapshot/publish", params={"host": host}
        ).raise_for_status()

        return {
            "skipped": False,
            "host": host,
            "eval_url": eval_url,
            "tunable_created": tunable_created,
            "oracle_regime": _ORACLE_REGIME,
            "scenarios_added": added,
            "published": True,
        }
    finally:
        if own_client:
            client.close()


__all__ = ["register", "_ORACLE_REGIME"]
