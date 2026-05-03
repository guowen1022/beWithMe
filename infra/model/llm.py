"""LLM facade — exposes the active provider's interface.

Call sites import from here (e.g. `from infra.model.llm import generate`)
and never reach into `minimax/` or `deepseek/` directly. The active backend
is chosen by `settings.llm_provider` at process start, controlled via the
`LLM_PROVIDER` env var.

All providers expose the same five functions:
  - generate(prompt, system="", max_tokens=4096) -> str
  - generate_cached(static_system, static_user_passage, dynamic_user,
                    prior_messages=None, max_tokens=4096) -> (text, usage)
  - stream_cached(...) -> AsyncIterator[{"kind": "delta"|"done", ...}]
  - stream_with_tools(static_system, static_user_passage, dynamic_user,
                      prior_messages=None, tools=None, max_tokens=4096)
                      -> AsyncIterator[{"kind": "delta"|"tool_call"|"done", ...}]
  - generate_json(prompt, max_tokens=512) -> str
"""
from infra.config import settings

_PROVIDER = (settings.llm_provider or "").lower()


def _require(env_name: str, value: str) -> None:
    if not value:
        raise RuntimeError(
            f"LLM_PROVIDER={_PROVIDER!r} requires {env_name} to be set in .env"
        )


if _PROVIDER == "deepseek":
    _require("DEEPSEEK_API_KEY", settings.deepseek_api_key)
    _require("DEEPSEEK_BASE_URL", settings.deepseek_base_url)
    _require("DEEPSEEK_MODEL", settings.deepseek_model)
    from infra.model.deepseek.llm import (  # noqa: F401
        generate,
        generate_cached,
        stream_cached,
        stream_with_tools,
        generate_json,
    )
elif _PROVIDER == "minimax":
    _require("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    _require("ANTHROPIC_BASE_URL", settings.anthropic_base_url)
    _require("LLM_MODEL", settings.llm_model)
    from infra.model.minimax.llm import (  # noqa: F401
        generate,
        generate_cached,
        stream_cached,
        stream_with_tools,
        generate_json,
    )
elif _PROVIDER == "fake":
    # E2E test provider — deterministic canned tokens. No API key needed.
    from infra.model.fake.llm import (  # noqa: F401
        generate,
        generate_cached,
        stream_cached,
        stream_with_tools,
        generate_json,
    )
else:
    raise ValueError(
        f"Unknown LLM_PROVIDER: {settings.llm_provider!r} "
        "(expected 'minimax', 'deepseek', or 'fake')"
    )

__all__ = [
    "generate",
    "generate_cached",
    "stream_cached",
    "stream_with_tools",
    "generate_json",
]
