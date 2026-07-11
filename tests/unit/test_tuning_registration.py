"""Idempotency tests for the tuning sidecar's skillforge self-registration.

Everything goes through an injected fake HTTP client — no live skillforge.
"""
import pytest

from infra.config import settings
from services.tuning import registration
from services.tuning.scenarios import SCENARIOS


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, champion=None, existing_inputs=()):
        self.posts = []  # (url, json, params)
        self._champion = champion
        self._existing = list(existing_inputs)

    def get(self, url):
        if url.endswith("/champion"):
            return _Resp(200, {"champion_version": self._champion})
        if url.endswith("/scenarios"):
            return _Resp(200, {"scenarios": [
                {"id": i, "spec": {"input": inp}, "guard": False, "origin": "curated"}
                for i, inp in enumerate(self._existing)
            ]})
        return _Resp(404)

    def post(self, url, json=None, params=None):
        self.posts.append((url, json, params))
        return _Resp(200, {"ok": True})


@pytest.fixture(autouse=True)
def _urls(monkeypatch):
    monkeypatch.setattr(settings, "skillforge_edge_url", "http://edge")
    monkeypatch.setattr(settings, "skillforge_store_url", "http://store")
    monkeypatch.setattr(settings, "skillforge_eval_svc_url", "http://evalsvc")


def _posted_paths(client):
    return [url for url, _, _ in client.posts]


def test_skipped_when_urls_unset(monkeypatch):
    monkeypatch.setattr(settings, "skillforge_store_url", "")
    out = registration.register(client=_FakeClient())
    assert out["skipped"] is True


def test_fresh_store_full_sequence():
    client = _FakeClient(champion=None, existing_inputs=())
    out = registration.register(client=client)

    assert out["skipped"] is False
    assert out["tunable_created"] is True
    assert out["scenarios_added"] == len(SCENARIOS)
    assert out["published"] is True
    assert out["eval_url"].endswith("/eval")

    paths = _posted_paths(client)
    assert paths[0] == "http://edge/api/hosts/register"
    assert "http://store/api/tunables" in paths
    assert any(p.endswith("/variants") for p in paths)
    assert any(p.endswith("/enabled") for p in paths)
    assert sum(1 for p in paths if p.endswith("/scenarios")) == len(SCENARIOS)
    assert paths[-1] == "http://edge/api/snapshot/publish"

    # the baseline variant registers DEFAULT-OFF with select_prompt mirrored
    variant = next(j for u, j, _ in client.posts if u.endswith("/variants"))
    assert variant["config"]["select_prompt"] == variant["body"]
    enabled = next(j for u, j, _ in client.posts if u.endswith("/enabled"))
    assert enabled == {"enabled": False}
    # guard flag survives the trip; spec carries everything but `guard`
    guard_posts = [j for u, j, _ in client.posts if u.endswith("/scenarios") and j["guard"]]
    assert len(guard_posts) == sum(1 for sc in SCENARIOS if sc.get("guard"))
    assert all("guard" not in j["spec"] for u, j, _ in client.posts if u.endswith("/scenarios"))


def test_existing_tunable_and_scenarios_only_upserts_host():
    client = _FakeClient(
        champion="v3", existing_inputs=[sc["input"] for sc in SCENARIOS],
    )
    out = registration.register(client=client)

    assert out["tunable_created"] is False
    assert out["scenarios_added"] == 0
    paths = _posted_paths(client)
    # exactly: host upsert + snapshot publish — nothing duplicated
    assert paths == ["http://edge/api/hosts/register", "http://edge/api/snapshot/publish"]


def test_partial_scenarios_added():
    client = _FakeClient(
        champion="v1", existing_inputs=[SCENARIOS[0]["input"], SCENARIOS[1]["input"]],
    )
    out = registration.register(client=client)
    assert out["scenarios_added"] == len(SCENARIOS) - 2
