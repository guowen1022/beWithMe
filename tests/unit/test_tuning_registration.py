"""Idempotency tests for the tuning sidecar's skillforge self-registration.

Everything goes through an injected fake HTTP client — no live skillforge.
"""
import pytest

from infra.config import settings
from persona.teacher.prompts.canvas_guides import MENU_TUNABLE_ID
from services.tuning import registration
from services.tuning.scenarios import SCENARIOS
from services.tuning.scenarios_grid import GRID_TUNABLE_ID
from services.tuning.scenarios_grid import SCENARIOS as GRID_SCENARIOS


def _menu(out):
    """Per-tunable result for the canvas-guides tunable (what most of these
    tests are about; the sidecar now registers several)."""
    return out["tunables"][MENU_TUNABLE_ID]


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
    """Fake skillforge. The sidecar registers SEVERAL tunables through one
    client, so the fake answers per tunable: a test's `champion`/`existing_*`
    describe the tunable under test (the menu by default), and every OTHER
    tunable is answered as already fully onboarded so it stays inert and cannot
    leak into that test's assertions. Grid tests pass `tunable=GRID_TUNABLE_ID`
    to flip which one is under test."""

    def __init__(self, champion=None, existing_inputs=(), existing_rows=None,
                 tunable=MENU_TUNABLE_ID):
        self.posts = []  # (url, json, params)
        self.deletes = []
        self._champion = champion
        self._existing = list(existing_inputs)
        self._rows = existing_rows  # full remote rows, when a test needs shape control
        self._under_test = tunable

    @staticmethod
    def _tunable_of(url: str) -> str:
        for t in registration.TUNABLES:
            if t.tunable_id in url:
                return t.tunable_id
        return ""

    def _inert_rows(self, tunable_id):
        """Every scenario already present + tagged → nothing to add, no warning."""
        spec = next(t for t in registration.TUNABLES if t.tunable_id == tunable_id)
        return [
            {"id": 9000 + i,
             "spec": {"input": sc["input"], "region": "r", "split": "train"},
             "guard": False, "origin": "curated"}
            for i, sc in enumerate(spec.scenarios)
        ]

    def _scenario_rows(self, tunable_id):
        if tunable_id != self._under_test:
            return self._inert_rows(tunable_id)
        if self._rows is not None:
            return self._rows
        # default: fully tagged remote rows, mirroring what this code registers
        return [
            {"id": i, "spec": {"input": inp, "region": "plot", "split": "train"},
             "guard": False, "origin": "curated"}
            for i, inp in enumerate(self._existing)
        ]

    def get(self, url):
        tid = self._tunable_of(url)
        if url.endswith("/champion"):
            champion = self._champion if tid == self._under_test else "v1"
            return _Resp(200, {"champion_version": champion})
        if url.endswith("/scenarios"):
            return _Resp(200, {"scenarios": self._scenario_rows(tid)})
        return _Resp(404)

    def post(self, url, json=None, params=None):
        self.posts.append((url, json, params))
        return _Resp(200, {"ok": True})

    def delete(self, url, **kwargs):
        # Registration must never delete scenarios; recorded so a test can
        # assert it, rather than relying on an AttributeError.
        self.deletes.append(url)
        return _Resp(200, {"ok": True})


@pytest.fixture(autouse=True)
def _urls(monkeypatch):
    monkeypatch.setattr(settings, "skillforge_edge_url", "http://edge")
    monkeypatch.setattr(settings, "skillforge_store_url", "http://store")
    monkeypatch.setattr(settings, "skillforge_eval_svc_url", "http://evalsvc")


def _posted_paths(client, tunable=None):
    """Posted URLs. With `tunable`, only that tunable's own posts plus the
    host/publish calls shared across all of them. Note the tunable-declaration
    endpoint is one URL for every tunable (the id is in the BODY), so it is
    filtered on the payload rather than the path."""
    if tunable is None:
        return [url for url, _, _ in client.posts]
    others = [t.tunable_id for t in registration.TUNABLES if t.tunable_id != tunable]
    out = []
    for url, body, _ in client.posts:
        if any(o in url for o in others):
            continue
        if isinstance(body, dict) and body.get("tunable_id") not in (None, tunable):
            continue
        out.append(url)
    return out


