"""Shared httpx client factory for LLM SDKs.

Why this exists: the OpenAI and Anthropic SDKs each create an internal
`httpx.AsyncClient` with no timeout and no keepalive_expiry override.
With the user's typical pattern (open the desktop, idle 5–15 min, ask
the teacher something), the connection pool happily reuses a TCP
socket that the upstream server (or proxy) has already half-closed
during the idle window. The HTTP write succeeds, the response never
arrives, and the SDK has no read timeout to abort — the request hangs
forever. Tasks like `lane-a` and `reflect` show "running…" indefinitely.

Fix: build a custom `httpx.AsyncClient` with

  - aggressive `keepalive_expiry` so idle pool connections are evicted
    before they go stale,
  - explicit `read`/`connect`/`write` timeouts so dead sockets get
    aborted instead of hung on,
  - an `AsyncHTTPTransport(retries=N)` that auto-retries on connection
    errors before the SDK even sees them.

`trust_env=True` is the httpx default, so `http_proxy` / `https_proxy` /
`no_proxy` env vars (loaded from .env via `load_dotenv` at module
import) are still honored — important per CLAUDE.md's proxy rules.
"""
from __future__ import annotations

import httpx


# Read timeout is generous: streaming LLM responses can have multi-second
# gaps between chunks during long tool calls. 180s is the inter-chunk
# ceiling; if the model goes silent for that long it's almost certainly
# wedged. Connect/write are tighter — those should be sub-second on a
# healthy network.
_DEFAULT_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=180.0,
    write=30.0,
    pool=5.0,
)

# keepalive_expiry=30s is the load-bearing knob: it caps how long an
# unused TCP connection sits in the pool before httpx closes it. With
# the default (5s) we'd already be safe, but some upstreams / proxies
# close idle conns faster than we can detect. Using 30s explicitly
# documents intent and is well under typical NAT/proxy idle thresholds
# (60–120s).
_DEFAULT_LIMITS = httpx.Limits(
    max_keepalive_connections=10,
    max_connections=20,
    keepalive_expiry=30.0,
)


def make_async_http_client() -> httpx.AsyncClient:
    """Build the shared httpx.AsyncClient handed to AsyncOpenAI / AsyncAnthropic.

    Each LLM provider should hold a single long-lived instance of this
    client (alongside its single long-lived SDK client) — the pool is
    what we want to keep, the *stale* idle connections are what we
    want to evict.
    """
    return httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        limits=_DEFAULT_LIMITS,
        # retries=2 covers transport-level ConnectError on a fresh socket
        # (e.g. immediately after the pool evicts a stale conn). Retries
        # do NOT cover read timeouts mid-response — those propagate up
        # so the caller can decide.
        transport=httpx.AsyncHTTPTransport(retries=2),
    )
