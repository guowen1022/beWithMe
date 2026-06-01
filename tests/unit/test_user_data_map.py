"""Guard test for the user-data map (``infra/user_data.py``).

This is the drift enforcement that makes "where does this user's data live"
stay complete as the codebase grows. It fails CI whenever new user data is
added without being recorded in the map:

  * a new DB table that holds rows for a user but isn't discoverable, and
  * a new ``data/<name>/<user_id>/`` disk root that isn't registered.

When this test fails, you have two honest choices: wire the new store into the
map (a ``user_id`` column for tables / :func:`register_user_dir` for dirs), or
add it to the allowlist below with a comment explaining why it is *not*
per-user. Either way the decision is explicit and reviewable.
"""
from __future__ import annotations

import re
from pathlib import Path

from infra.db import Base
from infra.user_data import (
    load_domains,
    registered_dirs,
    user_scoped_tables,
)

_ROOT = Path(__file__).resolve().parents[2]

# Source trees that may construct data/<name> paths.
_SCAN_DIRS = ("infra", "silicon_brain", "persona", "services", "workshop", "agents", "tools", "scripts")

# data/<name> roots that are deliberately NOT per-user (so not in the purge map).
# Each must stay justified — these hold shared, global, or content-addressed data.
_NON_USER_DIRS = {
    "browser_profile",  # shared Chromium profile, not partitioned by user
    "diagrams",         # content-addressed by sha256, deduped across users
    "per-host-skills",  # global per-website navigation notes, not per-user
}

# Tables on the shared Base that legitimately have no `user_id` of their own.
_NON_USER_TABLES = {
    "users",            # the anchor; keyed by `id`, handled explicitly
    "document_chunks",  # cascades from documents (ON DELETE CASCADE), no user_id
}

# user-scoped tables whose `user_id` lacks an ON DELETE CASCADE foreign key.
# Per-table purge covers them anyway; listed so the gap is conscious, not silent.
# Empty today — note_chunks gained its FK (scripts/init_db.py MIGRATE).
_NO_CASCADE_OK: set[str] = set()

# data/<name> and "data" / "name" path literals in source.
_DATA_PATH = re.compile(r'"data"\s*/\s*"([\w-]+)"|"data/([\w-]+)')


def _scan_data_dir_names() -> set[str]:
    names: set[str] = set()
    for d in _SCAN_DIRS:
        for path in (_ROOT / d).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for m in _DATA_PATH.finditer(path.read_text(encoding="utf-8")):
                names.add(m.group(1) or m.group(2))
    return names


def test_every_data_dir_is_registered_or_allowlisted():
    load_domains()
    registered = {d.name for d in registered_dirs()}
    found = _scan_data_dir_names()
    unclassified = found - registered - _NON_USER_DIRS
    assert not unclassified, (
        f"data/ roots used in source but not in the user-data map: {sorted(unclassified)}. "
        "Register per-user ones via infra.user_data.register_user_dir(), or add "
        "non-user ones to _NON_USER_DIRS with a justification."
    )


def test_every_table_is_classified():
    load_domains()
    unclassified = [
        t.name for t in Base.metadata.tables.values()
        if "user_id" not in t.c and t.name not in _NON_USER_TABLES
    ]
    assert not unclassified, (
        f"tables on the shared Base with no user_id and not allowlisted: {sorted(unclassified)}. "
        "Add a user_id column (so purge covers them) or add to _NON_USER_TABLES with a reason."
    )


def test_user_scoped_tables_cover_expected():
    load_domains()
    names = {t.name for t in user_scoped_tables()}
    expected = {
        "users", "profile", "user_preferences", "note_chunks", "devices",
        "documents", "canvas_layout", "interactions", "session_summaries",
        "learning_goals", "recommendations", "teacher_preference_model",
        "concept_nodes", "concept_edges",
    }
    assert expected <= names, f"purge map is missing tables: {sorted(expected - names)}"


def test_user_id_columns_cascade_or_allowlisted():
    """Each user_id should ON DELETE CASCADE from users, or be a known exception."""
    load_domains()
    missing = []
    for t in user_scoped_tables():
        if t.name == "users" or "user_id" not in t.c:
            continue
        cascades = any(fk.ondelete == "CASCADE" for fk in t.c.user_id.foreign_keys)
        if not cascades and t.name not in _NO_CASCADE_OK:
            missing.append(t.name)
    assert not missing, (
        f"user_id without ON DELETE CASCADE (and not in _NO_CASCADE_OK): {sorted(missing)}"
    )
