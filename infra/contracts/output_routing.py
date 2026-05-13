"""Request-scoped output routing.

When a client sends ``X-Output-Device-Id``, downstream tool dispatchers
(speak, mount_template) should default their ``target_device_id`` to
that value so the persona's effects land on the named peer device
instead of broadcasting to all of the user's online devices.

This module owns the ``ContextVar`` so it can be set once at the
request boundary (in ``ask_stream``) and read deep inside tool
executors without threading it through every call.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional
from uuid import UUID

OUTPUT_DEVICE_ID: ContextVar[Optional[UUID]] = ContextVar(
    "OUTPUT_DEVICE_ID", default=None
)


def get_output_device_id() -> Optional[UUID]:
    """Returns the request-scoped output device id, or None if unset."""
    return OUTPUT_DEVICE_ID.get()


__all__ = ["OUTPUT_DEVICE_ID", "get_output_device_id"]
