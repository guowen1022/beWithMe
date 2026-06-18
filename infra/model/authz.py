"""Persona tool authorization — the domain-grant capability model.

A persona is the *untrusted decision-maker*: the LLM chooses the tool calls.
This module is the runtime guard on **what the LLM may select** — a persona may
select a tool iff the tool's `domain` is in the persona's `CapabilityGrant`.

It does NOT gate what a tool's own (trusted, vetted) executor then does: e.g.
`end_session` composing the `go_home` effect is fine — that is not an LLM
*selection*, so there is nothing to authorize.

Pure functions over `ToolDomain` / `ToolSpec` — no I/O, no persona knowledge,
no upward imports. infra is the leaf.

See ARCHITECTURE.md §4.4 and
architecture-review/proposals/2026-06-17-tool-authorization.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable, List

from infra.model.tools import ToolDomain, ToolSpec


@dataclass(frozen=True)
class CapabilityGrant:
    """A persona's tool-selection capability: the set of domains it may select
    from. `persona` is the name, for error messages and auditing."""
    persona: str
    domains: FrozenSet[ToolDomain]


def authorize(grant: CapabilityGrant, spec: ToolSpec) -> bool:
    """True iff `spec`'s domain is in the persona's grant. One rule, no
    exceptions — cross-domain reach happens via a tool's executor composing an
    effect, never via the LLM selecting another domain's tool."""
    return spec.domain in grant.domains


def authorized_tools(
    grant: CapabilityGrant, specs: Iterable[ToolSpec],
) -> List[ToolSpec]:
    """Assembly-time enforcement: keep only the tools the grant authorizes, so
    the LLM never even sees the rest (least privilege at the prompt)."""
    return [s for s in specs if authorize(grant, s)]


__all__ = ["CapabilityGrant", "authorize", "authorized_tools"]
