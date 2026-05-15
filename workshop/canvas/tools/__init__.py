"""Canvas-manipulation tools. Moved here from the top-level `tools/`
package so both the teacher and the engineer can import the same
verbs without crossing persona boundaries.

`build_canvas_specs(user_id)` returns the per-request ToolSpec list for
the 10 canvas verbs. Personas combine it with `tools.build_generic_specs`
and any persona-specific tools (e.g. teacher's research lane).
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from infra.model.tools import ToolSpec

from workshop.canvas.tools import (
    block_action,
    edit_note,
    interactive_graph,
    layout_blocks,
    list_media,
    mount_template,
    point_arrow,
    push_block_content,
    read_media,
    request_ui_block,
)


def build_canvas_specs(user_id: UUID) -> List[ToolSpec]:
    return [
        read_media.build_spec(user_id),
        list_media.build_spec(user_id),
        mount_template.build_spec(user_id),
        edit_note.build_spec(user_id),
        request_ui_block.build_spec(user_id),
        push_block_content.build_spec(user_id),
        interactive_graph.build_spec(user_id),
        point_arrow.build_spec(user_id),
        layout_blocks.build_spec(user_id),
        block_action.build_spec(user_id),
    ]


__all__ = ["build_canvas_specs"]
