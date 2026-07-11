"""Idempotent self-registration with the local skillforge instance.

Runs on every sidecar boot (and via POST /register). Safe to repeat:

  1. host + eval_url        — server-side upsert; every boot re-points the
                              live record at THIS process (fixes any stale
                              placeholder from earlier onboarding demos);
  2. tunable + v1 baseline  — created only when the tunable has no champion
                              yet (skillforge's own reference host re-adds a
                              variant per boot; ours must not);
  3. scenarios              — deduped by spec["input"] against what the store
                              already holds (the store never dedups server-side);
  4. snapshot publish       — makes 1–3 visible to resolve().

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

        # 2. tunable + v1 baseline + default-OFF — only when absent.
        r = client.get(f"{store}/api/tunables/{host}/{MENU_TUNABLE_ID}/champion")
        champion = r.json().get("champion_version") if r.status_code == 200 else None
        tunable_created = False
        if not champion:
            client.post(
                f"{store}/api/tunables",
                json={"host": host, "tunable_id": MENU_TUNABLE_ID,
                      "kind": "selection", "description": _TUNABLE_DESCRIPTION},
            ).raise_for_status()
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
            "scenarios_added": added,
            "published": True,
        }
    finally:
        if own_client:
            client.close()


__all__ = ["register"]
