"""In-memory perception cache.

Includes echo dedup: when an `ambient_mic` block on the same device as
an active speaker captures the teacher's own TTS played through the
speakers, the resulting transcript is essentially the teacher's
recent utterance. `is_likely_echo(...)` compares an incoming user
transcript against the user's recent teacher voice utterances; the
perception_utterance endpoint uses it to drop echoes server-side
without suppressing legitimate user interruptions like "stop" or
"wait" (which won't match any teacher utterance).

Single source of truth for "what is the user receiving right now."
Producers (frontend blocks, tools/speak.py) write here; consumers (the
persona's read_media tool) read from here. No DB writes — state is
transient and rehydrates from frontend reports within seconds of a
backend restart.

P6 hook: every state change fires registered listeners. Empty in this
PR; the next PR's event-driven persona subscribes here. Keeping the
hook now means that PR adds new code rather than refactoring this one.

Focus invariant: at most one block per (user, device) is `active`.
record_block_state enforces this — promoting block A demotes whatever
was previously active for that device, and fires a change event for
the demoted block too.

Focus-only coalescing: when a block update changes only the focus field
(content/kind/extra unchanged) and the previous update for that block
was less than COALESCE_WINDOW_MS ago, we still write the new state
(latest wins) but defer the listener fire so a fast mouseover sweep
across N blocks doesn't fire N listener calls. The fire happens once
COALESCE_WINDOW_MS after the last focus-only change for that block.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Union
from uuid import UUID

from infra.perception.contracts import (
    BlockChangeEvent,
    BlockCompletedEvent,
    BlockState,
    PerceptionEvent,
    UserSpeechEvent,
    UserUtterance,
    VoiceEvent,
    VoiceUtterance,
)


# ---------- tunables ----------

CONTENT_MAX_CHARS = 1000
VOICE_LOG_MAX = 50
USER_SPEECH_LOG_MAX = 50
COALESCE_WINDOW_MS = 500

# Echo dedup tunables.
ECHO_WINDOW_S = 15.0       # only compare against teacher utterances this recent
ECHO_RATIO_THRESHOLD = 0.6 # SequenceMatcher.ratio above this → echo
ECHO_SUBSET_MIN_TOKENS = 4 # ignore subset matches shorter than this (so "ok"
                           # uttered while teacher said "okay sure" doesn't get
                           # mis-classified as echo)


# ---------- state ----------

# user_id (str) -> device_id (str) -> block_id (str) -> BlockState
_block_state: Dict[str, Dict[str, Dict[str, BlockState]]] = defaultdict(
    lambda: defaultdict(dict)
)
# (user, device, block) -> datetime
_block_state_at: Dict[tuple, datetime] = {}

# user_id (str) -> deque[VoiceUtterance]
_voice_log: Dict[str, Deque[VoiceUtterance]] = defaultdict(
    lambda: deque(maxlen=VOICE_LOG_MAX)
)

# user_id (str) -> deque[UserUtterance]
# Ambient speech captured by the ambient_mic block. In-memory only —
# resets on persona-sidecar restart. Talk is cheap; not promoted to disk.
_user_speech_log: Dict[str, Deque[UserUtterance]] = defaultdict(
    lambda: deque(maxlen=USER_SPEECH_LOG_MAX)
)

Listener = Callable[[PerceptionEvent], Awaitable[None]]
_listeners: List[Listener] = []

# Focus-only coalescing: (user, device, block) -> deferred-fire task.
_pending_focus_fires: Dict[tuple, asyncio.Task] = {}


# ---------- helpers ----------


def _truncate(s: str) -> str:
    if len(s) <= CONTENT_MAX_CHARS:
        return s
    return s[:CONTENT_MAX_CHARS] + f"…[truncated {len(s) - CONTENT_MAX_CHARS} chars]"


def _state_with_truncation(state: BlockState) -> BlockState:
    if len(state.content) <= CONTENT_MAX_CHARS:
        return state
    return state.model_copy(update={"content": _truncate(state.content)})


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _normalize_for_compare(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Stable for
    SequenceMatcher input."""
    if not s:
        return ""
    tokens = _WORD_RE.findall(s.lower())
    return " ".join(tokens)


