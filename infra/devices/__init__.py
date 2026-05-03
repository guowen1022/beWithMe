"""Device registry — live presence + durable mirror.

A device is "online" when at least one SSE connection from it is open.
The DB row exists from first-ever connect onward so the teacher's
`list_media()` can still report a phone/laptop the user owns even when
it's currently closed.
"""
from infra.devices.registry import (
    list_for_user,
    register,
    mark_offline,
    mark_offline_local,
    schedule_offline_write,
    is_online,
)

__all__ = [
    "list_for_user",
    "register",
    "mark_offline",
    "mark_offline_local",
    "schedule_offline_write",
    "is_online",
]
