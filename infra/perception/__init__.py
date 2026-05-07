"""Persona perception subsystem.

Producers (frontend blocks, the speak tool) push state into the cache.
Consumers (the persona's read_media tool) read from it. The cache is
the single source of truth for "what is the user currently receiving."

Designed so a future event-driven persona can subscribe to state changes
via cache.subscribe(...) without changing this module's API.
"""
from infra.perception.cache import (
    record_block_state,
    record_voice,
    record_user_speech,
    read_for_user,
    forget_block,
    forget_device,
    is_likely_echo,
    subscribe,
    unsubscribe,
)
from infra.perception.contracts import (
    BlockState,
    VoiceUtterance,
    UserUtterance,
    VoiceEvent,
    UserSpeechEvent,
    BlockChangeEvent,
    BlockCompletedEvent,
    BlockSummary,
    CanvasPerception,
    VoicePerception,
    MediaPerception,
)

__all__ = [
    "record_block_state",
    "record_voice",
    "record_user_speech",
    "read_for_user",
    "forget_block",
    "forget_device",
    "is_likely_echo",
    "subscribe",
    "unsubscribe",
    "BlockState",
    "VoiceUtterance",
    "UserUtterance",
    "VoiceEvent",
    "UserSpeechEvent",
    "BlockChangeEvent",
    "BlockCompletedEvent",
    "BlockSummary",
    "CanvasPerception",
    "VoicePerception",
    "MediaPerception",
]
