"""Contract tests for the tuning sidecar's HTTP surface (FastAPI TestClient).

Registration and the scorer are monkeypatched — startup must never reach a
live skillforge, and /eval must never burn a real LLM call.
"""
from fastapi.testclient import TestClient

from services.tuning import main as tuning_main


def _quiet_registration(monkeypatch, result=None):
    monkeypatch.setattr(
        tuning_main.registration, "register",
        lambda client=None: result or {"skipped": True, "reason": "test"},
    )


def test_health_shape(monkeypatch):
    _quiet_registration(monkeypatch)
    with TestClient(tuning_main.app) as c:
        body = c.get("/health").json()
    assert body == {
        "status": "ok",
        "host": "beWithMe",
        "tunable": "skill_menu.canvas_guides",
    }


def test_eval_delegates_to_scorer_verbatim(monkeypatch):
    _quiet_registration(monkeypatch)
    calls = {}

    async def _fake_score(*, body, config, scenario):
        calls.update(body=body, config=config, scenario=scenario)
        return {"ok": True, "quality": 0.75, "outcome": 0.75}

    monkeypatch.setattr(tuning_main.scorer, "score", _fake_score)
    with TestClient(tuning_main.app) as c:
        r = c.post("/eval", json={
            "body": "candidate lead-in",
            "config": {"select_prompt": "x"},
            "scenario": {"input": "plot it", "expect_guide": "plot"},
        })
    assert r.json() == {"ok": True, "quality": 0.75, "outcome": 0.75}
    assert calls["body"] == "candidate lead-in"
    assert calls["scenario"]["expect_guide"] == "plot"


def test_eval_defaults_missing_fields(monkeypatch):
    _quiet_registration(monkeypatch)
    calls = {}

    async def _fake_score(*, body, config, scenario):
        calls.update(body=body, config=config, scenario=scenario)
        return {"ok": False, "quality": 0.0, "outcome": 0.0}

    monkeypatch.setattr(tuning_main.scorer, "score", _fake_score)
    with TestClient(tuning_main.app) as c:
        c.post("/eval", json={})
    assert calls == {"body": "", "config": {}, "scenario": {}}


def test_register_endpoint_surfaces_summary_and_errors(monkeypatch):
    _quiet_registration(monkeypatch, result={"skipped": False, "scenarios_added": 2})
    with TestClient(tuning_main.app) as c:
        assert c.post("/register").json() == {"skipped": False, "scenarios_added": 2}

    def _boom(client=None):
        raise RuntimeError("skillforge down")

    monkeypatch.setattr(tuning_main.registration, "register", _boom)
    with TestClient(tuning_main.app) as c:
        body = c.post("/register").json()
    assert body["error"] == "skillforge down"
