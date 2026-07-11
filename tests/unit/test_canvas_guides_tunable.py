"""Unit tests for the `skill_menu.canvas_guides` selection tunable.

The canvas-writer's visual-guide menu is a skillforge selection tunable: a
bounded `resolve().config` may narrow (`offer`), reorder (`order`), relabel
(`summaries`), or re-lead (`select_prompt`) the menu — each fail-open to the
baseline. The writer's turn then reports a synchronous outcome: did the
modality it OPENED from the menu match the fence it AUTHORED.

All menu-shaping tests are pure (config passed as a dict), so they need no
skillforge running. The telemetry test uses the adapter's test seam.

See architecture-review/proposals/2026-07-09-skill-loading-framework.md
(the eng-reviewed "minimal spine") and persona/teacher/prompts/canvas_guides.py.
"""
import pytest

from infra import skillforge_client as sf
from persona.teacher.prompts import canvas_guides as cg


@pytest.fixture(autouse=True)
def _reset():
    sf._reset_for_test()
    yield
    sf._reset_for_test()


def _baseline_menu() -> str:
    lines = "\n".join(
        f"  • {i} — {cg.GUIDE_TREE[i]['summary']}" for i in cg._root_ids()
    )
    return f"{cg._MENU_PREAMBLE}\n{lines}\n{cg._MENU_FOOTER}"


# --- fail-open baseline (skillforge off / no config) -----------------------

def test_baseline_menu_byte_for_byte():
    # None (skillforge off) and {} must both reproduce today's menu exactly.
    assert cg.render_root_menu(None) == _baseline_menu()
    assert cg.render_root_menu({}) == _baseline_menu()
    assert "• plot —" in cg.render_root_menu(None)
    assert "• mermaid —" in cg.render_root_menu(None)


def test_menu_is_not_cached():
    # Rebuilt per call — a refreshed snapshot must take effect, not a memoized
    # first value (the load_skill lru_cache footgun must not reach the menu).
    a = cg.render_root_menu({"offer": ["plot"]})
    b = cg.render_root_menu({"offer": ["mermaid"]})
    assert a != b


# --- offer (subset) --------------------------------------------------------

def test_offer_narrows_and_ignores_unknown():
    m = cg.render_root_menu({"offer": ["mermaid"]})
    assert "• mermaid —" in m and "• plot —" not in m
    m2 = cg.render_root_menu({"offer": ["mermaid", "bogus"]})
    assert "• mermaid —" in m2 and "• plot —" not in m2 and "bogus" not in m2


def test_empty_or_garbage_offer_falls_back():
    base = cg.render_root_menu(None)
    assert cg.render_root_menu({"offer": []}) == base          # empty
    assert cg.render_root_menu({"offer": "plot"}) == base       # not a list
    assert cg.render_root_menu({"offer": ["nope"]}) == base     # all unknown


# --- order (permutation) ---------------------------------------------------

def test_order_permutes_and_ignores_unknown():
    m = cg.render_root_menu({"order": ["mermaid", "plot"]})
    assert m.index("• mermaid —") < m.index("• plot —")
    base = cg.render_root_menu(None)
    assert base.index("• plot —") < base.index("• mermaid —")  # baseline order
    # unknown order ids ignored; known nodes still all present
    m2 = cg.render_root_menu({"order": ["zzz", "mermaid"]})
    assert "• mermaid —" in m2 and "• plot —" in m2


# --- summaries (bounded relabel) -------------------------------------------

def test_summaries_override_is_bounded():
    m = cg.render_root_menu({"summaries": {"plot": "TUNED plot line"}})
    assert "• plot — TUNED plot line" in m
    big = "x" * (cg._SUMMARY_MAX + 1)
    assert big not in cg.render_root_menu({"summaries": {"plot": big}})
    # non-string / non-dict → baseline
    assert cg.render_root_menu({"summaries": {"plot": 123}}) == cg.render_root_menu(None)
    assert cg.render_root_menu({"summaries": "nope"}) == cg.render_root_menu(None)


# --- select_prompt (bounded lead-in) ---------------------------------------

def test_select_prompt_override_is_bounded():
    assert cg.render_root_menu({"select_prompt": "PICK ONE:"}).startswith("PICK ONE:")
    # oversize → baseline preamble
    assert cg.render_root_menu({"select_prompt": "x" * 3000}).startswith(cg._MENU_PREAMBLE)


# --- authored-modality detection -------------------------------------------

def test_authored_modalities_detects_fences():
    assert cg.authored_modalities("```plot\nx*x\n```") == {"plot"}
    assert cg.authored_modalities("```mermaid\ngraph TD\n```") == {"mermaid"}
    both = "intro\n```plot\n..\n```\nmid\n```mermaid\n..\n```\n"
    assert cg.authored_modalities(both) == {"plot", "mermaid"}
    assert cg.authored_modalities("```python\nprint(1)\n```") == set()   # other lang
    assert cg.authored_modalities("```plotly\n..\n```") == set()          # word-boundary
    assert cg.authored_modalities("") == set()
    assert cg.authored_modalities(None) == set()


# --- outcome mapping (the synchronous proxy signal) ------------------------

def test_menu_outcome_cases():
    assert cg.menu_outcome(set(), {"plot"})[0] is False              # menu unused → no emit
    assert cg.menu_outcome({"plot"}, set()) == (True, False, None)   # peek-then-prose: neutral
    assert cg.menu_outcome({"plot"}, {"plot"}) == (True, True, 1.0)  # pick paid off
    assert cg.menu_outcome({"plot"}, {"mermaid"}) == (True, True, 0.0)  # wrong modality
    assert cg.menu_outcome({"plot", "mermaid"}, {"plot"}) == (True, True, 1.0)  # subset match


# --- end-to-end at the adapter boundary ------------------------------------

def test_resolve_feeds_menu_config():
    sf._set_for_test("http://edge", {cg.MENU_TUNABLE_ID: {"config": {"offer": ["plot"]}}})
    cfg = sf.resolve(cg.MENU_TUNABLE_ID).config
    m = cg.render_root_menu(cfg)
    assert "• plot —" in m and "• mermaid —" not in m


def test_match_banks_a_win(monkeypatch):
    # Mirror the writer's emit path: a plot pick + a plot fence → outcome 1.0,
    # attributed to the version that ran.
    fired = []
    monkeypatch.setattr(sf, "collect", fired.append)
    sf._set_for_test("http://edge", {cg.MENU_TUNABLE_ID: {"version": "v7"}})

    emit, ok, scalar = cg.menu_outcome({"plot"}, cg.authored_modalities("```plot\nx\n```"))
    assert (emit, ok, scalar) == (True, True, 1.0)
    sf.collect_result(cg.MENU_TUNABLE_ID, ok=ok, outcome_scalar=scalar, variant_version="v7")

    (event,) = fired
    assert event["tunable_id"] == cg.MENU_TUNABLE_ID
    assert event["outcome_scalar"] == 1.0
    assert event["variant_version"] == "v7"
    assert event["result"]["ok"] is True


def test_menu_unused_emits_nothing(monkeypatch):
    fired = []
    monkeypatch.setattr(sf, "collect", fired.append)
    sf._set_for_test("http://edge", {cg.MENU_TUNABLE_ID: {"version": "v7"}})
    emit, ok, scalar = cg.menu_outcome(set(), cg.authored_modalities("no fence here"))
    assert emit is False
    # writer only calls collect_result when emit → nothing fires
    assert fired == []
