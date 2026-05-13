"""Block ID → template name registry.

Mount-time scratch: `mount_template` records the template name it
mounted under each block id; `push_block_content` consults this when
deciding whether to run the value through a per-template preprocessor
(today: only `rich_card` HTML).

In-process only. Mounts are already ephemeral (no DB, no git), so a
workshop restart drops both the mounts and this registry consistently.
"""
from __future__ import annotations

_block_template: dict[str, str] = {}


def register(block_id: str, template_name: str) -> None:
    if block_id and template_name:
        _block_template[block_id] = template_name


def template_for(block_id: str) -> str | None:
    return _block_template.get(block_id)


def forget(block_id: str) -> None:
    _block_template.pop(block_id, None)


def clear() -> None:
    _block_template.clear()