def is_likely_echo(
    user_id: UUID,
    text: str,
    *,
    window_s: float = ECHO_WINDOW_S,
    ratio_threshold: float = ECHO_RATIO_THRESHOLD,
) -> bool:
    """Return True if `text` looks like an echo of something the
    teacher recently said on this user's device.

    Used by the perception endpoint to drop transcripts that are
    actually the teacher's own TTS leaking back through the mic, while
    still letting genuine user interruptions through ("stop", "wait",
    "hold on" — none of which appear inside the teacher's running
    utterance).

    Heuristics, in order:
      1. Exact normalized match.
      2. User's normalized text is a substring of any recent teacher
         utterance (with a token-count floor so single short words
         don't false-positive).
      3. SequenceMatcher.ratio >= threshold against any recent
         teacher utterance.

    Only checks teacher-source voice entries within `window_s` seconds.
    """
    norm_user = _normalize_for_compare(text)
    if not norm_user:
        return False
    user_token_count = len(norm_user.split())

    log = _voice_log.get(str(user_id))
    if not log:
        return False

    cutoff = datetime.utcnow() - timedelta(seconds=window_s)
    for v in log:
        if getattr(v, "source", "external") != "teacher":
            continue
        if v.played_at < cutoff:
            continue
        norm_teacher = _normalize_for_compare(v.text)
        if not norm_teacher:
            continue
        if norm_user == norm_teacher:
            return True
        if (
            user_token_count >= ECHO_SUBSET_MIN_TOKENS
            and norm_user in norm_teacher
        ):
            return True
        ratio = SequenceMatcher(None, norm_user, norm_teacher).ratio()
        if ratio >= ratio_threshold:
            return True
    return False


def _is_focus_only_change(prev: Optional[BlockState], new: BlockState) -> bool:
    if prev is None:
        return False
    return (
        prev.kind == new.kind
        and prev.content == new.content
        and prev.extra == new.extra
        and prev.focus != new.focus
    )


# ---------- listeners (P6 hook) ----------


def subscribe(listener: Listener) -> Callable[[], None]:
    """Register a listener for perception events. Returns an unsubscribe fn."""
    _listeners.append(listener)
    def _unsub() -> None:
        try:
            _listeners.remove(listener)
        except ValueError:
            pass
    return _unsub


def unsubscribe(listener: Listener) -> None:
    try:
        _listeners.remove(listener)
    except ValueError:
        pass


async def _fire(event: PerceptionEvent) -> None:
    if not _listeners:
        return
    for listener in list(_listeners):
        try:
            await listener(event)
        except Exception as e:
            print(f"[perception] listener error: {e}", flush=True)


def _schedule_fire(event: PerceptionEvent) -> None:
    """Spawn the listener fire as an independent task so the caller's
    request task can return immediately. Mirrors the device-registry
    pattern in infra/devices/registry.py."""
    if not _listeners:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_fire(event))


# ---------- recording ----------


def record_block_state(
    *,
    user_id: UUID,
    device_id: UUID,
    block_id: str,
    state: BlockState,
) -> None:
    """Write a block's state to the cache.

    Enforces the focus invariant (at most one active per device) and
    coalesces focus-only changes (see module docstring).
    """
    state = _state_with_truncation(state)
    uid_s, did_s = str(user_id), str(device_id)
    now = datetime.utcnow()
    by_block = _block_state[uid_s][did_s]

    prev = by_block.get(block_id)

    # Focus invariant: promoting one block to active demotes any other
    # active block on the same device. Each demotion fires its own event.
    demoted: list[tuple[str, BlockState]] = []
    if state.focus == "active":
        for other_id, other_state in list(by_block.items()):
            if other_id == block_id:
                continue
            if other_state.focus == "active":
                demoted_state = other_state.model_copy(update={"focus": "visible"})
                by_block[other_id] = demoted_state
                _block_state_at[(uid_s, did_s, other_id)] = now
                demoted.append((other_id, demoted_state))

    by_block[block_id] = state
    _block_state_at[(uid_s, did_s, block_id)] = now

    # Listener fires.
    primary_event = BlockChangeEvent(
        user_id=user_id, device_id=device_id, block_id=block_id, state=state
    )

    focus_only = _is_focus_only_change(prev, state)
    key = (uid_s, did_s, block_id)
    pending = _pending_focus_fires.pop(key, None)
    if pending is not None and not pending.done():
        pending.cancel()

    if focus_only and _listeners:
        # Schedule a deferred fire that yields to coalesce subsequent changes.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            async def _deferred() -> None:
                try:
                    await asyncio.sleep(COALESCE_WINDOW_MS / 1000.0)
                    # Fire with the latest cached state (may have been overwritten
                    # multiple times during the window — the cache always reflects
                    # the most recent value).
                    latest = _block_state.get(uid_s, {}).get(did_s, {}).get(block_id)
                    if latest is None:
                        return
                    await _fire(BlockChangeEvent(
                        user_id=user_id, device_id=device_id,
                        block_id=block_id, state=latest,
                    ))
                finally:
                    _pending_focus_fires.pop(key, None)
            _pending_focus_fires[key] = loop.create_task(_deferred())
    else:
        _schedule_fire(primary_event)

    # Edge-detect completion: false → true. Re-true→true edges don't refire.
    prev_completed = prev.completed if prev is not None else False
    if state.completed and not prev_completed:
        _schedule_fire(BlockCompletedEvent(
            user_id=user_id, device_id=device_id, block_id=block_id, state=state,
        ))

    for did, dstate in demoted:
        _schedule_fire(BlockChangeEvent(
            user_id=user_id, device_id=device_id, block_id=did, state=dstate,
        ))


