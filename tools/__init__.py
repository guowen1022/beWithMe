"""Tools — the verbs personas can invoke.

A tool wraps one capability (a service call, an agent invocation, or a UI
mutation) in a uniform async-function shape. Tools are the only path
between personas and agents/services. Personas never import agents or
service internals directly.

`build_generic_specs(user_id)` returns the per-request ToolSpec list for
the 8 generic verbs (perception, web reading, voice). Personas combine
it with `workshop.canvas.tools.build_canvas_specs` and any persona-
specific tools.
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from infra.model.tools import ToolSpec

from tools import (
    browser_set,
    look_at_image,
    look_at_video,
    read_document,
    read_url,
    search_notes,
    speak,
    web_view,
)


def build_generic_specs(user_id: UUID) -> List[ToolSpec]:
    return [
        read_document.build_spec(user_id),
        search_notes.build_spec(user_id),
        look_at_image.build_spec(user_id),
        look_at_video.build_spec(user_id),
        read_url.build_spec(user_id),
        browser_set.build_spec(user_id),
        web_view.build_spec(user_id),
        speak.build_spec(user_id),
    ]


__all__ = ["build_generic_specs"]
