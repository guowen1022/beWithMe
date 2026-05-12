"""Recipe replay engine — smoke-test then execute a recorded tool sequence.

Two functions:

  * `smoke_test(user_id, recipe, runtime_url)` runs `goto + snapshot`
    on `runtime_url`, builds a `(role, name) → @e<n>` map from the fresh
    snapshot's refs, and verifies that at least 70% of the recipe's
    `recorded_refs` find a match. If they do, returns the
    `{(role, name): "@e<n>"}` ref-remap dict; otherwise returns None
    (caller falls back to fresh Lane R).

  * `run_recipe(user_id, recipe, runtime_url, ref_remap, on_step)`
    resolves the parameterized tool calls against the runtime, executes
    them via the same `tools.browser_set.browser_set` /
    `tools.read_url.read_url` / `tools.look_at_image.look_at_image`
    etc. closures the manifest uses, and returns the list of collected
    `(tool_name, args, result)` tuples.

**No LLM in this module.** Synthesis is the caller's job (persona-flavored).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import UUID

from workshop.research import recipe_parameterize
from workshop.research.recipe_store import ResearchRecipe


_MIN_REF_MATCH = 0.7  # fraction of recorded refs that must remap


def _build_role_name_map(refs: List[Dict[str, Any]]) -> Dict[Tuple[str, str], str]:
    """Index a snapshot's refs by (role, name) so we can look up the
    fresh `@e<n>` for an old (role, name) pair from the recipe."""
    out: Dict[Tuple[str, str], str] = {}
    for r in refs:
        key = (str(r.get("role") or ""), str(r.get("name") or ""))
        ref = str(r.get("ref") or "")
        if not ref:
            continue
        # First occurrence wins — matches the snapshot's deterministic
        # ordering (page-DOM-order).
        if key not in out:
            out[key] = ref
    return out


async def smoke_test(
    user_id: UUID,
    recipe: ResearchRecipe,
    runtime_url: str,
) -> Optional[Dict[Tuple[str, str], str]]:
    """Goto + snapshot, then try to remap each recorded ref by (role, name).

    Returns a `{(role, name) → @e<n>}` map on success (≥70% of recorded
    refs found a fresh equivalent), `None` on failure. On `None` the
    caller should fall back to the fresh Lane R path.
    """
    # Late import — `tools.browser_set` pulls in httpx + sidecar config
    # at import time; we don't want to slow down `workshop` package load.
    from tools.browser_set import browser_set

    if not runtime_url:
        return None

    try:
        goto_result = await browser_set(
            user_id=user_id, action="goto", url=runtime_url,
        )
    except Exception as e:
        print(f"[recipe_runner] smoke goto failed: {e}", flush=True)
        return None
    if isinstance(goto_result, dict) and goto_result.get("error"):
        print(
            f"[recipe_runner] smoke goto returned error: {goto_result['error']}",
            flush=True,
        )
        return None

    try:
        snap = await browser_set(user_id=user_id, action="snapshot")
    except Exception as e:
        print(f"[recipe_runner] smoke snapshot failed: {e}", flush=True)
        return None
    if isinstance(snap, dict) and snap.get("error"):
        print(
            f"[recipe_runner] smoke snapshot returned error: {snap['error']}",
            flush=True,
        )
        return None

    fresh_refs = (snap or {}).get("refs") or []
    fresh_by_role_name = _build_role_name_map(fresh_refs)

    remap: Dict[Tuple[str, str], str] = {}
    for r in recipe.recorded_refs:
        key = (str(r.get("role") or ""), str(r.get("name") or ""))
        if key in fresh_by_role_name:
            remap[key] = fresh_by_role_name[key]

    if not recipe.recorded_refs:
        # Recipe has no refs — only generic actions (no addressable
        # @e<n> calls). Safe to replay; empty remap.
        return remap
    fraction = len(remap) / len(recipe.recorded_refs)
    if fraction < _MIN_REF_MATCH:
        print(
            f"[recipe_runner] smoke matched {len(remap)}/{len(recipe.recorded_refs)} refs "
            f"({fraction:.0%}) — below {_MIN_REF_MATCH:.0%}; aborting replay",
            flush=True,
        )
        return None
    return remap


# Tool dispatch — same imports the manifest uses. Importing them lazily
# inside the function so this module doesn't pull the world at startup.
async def _dispatch_tool(
    user_id: UUID,
    name: str,
    arguments: Dict[str, Any],
) -> Any:
    """Execute one tool call with kwargs. Mirrors the executor closures
    in `persona/teacher/tools/manifest.py` for the subset of tools that
    can appear in a recorded sequence."""
    args = arguments or {}

    if name == "browser_set":
        from tools.browser_set import browser_set
        action = (args.get("action") or "").strip().lower()
        if not action:
            return {"error": "action required"}
        return await browser_set(
            user_id=user_id,
            action=action,
            url=args.get("url") if isinstance(args.get("url"), str) else None,
            selector=args.get("selector") if isinstance(args.get("selector"), str) else None,
            value=args.get("value") if isinstance(args.get("value"), str) else None,
            text=args.get("text") if isinstance(args.get("text"), str) else None,
            key=args.get("key") if isinstance(args.get("key"), str) else None,
            expression=args.get("expression") if isinstance(args.get("expression"), str) else None,
            state=args.get("state") if isinstance(args.get("state"), str) else None,
            wait_until=args.get("wait_until") if isinstance(args.get("wait_until"), str) else None,
            timeout=int(args.get("timeout")) if args.get("timeout") is not None else None,
            delay=int(args.get("delay")) if args.get("delay") is not None else None,
            full_page=bool(args.get("full_page") or False),
            drain=bool(args.get("drain", True)),
            x=int(args.get("x")) if args.get("x") is not None else None,
            y=int(args.get("y")) if args.get("y") is not None else None,
        )

    if name == "read_url":
        from tools.read_url import read_url
        url = (args.get("url") or "").strip()
        if not url:
            return {"error": "url required"}
        return await read_url(user_id=user_id, url=url)

    if name == "look_at_image":
        from tools.look_at_image import look_at_image
        image = (args.get("image") or "").strip()
        if not image:
            return {"error": "image required"}
        q = args.get("question")
        return await look_at_image(
            image, q if isinstance(q, str) and q.strip() else None,
        )

    if name == "read_media":
        from workshop.canvas.tools.read_media import read_media
        perc = await read_media(user_id)
        # Return as plain dict for inclusion in the synthesis prompt.
        return {
            "canvases_count": len(perc.canvases),
            "voices_count": len(perc.voices),
        }

    # Anything else (research_plan, research_note, speak, web_view,
    # mount_template, structural canvas tools) is intentionally NOT
    # replayed:
    #   - research_plan / research_note are recording scaffolds; the
    #     replay path has its own ribbon updates.
    #   - speak is the synthesis step the caller owns.
    #   - web_view / mount_template push canvas UI; the replay path's
    #     ribbon already shows progress.
    # We skip them silently and continue with the next call.
    return {"_skipped": True, "reason": f"tool {name!r} not replayable"}


# Tools we DO replay during the data-collection phase. Everything else
# is skipped (see comment in `_dispatch_tool`).
_REPLAYABLE_TOOLS = {"browser_set", "read_url", "look_at_image", "read_media"}


async def run_recipe(
    user_id: UUID,
    recipe: ResearchRecipe,
    runtime_url: str,
    ref_remap: Dict[Tuple[str, str], str],
    on_step: Optional[Callable[[str], Awaitable[None]]] = None,
) -> List[Dict[str, Any]]:
    """Resolve the parameterized sequence against `runtime_url` + `ref_remap`,
    execute the replayable subset, and return the collected results.

    Skips the first goto+snapshot (smoke ran them and left the page
    loaded). Returns `[{tool_name, args, result}, ...]`.
    """
    runtime = {
        "page_url": runtime_url,
        "secondary_urls": [],  # filled if any secondary URL params resolve later
        "ref_remap": ref_remap,
    }
    resolved = recipe_parameterize.resolve(recipe.tool_call_sequence, runtime)

    # Skip the leading goto + snapshot — smoke_test already ran them.
    # Cheap structural skip: drop the first two browser_set calls if they
    # are goto / snapshot in that order. (If a recipe didn't start with
    # that pair, replay the lot.)
    skip_count = 0
    if (
        len(resolved) >= 2
        and resolved[0].get("name") == "browser_set"
        and (resolved[0].get("arguments") or {}).get("action") == "goto"
        and resolved[1].get("name") == "browser_set"
        and (resolved[1].get("arguments") or {}).get("action") == "snapshot"
    ):
        skip_count = 2

    collected: List[Dict[str, Any]] = []
    last_step_text = ""
    for i, call in enumerate(resolved[skip_count:], start=skip_count + 1):
        name = call.get("name") or ""
        if name not in _REPLAYABLE_TOOLS:
            # Skip scaffolding calls (research_plan/note/speak/web_view/etc.)
            continue
        args = call.get("arguments") or {}

        # Coarse-grained ribbon progress callback — group by action verb.
        step_text = _progress_label(name, args)
        if on_step and step_text and step_text != last_step_text:
            try:
                await on_step(step_text)
            except Exception:
                pass
            last_step_text = step_text

        try:
            result = await _dispatch_tool(user_id, name, args)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}

        collected.append({
            "tool_name": name,
            "args": args,
            "result": result,
        })

    return collected


def _progress_label(tool_name: str, args: Dict[str, Any]) -> str:
    """Compact human-friendly label for the ribbon. The user sees these
    (via the existing research_progress block), so they should read as
    "what the agent is doing", not as tool names."""
    if tool_name == "browser_set":
        action = (args.get("action") or "").lower()
        if action == "text":
            return "Reading a section"
        if action == "snapshot":
            return "Mapping the page"
        if action == "scroll":
            return "Scrolling to a section"
        if action in ("click", "fill", "type", "press"):
            return "Interacting with the page"
        if action == "evaluate":
            return "Inspecting the page"
        return f"Browser: {action}"
    if tool_name == "read_url":
        return "Fetching a URL"
    if tool_name == "look_at_image":
        return "Looking at an image"
    if tool_name == "read_media":
        return "Checking the canvas"
    return tool_name


__all__ = ["smoke_test", "run_recipe"]
