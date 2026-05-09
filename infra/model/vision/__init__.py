"""Vision (image-understanding) facade — exposes the active provider's interface.

Parallel to `infra.model.llm` but for visual perception. The main reasoning
LLM stays text-only; tools that need eyes (look_at_image, web_view with
include_screenshot) call into here.

The active backend is chosen by `settings.vision_provider` at process start,
controlled via the `VISION_PROVIDER` env var. All providers expose:

  - describe_image(image, prompt=None) -> str
"""
from __future__ import annotations

from typing import Optional

from infra.config import settings


_PROVIDER = (settings.vision_provider or "").lower()


def _require(env_name: str, value: str) -> None:
    if not value:
        raise RuntimeError(
            f"VISION_PROVIDER={_PROVIDER!r} requires {env_name} to be set in .env"
        )


if _PROVIDER == "doubao":
    _require("DOUBAO_API_KEY", settings.doubao_api_key)
    _require("DOUBAO_BASE_URL", settings.doubao_base_url)
    _require("DOUBAO_VISION_MODEL", settings.doubao_vision_model)
    from infra.model.vision.doubao import describe_image as _raw_describe_image
else:
    raise ValueError(
        f"Unknown VISION_PROVIDER: {settings.vision_provider!r} (expected 'doubao')"
    )


async def describe_image(image: str, prompt: Optional[str] = None) -> str:
    """Run a single-shot image-understanding call against the active provider.

    `image` is either a data URL (`data:image/png;base64,...`) or an http(s)
    URL — but Volces' cn-beijing region cannot fetch external URLs, so for
    Doubao prefer base64 data URLs.
    """
    return await _raw_describe_image(image, prompt)


__all__ = ["describe_image"]
