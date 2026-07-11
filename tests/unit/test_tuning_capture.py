"""Hermetic tests for the real-traffic capture path:
writer → skillforge_client.capture_case → tuning sidecar /capture (policy:
failures always, successes sampled + capped) → skillforge M8 (faked here).
"""
import pytest
from fastapi.testclient import TestClient

from infra import skillforge_client as sf
from infra.config import settings
from services.tuning import capture
from services.tuning import main as tuning_main


# ---- adapter side: capture_case ----------------------------------------------

def test_capture_case_noop_when_disabled(monkeypatch):
    fired = []
    monkeypatch.setattr(sf, "_capture_post", fired.append)
    sf.capture_case("skill_menu.canvas_guides", {"question": "q"})
    assert fired == []  # adapter reset to DEFAULT-OFF by the autouse fixture


def test_capture_case_posts_case_with_tunable(monkeypatch):
    sf._set_for_test("http://edge", {})
    fired = []
    monkeypatch.setattr(sf, "_capture_post", fired.append)
    sf.capture_case(
        "skill_menu.canvas_guides",
        {"question": "q", "authored": ["plot"], "outcome": 0.0, "correlation_id": "c1"},
    )
    assert fired == [{
        "tunable_id": "skill_menu.canvas_guides",
        "question": "q",
        "authored": ["plot"],
        "outcome": 0.0,
        "correlation_id": "c1",
    }]


# ---- sidecar side: forward_case ------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, traffic_rows=0):
        self.posts = []
        self.gets = []
        self._traffic_rows = traffic_rows

    def get(self, url):
        self.gets.append(url)
        rows = [{"id": i, "spec": {"input": f"t{i}"}, "guard": False,
                 "origin": "from_traffic"} for i in range(self._traffic_rows)]
        rows.append({"id": 99, "spec": {"input": "x"}, "guard": True, "origin": "curated"})
        return _Resp({"scenarios": rows})

    def post(self, url, json=None):
        self.posts.append((url, json))
        return _Resp({"captured": True, "scenario_id": 9,
                      "origin": json["origin"]})


@pytest.fixture(autouse=True)
def _eval_svc_url(monkeypatch):
    monkeypatch.setattr(settings, "skillforge_eval_svc_url", "http://evalsvc")


def _failure(**over):
    case = {
        "question": "sketch the shape of a damped oscillation",
        "transcript": "The swing loses a bit of height every cycle.",
        "selected": ["mermaid"],
        "authored": ["plot"],
        "outcome": 0.0,
        "correlation_id": "corr-1",
    }
    case.update(over)
    return case


def _success(**over):
    case = {
        "question": "flowchart of user signup",
        "transcript": "Signup runs email, verify, profile.",
        "selected": ["mermaid"],
        "authored": ["mermaid"],
        "outcome": 1.0,
        "correlation_id": "corr-2",
    }
    case.update(over)
    return case


def test_failure_always_captured_as_from_failure():
    client = _FakeClient()
    out = capture.forward_case(_failure(), client=client)
    assert out["captured"] is True

    url, body = client.posts[0]
    assert url == "http://evalsvc/api/eval/beWithMe/skill_menu.canvas_guides/capture"
    assert body["origin"] == "from_failure"
    assert body["outcome"] == 0.0 and body["guard"] is False
    assert body["correlation_id"] == "corr-1"
    spec = body["spec"]
    # expect_guide = the modality authored WITHOUT being opened
    assert spec["expect_guide"] == "plot"
    assert "without" in spec["rubric"][0]
    assert client.gets == []  # failures skip the sampling/cap machinery


def test_failure_picks_unopened_modality():
    client = _FakeClient()
    capture.forward_case(
        _failure(selected=["plot"], authored=["plot", "mermaid"]), client=client)
    assert client.posts[0][1]["spec"]["expect_guide"] == "mermaid"


def test_success_sampled_in_captured_as_from_traffic(monkeypatch):
    monkeypatch.setattr(capture.random, "random", lambda: 0.0)  # sampled IN
    client = _FakeClient(traffic_rows=3)
    out = capture.forward_case(_success(), client=client)
    assert out["captured"] is True and out["origin"] == "from_traffic"

    body = client.posts[0][1]
    assert body["origin"] == "from_traffic" and body["outcome"] == 1.0
    spec = body["spec"]
    # expect_guide = the modality it opened AND authored (revealed right pick)
    assert spec["expect_guide"] == "mermaid"
    assert "keep steering" in spec["rubric"][0]


def test_success_sampled_out(monkeypatch):
    monkeypatch.setattr(capture.random, "random", lambda: 0.99)
    client = _FakeClient()
    out = capture.forward_case(_success(), client=client)
    assert out == {"captured": False, "reason": "success sampled out"}
    assert client.posts == [] and client.gets == []  # no cap check either


def test_success_cap_reached(monkeypatch):
    monkeypatch.setattr(capture.random, "random", lambda: 0.0)
    client = _FakeClient(traffic_rows=capture._SUCCESS_CAP)
    out = capture.forward_case(_success(), client=client)
    assert out == {"captured": False, "reason": "success cap reached"}
    assert client.posts == []


def test_rejects_non_replayable_cases():
    client = _FakeClient()
    # failure shape but authored ⊆ selected — nothing mis-steered
    out = capture.forward_case(
        _failure(selected=["plot"], authored=["plot"]), client=client)
    assert out["captured"] is False and client.posts == []
    # no question
    out = capture.forward_case(_failure(question=""), client=client)
    assert out["captured"] is False and client.posts == []
    # authored modality unknown to the registry
    out = capture.forward_case(_failure(authored=["sculpture"]), client=client)
    assert out["captured"] is False and client.posts == []


def test_truncates_content():
    client = _FakeClient()
    capture.forward_case(
        _failure(question="q" * 5000, transcript="t" * 9000), client=client)
    spec = client.posts[0][1]["spec"]
    assert len(spec["input"]) == 2000 and len(spec["transcript"]) == 4000


def test_skipped_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "skillforge_eval_svc_url", "")
    client = _FakeClient()
    out = capture.forward_case(_failure(), client=client)
    assert out["captured"] is False and client.posts == []


# ---- endpoint ------------------------------------------------------------------

def _quiet_registration(monkeypatch):
    monkeypatch.setattr(
        tuning_main.registration, "register",
        lambda client=None: {"skipped": True, "reason": "test"},
    )


def test_capture_endpoint_delegates_and_fails_open(monkeypatch):
    _quiet_registration(monkeypatch)
    seen = []

    def _fake_forward(case, client=None):
        seen.append(case)
        return {"captured": True, "scenario_id": 3}

    monkeypatch.setattr(tuning_main.capture, "forward_case", _fake_forward)
    with TestClient(tuning_main.app) as c:
        assert c.post("/capture", json=_failure()).json() == {
            "captured": True, "scenario_id": 3}
    assert seen[0]["question"].startswith("sketch")

    def _boom(case, client=None):
        raise RuntimeError("skillforge down")

    monkeypatch.setattr(tuning_main.capture, "forward_case", _boom)
    with TestClient(tuning_main.app) as c:
        body = c.post("/capture", json=_failure()).json()
    assert body["captured"] is False and "skillforge down" in body["error"]