def _scenario_posts(client, tunable):
    return [j for u, j, _ in client.posts
            if u.endswith("/scenarios") and tunable in u]


def test_skipped_when_urls_unset(monkeypatch):
    monkeypatch.setattr(settings, "skillforge_store_url", "")
    out = registration.register(client=_FakeClient())
    assert out["skipped"] is True


def test_fresh_store_full_sequence():
    client = _FakeClient(champion=None, existing_inputs=())
    out = registration.register(client=client)

    assert out["skipped"] is False
    assert _menu(out)["tunable_created"] is True
    assert _menu(out)["scenarios_added"] == len(SCENARIOS)
    assert out["published"] is True
    assert out["eval_url"].endswith("/eval")

    paths = _posted_paths(client, MENU_TUNABLE_ID)
    assert paths[0] == "http://edge/api/hosts/register"
    assert "http://store/api/tunables" in paths
    assert any(p.endswith("/variants") for p in paths)
    assert any(p.endswith("/enabled") for p in paths)
    assert sum(1 for p in paths if p.endswith("/scenarios")) == len(SCENARIOS)
    assert paths[-1] == "http://edge/api/snapshot/publish"

    # the baseline variant registers DEFAULT-OFF with select_prompt mirrored
    variant = next(j for u, j, _ in client.posts
                   if u.endswith("/variants") and MENU_TUNABLE_ID in u)
    assert variant["config"]["select_prompt"] == variant["body"]
    enabled = next(j for u, j, _ in client.posts
                   if u.endswith("/enabled") and MENU_TUNABLE_ID in u)
    assert enabled == {"enabled": False}
    # guard flag survives the trip; spec carries everything but `guard`
    posts = _scenario_posts(client, MENU_TUNABLE_ID)
    assert len([j for j in posts if j["guard"]]) == sum(
        1 for sc in SCENARIOS if sc.get("guard"))
    assert all("guard" not in j["spec"] for j in posts)


def test_scenario_specs_carry_region_and_split():
    # The spec is forwarded whole minus `guard`, so the anti-overfitting
    # labels need no plumbing of their own — but they must actually land in
    # the POST body, and guard rows must stay on the gate side.
    client = _FakeClient(champion=None, existing_inputs=())
    registration.register(client=client)

    posts = _scenario_posts(client, MENU_TUNABLE_ID)
    specs = [j["spec"] for j in posts]
    assert len(specs) == len(SCENARIOS)
    for spec in specs:
        assert spec["region"] == spec["expect_guide"]
        assert spec["split"] in ("train", "holdout")

    by_split = lambda s: {sp["region"] for sp in specs if sp["split"] == s}
    assert by_split("train") == by_split("holdout") == {"plot", "mermaid"}

    guard_specs = [j["spec"] for j in posts if j["guard"]]
    assert guard_specs and all(sp["split"] == "holdout" for sp in guard_specs)


def test_existing_tunable_and_scenarios_only_upserts_host():
    client = _FakeClient(
        champion="v3", existing_inputs=[sc["input"] for sc in SCENARIOS],
    )
    out = registration.register(client=client)

    assert _menu(out)["tunable_created"] is False
    assert _menu(out)["scenarios_added"] == 0
    paths = _posted_paths(client, MENU_TUNABLE_ID)
    # host upsert + tunable declaration + snapshot publish. The tunable POST is
    # deliberately unconditional (it is what carries oracle_regime to an
    # already-onboarded tunable) and is an upsert server-side; the VARIANT and
    # ENABLED posts are the ones that must never repeat.
    assert paths == [
        "http://edge/api/hosts/register",
        "http://store/api/tunables",
        "http://edge/api/snapshot/publish",
    ]
    assert not any(p.endswith("/variants") or p.endswith("/enabled") for p in paths)


def test_partial_scenarios_added():
    client = _FakeClient(
        champion="v1", existing_inputs=[SCENARIOS[0]["input"], SCENARIOS[1]["input"]],
    )
    out = registration.register(client=client)
    assert _menu(out)["scenarios_added"] == len(SCENARIOS) - 2


# ---------------------------------------------------------------- oracle_regime


