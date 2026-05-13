"""Wire DTOs for the rich_card block.

Used by `mount_template` to validate persona input shape before it reaches
the preprocessor, and (later) by mobile / web typing.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RichCardParams(BaseModel):
    """Shape of the `params` blob a persona passes to `mount_template`
    when `template == "rich_card"`.

    Currently single-field — `content` is the HTML body conforming to the
    grammar in `infra/render/rich_card_grammar.py`. The `version` field
    exists so we can roll the grammar forward without breaking older
    canvases that already mounted v1 cards.
    """

    content: str = Field(..., description="HTML body. Will be preprocessed + sanitized.")
    version: Literal["v1"] = "v1"
