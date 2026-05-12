"""Argument parameterization for recorded tool sequences.

A recipe stores a *parameterized* tool sequence — values that will vary
between replays (URLs, @e refs that depend on the live page's snapshot)
are replaced with `{"$var": ...}` tokens. At replay time, `resolve`
substitutes them back to concrete values.

Heuristic (kept small and deterministic):

  - value matches `^https?://`  → `{"$var": "page_url"}` for the FIRST
    URL seen across the whole sequence; subsequent URLs become
    `{"$var": "secondary_url_<n>"}`. (Most recipes only ever touch one
    URL; the secondary slot is there so we don't silently drop side
    fetches.)
  - value starts with `@e<digit>` → `{"$var": "ref", "role": "<role>",
    "name": "<name>"}` looked up from `recorded_refs`. At replay time
    this maps through the fresh snapshot's role+name table.
  - everything else (booleans, ints, enums, raw strings) → kept as-is.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_REF_RE = re.compile(r"^@e\d+$")


def _ref_lookup(refs: List[Dict[str, Any]], ref: str) -> Optional[Tuple[str, str]]:
    """Find (role, name) for a recorded ref. Returns None if not in the
    recorded set (which means it was generated mid-run by a re-snapshot
    and isn't safely replayable — we leave it as a constant in that case
    and trust the smoke test to invalidate the recipe if it matters)."""
    for r in refs:
        if r.get("ref") == ref:
            return (str(r.get("role") or ""), str(r.get("name") or ""))
    return None


def _walk_parameterize(
    value: Any,
    refs: List[Dict[str, Any]],
    state: Dict[str, int],
) -> Any:
    """Recursive walk. `state["url_count"]` tracks how many URLs we've
    parameterized so far (so the second URL becomes `secondary_url_0`,
    third `secondary_url_1`, ...)."""
    if isinstance(value, dict):
        return {k: _walk_parameterize(v, refs, state) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_parameterize(v, refs, state) for v in value]
    if isinstance(value, str):
        if _URL_RE.match(value):
            idx = state["url_count"]
            state["url_count"] = idx + 1
            if idx == 0:
                return {"$var": "page_url"}
            return {"$var": f"secondary_url_{idx - 1}"}
        if _REF_RE.match(value):
            looked = _ref_lookup(refs, value)
            if looked is not None:
                role, name = looked
                return {"$var": "ref", "role": role, "name": name}
            # Unknown ref — keep verbatim. Smoke test catches stale runs.
            return value
    return value


def parameterize(
    tool_calls: List[Dict[str, Any]],
    recorded_refs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return a deep-copied sequence with concrete URLs/refs swapped for
    `$var` tokens. The original list is not mutated."""
    state = {"url_count": 0}
    out: List[Dict[str, Any]] = []
    for call in tool_calls:
        new_call = copy.deepcopy(call)
        if isinstance(new_call.get("arguments"), dict):
            new_call["arguments"] = _walk_parameterize(
                new_call["arguments"], recorded_refs, state
            )
        out.append(new_call)
    return out


def _walk_resolve(value: Any, runtime: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        # Variable token?
        if "$var" in value:
            name = value["$var"]
            if name == "page_url":
                return runtime.get("page_url") or ""
            if isinstance(name, str) and name.startswith("secondary_url_"):
                idx = int(name.rsplit("_", 1)[-1])
                secondaries = runtime.get("secondary_urls") or []
                if 0 <= idx < len(secondaries):
                    return secondaries[idx]
                return ""
            if name == "ref":
                role = value.get("role", "")
                ref_role_name = (role, value.get("name", ""))
                remap = runtime.get("ref_remap") or {}
                # Two formats supported for ref_remap:
                #   1) {"@e3": "@e7", ...}                — keyed by old ref
                #   2) {(role, name): "@e7", ...}         — keyed by tuple
                # The runner uses (2) since old refs aren't carried into
                # the parameterized sequence; we look up by (role, name).
                resolved = remap.get(ref_role_name)
                if resolved is None and isinstance(remap, dict):
                    # Fallback: walk dict for compatibility.
                    for k, v in remap.items():
                        if isinstance(k, tuple) and k == ref_role_name:
                            resolved = v
                            break
                return resolved or ""
            return value
        return {k: _walk_resolve(v, runtime) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_resolve(v, runtime) for v in value]
    return value


def resolve(
    parameterized_calls: List[Dict[str, Any]],
    runtime: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Substitute variables in a parameterized sequence. `runtime`:
        {
            "page_url": str,
            "secondary_urls": [str, ...],   # optional
            "ref_remap": {(role, name): "@e<n>"}
        }
    """
    out: List[Dict[str, Any]] = []
    for call in parameterized_calls:
        new_call = copy.deepcopy(call)
        if isinstance(new_call.get("arguments"), dict):
            new_call["arguments"] = _walk_resolve(new_call["arguments"], runtime)
        out.append(new_call)
    return out


__all__ = ["parameterize", "resolve"]