def test_oracle_regime_declared_exactly_validate():
    # skillforge treats an unrecognized regime string as non-gated, so a typo
    # here silently restores auto-promotion. Pin the exact spelling.
    assert registration._ORACLE_REGIME == "validate"


def test_tunable_registration_carries_oracle_regime():
    client = _FakeClient(champion=None, existing_inputs=())
    out = registration.register(client=client)

    bodies = [j for u, j, _ in client.posts if u == "http://store/api/tunables"]
    menu_body = next(b for b in bodies if b["tunable_id"] == MENU_TUNABLE_ID)
    assert menu_body["oracle_regime"] == "validate"
    assert _menu(out)["oracle_regime"] == "validate"
    # every tunable declares a regime — a new one must not default to
    # `reference` (auto-promote) by omission
    assert all(b.get("oracle_regime") == "validate" for b in bodies)


def test_oracle_regime_declared_even_when_tunable_already_has_champion():
    # The regression this guards: nesting the tunable POST under `if not
    # champion` makes the regime declaration dead code for every
    # already-onboarded tunable — including the live one — which would keep
    # auto-promoting under the `reference` default. It must go out every boot.
    client = _FakeClient(champion="v7", existing_inputs=())
    registration.register(client=client)

    bodies = [j for u, j, _ in client.posts
              if u == "http://store/api/tunables" and j["tunable_id"] == MENU_TUNABLE_ID]
    assert len(bodies) == 1
    assert bodies[0]["oracle_regime"] == "validate"


# ------------------------------------------------------- partial-tagging warning


def _untagged_row(i, inp):
    """A row as registered before region/split existed."""
    return {"id": i, "spec": {"input": inp}, "guard": False, "origin": "curated"}


def test_warns_when_remote_rows_lack_labels(capsys):
    rows = [_untagged_row(101, SCENARIOS[0]["input"]),
            _untagged_row(102, SCENARIOS[1]["input"])]
    client = _FakeClient(champion="v1", existing_rows=rows)
    out = registration.register(client=client)

    err = capsys.readouterr().out
    assert "WARNING" in err
    assert "101" in err and "102" in err          # names the affected ids
    assert "DELETE" in err                        # states the remedy
    assert [u["id"] for u in _menu(out)["scenarios_untagged"]] == [101, 102]


def test_no_warning_when_remote_set_is_fully_tagged(capsys):
    client = _FakeClient(
        champion="v1", existing_inputs=[sc["input"] for sc in SCENARIOS],
    )
    out = registration.register(client=client)

    assert _menu(out)["scenarios_untagged"] == []
    assert "WARNING" not in capsys.readouterr().out


def test_warns_when_only_one_label_field_is_missing(capsys):
    rows = [{"id": 7, "spec": {"input": "x", "region": "plot"}}]  # no split
    client = _FakeClient(champion="v1", existing_rows=rows)
    out = registration.register(client=client)

    assert _menu(out)["scenarios_untagged"] == [
        {"id": 7, "input": "x", "missing": ["split"]}
    ]
    assert "WARNING" in capsys.readouterr().out


def test_no_warning_on_an_empty_remote_set(capsys):
    client = _FakeClient(champion="v1", existing_rows=[])
    out = registration.register(client=client)
    assert _menu(out)["scenarios_untagged"] == []
    assert "WARNING" not in capsys.readouterr().out


def test_check_is_fail_open_on_unexpected_row_shape():
    # Registration must still succeed if the store hands back rows the check
    # cannot read — a diagnostic is never allowed to block boot.
    client = _FakeClient(champion="v1", existing_rows=["not-a-dict", 42, None])
    out = registration.register(client=client)

    assert out["skipped"] is False
    assert out["published"] is True
    assert _menu(out)["scenarios_untagged"] == []
    assert _menu(out)["scenarios_added"] == len(SCENARIOS)


def test_check_never_deletes(capsys):
    # Warn-only is the whole point: captured rows are real production failures.
    rows = [_untagged_row(1, "a"), _untagged_row(2, "b")]
    client = _FakeClient(champion="v1", existing_rows=rows)
    registration.register(client=client)

    assert client.deletes == []
    assert "WARNING" in capsys.readouterr().out