def forget_device(*, user_id: UUID, device_id: UUID) -> None:
    """Drop all cached block state for one (user, device) pair.

    Called when a fresh client SSE channel opens for the device — a fresh
    client (e.g. Electron restart, full page reload) has zero blocks
    rendered, so any state cached from the previous client session is
    stale and must not be returned to the persona. Idempotent. Fires no
    events: removals are not "changes on live blocks."
    """
    uid_s, did_s = str(user_id), str(device_id)
    by_device = _block_state.get(uid_s)
    if by_device and did_s in by_device:
        for bid in list(by_device[did_s].keys()):
            _block_state_at.pop((uid_s, did_s, bid), None)
        del by_device[did_s]
        if not by_device:
            _block_state.pop(uid_s, None)


def forget_block(*, user_id: UUID, block_id: str) -> None:
    """Drop the cached state for a block id across every device.

    Called when a block is unmounted from the canvas — the block is no
    longer visible to the user, so its last-reported state should not
    keep showing up in `read_for_user`. Without this, an unmounted
    upload widget's "Ready: …" state would linger and the teacher
    would still think the widget is on screen.

    Idempotent. No event is fired (consumers care about *changes* on
    live blocks, not removals — and the unmount itself is already
    delivered via the SSE channel).
    """
    uid_s = str(user_id)
    by_device = _block_state.get(uid_s)
    if not by_device:
        return
    empty_devices: list[str] = []
    for did_s, by_block in by_device.items():
        if block_id in by_block:
            del by_block[block_id]
            _block_state_at.pop((uid_s, did_s, block_id), None)
        if not by_block:
            empty_devices.append(did_s)
    for did_s in empty_devices:
        del by_device[did_s]
    if not by_device:
        _block_state.pop(uid_s, None)


def record_voice(
    *,
    user_id: UUID,
    text: str,
    voice: Optional[str] = None,
    device_id: Optional[UUID] = None,
    source: str = "external",
) -> None:
    """Append a voice utterance to the user's log + fire a VoiceEvent.

    `source="teacher"` flags the persona's own speech so the teacher's
    orchestrator can drop the resulting event instead of waking another
    reflect turn.
    """
    utt = VoiceUtterance(
        text=text,
        voice=voice,
        device_id=device_id,
        played_at=datetime.utcnow(),
        source=source,
    )
    _voice_log[str(user_id)].append(utt)
    _schedule_fire(VoiceEvent(user_id=user_id, utterance=utt))


def record_user_speech(
    *,
    user_id: UUID,
    text: str,
    language: Optional[str] = None,
    audio_duration_s: Optional[float] = None,
    target_persona: str = "teacher",
    device_id: Optional[UUID] = None,
) -> None:
    """Append a user-speech utterance to the in-memory log + fire a
    UserSpeechEvent. No DB writes. Cheap-talk: persists only as long as
    the persona sidecar process lives.
    """
    utt = UserUtterance(
        text=text,
        language=language,
        audio_duration_s=audio_duration_s,
        device_id=device_id,
        captured_at=datetime.utcnow(),
        target_persona=target_persona,
    )
    _user_speech_log[str(user_id)].append(utt)
    _schedule_fire(UserSpeechEvent(
        user_id=user_id, utterance=utt, target_persona=target_persona,
    ))


# ---------- reading ----------


def read_for_user(user_id: UUID) -> dict:
    """Snapshot of everything cached for one user.

    Returns:
      {
        "block_state": {device_id_str: {block_id: (BlockState, datetime)}},
        "voice_log": [VoiceUtterance, ...],
        "user_speech_log": [UserUtterance, ...],
      }
    """
    uid_s = str(user_id)
    by_device = _block_state.get(uid_s, {})
    state_view: dict[str, dict[str, tuple[BlockState, datetime]]] = {}
    for did_s, by_block in by_device.items():
        state_view[did_s] = {}
        for bid, bstate in by_block.items():
            ts = _block_state_at.get((uid_s, did_s, bid))
            state_view[did_s][bid] = (bstate, ts or datetime.utcnow())
    voice = list(_voice_log.get(uid_s, ()))
    user_speech = list(_user_speech_log.get(uid_s, ()))
    return {
        "block_state": state_view,
        "voice_log": voice,
        "user_speech_log": user_speech,
    }


# ---------- test hooks ----------


def _reset_for_tests() -> None:
    """Wipe all in-memory state. Used by tests to keep cases isolated."""
    _block_state.clear()
    _block_state_at.clear()
    _voice_log.clear()
    _user_speech_log.clear()
    for task in list(_pending_focus_fires.values()):
        if not task.done():
            task.cancel()
    _pending_focus_fires.clear()
    # Listeners are deliberately left alone — tests that subscribe should
    # unsubscribe themselves.
