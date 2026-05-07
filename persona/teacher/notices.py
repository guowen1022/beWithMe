"""Notice bridge — Lane B → Lane A.

When the teacher's background lane (Lane B) finishes a task ("upload
finished — pdf_reader mounted as paper-1", "stale block unmounted after
form completed", etc.), it appends a one-line summary here. The next
Lane A reflect turn drains the deque and renders it as
`=== RECENT BACKGROUND ACTIONS ===` in the user-facing prompt, so the
teacher can naturally surface "by the way, your paper is ready" when
relevant.

In-memory only; resets on persona-sidecar restart. Talk is cheap —
notices are not promoted to silicon_brain.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List
from uuid import UUID


_MAX_NOTICES = 10
_notices: Dict[str, Deque[str]] = defaultdict(lambda: deque(maxlen=_MAX_NOTICES))


def append(user_id: UUID, text: str) -> None:
    """Push a one-line summary onto the user's notice queue.

    Empty / whitespace-only strings are dropped. Long strings are
    trimmed to 240 chars so a runaway tool result can't poison the
    next reflect prompt.
    """
    s = (text or "").strip()
    if not s:
        return
    if len(s) > 240:
        s = s[:237] + "…"
    _notices[str(user_id)].append(s)


def drain(user_id: UUID) -> List[str]:
    """Pop and return all pending notices for a user. Empties the queue."""
    key = str(user_id)
    bucket = _notices.get(key)
    if not bucket:
        return []
    out = list(bucket)
    bucket.clear()
    return out


def peek(user_id: UUID) -> List[str]:
    """Read pending notices without consuming. Tests / debug only."""
    return list(_notices.get(str(user_id), ()))


def _reset_for_tests() -> None:
    _notices.clear()
