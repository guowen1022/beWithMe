"""Unit tests for the multi-persona feed: blend, saturation, assemble,
select, and the teacher producer's mapping.

No DB / no real sidecar — fakes for the SiliconBrainClient + an in-memory
Cache. Async paths run via asyncio.run (this repo doesn't use pytest-asyncio).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from infra.contracts.feed import FeedCandidateCreate, FeedCandidateDTO
from services.maestro import blend as _blend
from services.maestro import feed as _feed
from services.maestro import saturation as _saturation
from services.maestro.cache import Cache


_NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)


def _dto(**overrides) -> FeedCandidateDTO:
    base = dict(
        id=uuid4(),
        user_id=uuid4(),
        source_persona="teacher",
        purpose="teacher:long-horizon-propose",
        posture="steady",
        title="A thread",
        opening="A short framing.",
        intra_rank=0.5,
        category="review",
        body=None,
        status="active",
        created_at=_NOW,
        selected_at=None,
        expires_at=None,
    )
    base.update(overrides)
    return FeedCandidateDTO(**base)


# --- blend -----------------------------------------------------------------


def test_blend_weight_one_preserves_intra_rank_order():
    cards = [_dto(intra_rank=0.4), _dto(intra_rank=0.9), _dto(intra_rank=0.6)]
    ranked = _blend.rank(cards, {"teacher": 1.0})
    assert [round(rc.blended_score, 2) for rc in ranked] == [0.9, 0.6, 0.4]


def test_blend_dims_saturated_persona_so_other_rises():
    cards = [
        _dto(source_persona="teacher", intra_rank=0.9),
        _dto(source_persona="comforter", intra_rank=0.8),
    ]
    ranked = _blend.rank(cards, {"teacher": 0.3, "comforter": 1.0})
    # comforter (0.8 * 1.0 = 0.8) now beats the dimmed teacher (0.9 * 0.3 = 0.27)
    assert ranked[0].card.source_persona == "comforter"
    assert ranked[1].card.source_persona == "teacher"


def test_blend_missing_weight_defaults_to_one():
    cards = [_dto(source_persona="helper", intra_rank=0.7)]
    ranked = _blend.rank(cards, {})  # no weight for "helper"
    assert ranked[0].persona_weight == 1.0
    assert round(ranked[0].blended_score, 2) == 0.7


def test_blend_is_source_agnostic_interleave():
    cards = [
        _dto(source_persona="teacher", intra_rank=0.5),
        _dto(source_persona="comforter", intra_rank=0.95),
        _dto(source_persona="teacher", intra_rank=0.7),
    ]
    ranked = _blend.rank(cards, {"teacher": 1.0, "comforter": 1.0})
    personas = [rc.card.source_persona for rc in ranked]
    assert personas == ["comforter", "teacher", "teacher"]


# --- saturation (Phase-0 stub) --------------------------------------------


def test_saturation_stub_returns_neutral_weight():
    w = asyncio.run(_saturation.persona_weight(None, uuid4(), "teacher"))
    assert w == 1.0


# --- assemble --------------------------------------------------------------


class _FakeClient:
    def __init__(self, cards):
        self._cards = cards
        self.selected = None
        self.dismissed = None

    async def list_feed_candidates(self, user_id, *, status=None, source_persona=None, limit=50):
        return list(self._cards)

    async def select_feed_candidate(self, user_id, candidate_id):
        self.selected = candidate_id
        return _dto(id=candidate_id, status="selected", posture="deepen",
                    purpose="teacher:long-horizon-propose", opening="Resume momentum.")

    async def dismiss_feed_candidate(self, user_id, candidate_id):
        self.dismissed = candidate_id
        return _dto(id=candidate_id, status="dismissed")


def test_assemble_blends_and_is_not_stale_with_fresh_cards(monkeypatch):
    produced = []
    async def _fake_notify(user_id, *, wait):
        produced.append((user_id, wait))
    monkeypatch.setattr(_feed, "_notify_produce", _fake_notify)

    cards = [_dto(intra_rank=0.3), _dto(intra_rank=0.8)]
    client = _FakeClient(cards)
    out = asyncio.run(_feed.assemble(client, uuid4(), now=_NOW))

    assert out["stale"] is False
    assert produced == []  # fresh → no regeneration
    assert [c["blended_score"] for c in out["cards"]] == [0.8, 0.3]


def test_assemble_empty_is_stale_and_triggers_async_produce(monkeypatch):
    produced = []
    async def _fake_notify(user_id, *, wait):
        produced.append((user_id, wait))
    monkeypatch.setattr(_feed, "_notify_produce", _fake_notify)

    client = _FakeClient([])
    uid = uuid4()
    out = asyncio.run(_feed.assemble(client, uid, now=_NOW))

    assert out["cards"] == []
    assert out["stale"] is True
    assert produced == [(uid, False)]  # fire-and-forget regen


def test_assemble_stale_when_newest_card_too_old(monkeypatch):
    monkeypatch.setattr(_feed, "_notify_produce", lambda *a, **k: _noop())
    old = _dto(created_at=_NOW - _feed.FEED_STALE_AFTER - timedelta(hours=1))
    out = asyncio.run(_feed.assemble(_FakeClient([old]), uuid4(), now=_NOW))
    assert out["stale"] is True


async def _noop():
    return None


# --- select ----------------------------------------------------------------


def test_select_seeds_cache_with_card_frame():
    cache = Cache()
    client = _FakeClient([])
    uid = uuid4()
    cid = uuid4()
    card = asyncio.run(_feed.select(client, cache, uid, cid))

    assert client.selected == cid
    assert card["status"] == "selected"
    entry = asyncio.run(cache.get(uid, "teacher:long-horizon-propose"))
    assert entry is not None
    assert entry.paragraph == "Resume momentum."
    assert entry.posture == "deepen"


# --- teacher producer mapping ----------------------------------------------


def test_producer_maps_items_and_skips_empty(monkeypatch):
    from persona.teacher.feed import producer as _producer

    items = [
        {"category": "review", "title": "Spaced repetition", "summary": "Quick refresher.",
         "reasoning": "rusty", "concept_names": ["SR"], "priority": 0.9},
        {"category": "deepen", "title": "Backprop", "summary": "One level deeper.",
         "priority": 0.6},
        {"category": "explore", "title": "", "summary": "missing title -> skip"},  # dropped
    ]

    async def _fake_reason(db, user_id, self_description):
        return items
    monkeypatch.setattr(_producer, "reason_candidate_items", _fake_reason)

    captured = {}

    class _C:
        async def get_profile(self, user_id):
            class _P: self_description = "bg"
            return _P()
        async def replace_feed_candidates(self, user_id, source_persona, creates):
            captured["persona"] = source_persona
            captured["creates"] = creates
            return []

    asyncio.run(_producer.produce_teacher_feed(db=None, user_id=uuid4(), client=_C()))

    creates = captured["creates"]
    assert captured["persona"] == "teacher"
    assert [c.title for c in creates] == ["Spaced repetition", "Backprop"]  # empty dropped
    assert all(isinstance(c, FeedCandidateCreate) for c in creates)
    # category → posture map
    postures = {c.title: c.posture for c in creates}
    assert postures["Spaced repetition"] == "steady"  # review → steady
    assert postures["Backprop"] == "deepen"            # deepen → deepen
    # intra_rank = priority; opening = summary
    sr = next(c for c in creates if c.title == "Spaced repetition")
    assert sr.intra_rank == 0.9
    assert sr.opening == "Quick refresher."
    assert sr.purpose == "teacher:long-horizon-propose"
