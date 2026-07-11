"""Repo-wide test fixtures."""
import pytest

from infra import skillforge_client as _sf


@pytest.fixture(autouse=True)
def _skillforge_baseline():
    """Force the skillforge adapter to its DEFAULT-OFF baseline for every test.

    `.env` points SKILLFORGE_EDGE_URL at the live local tuning instance; the
    adapter seeds its module state from it at import. Tests must stay hermetic
    and never consume whatever snapshot that instance happens to serve — a
    disabled tool or tuned description would silently shift manifest goldens.
    Tests that exercise tuning inject their own state via `_set_for_test`
    (see tests/unit/test_skillforge_gate.py).
    """
    _sf._reset_for_test()
    yield
    _sf._reset_for_test()
