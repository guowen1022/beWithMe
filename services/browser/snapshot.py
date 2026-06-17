"""Accessibility-snapshot + @ref resolution for the headless browser session.

Captures Playwright's ARIA snapshot (a YAML representation of the page's
accessibility tree), assigns `@e1, @e2, ...` refs to interesting nodes, and
stores a ref → Locator map on `app.state.session_refs`. The session handlers
(`services/browser/session.py`) accept `@e<n>` in their `selector` slot and
resolve it here. Extracted verbatim from `main.py` (F6).
"""
from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException


# Roles whose lines get a `@e<n>` ref. Other roles still appear in the
# tree text (preserve structure for the reader) but aren't addressable.
# Picked for research workflows — headings + content blocks + primary
# interactive elements. Excludes:
#   - link: Wikipedia-class pages have hundreds of citation links that
#     would exhaust the ref budget. The LLM can still SEE them in the
#     tree text and navigate using their @ref containers (heading,
#     region) if needed.
#   - listitem / list / group / generic: structural noise.
_REF_ROLES = {
    # Landmarks / sections
    "main", "region", "navigation", "banner", "complementary",
    "contentinfo", "article", "search", "form", "dialog", "tabpanel",
    # Headings — the primary navigation surface for content pages.
    # text(@heading) returns the WHOLE section under it (section-aware),
    # so the agent rarely needs to address individual paragraphs.
    "heading",
    # Interactive (no link — see comment above)
    "button", "textbox", "combobox", "listbox", "checkbox",
    "radio", "tab", "menuitem", "switch", "slider", "spinbutton",
    "treeitem", "option",
    # Content blocks — exclude paragraph (too many; sections cover them)
    "blockquote", "table", "img", "figure", "code", "math",
    # Status
    "alert", "alertdialog", "status", "tooltip",
}

# Max refs per snapshot. Wikipedia's HTTP/2 page produces ~87 KB of raw
# aria_snapshot YAML; capping keeps the LLM-visible tree scannable.
MAX_REFS = 250
MAX_NAME_LEN = 80
_MAX_TREE_CHARS = 16000

# Selectors we try (in order) to auto-scope snapshots to the page's main
# content area. Most content sites bury the article in a region marked
# with one of these — bypassing it wastes refs on nav/sidebar/footer
# clutter. The agent can override by passing an explicit selector.
DEFAULT_MAIN_SCOPES = (
    "main",
    "[role='main']",
    "article",
    "#bodyContent",      # Wikipedia
    "#content",
    "#main",
)


# Parses one line of Playwright's aria_snapshot YAML. Examples:
#   - heading "Photosynthesis" [level=1]:
#   - link "Jump to content"
#   - button "Search" [disabled]
#   - paragraph: Some text content here
#   - /url: "#bodyContent"        (skipped; metadata line)
#   - text: "Some text"           (skipped; static text)
_LINE_RE = re.compile(
    r"^(?P<indent>\s*)-\s+"          # bullet + leading indent
    r"(?P<role>[a-zA-Z][a-zA-Z0-9_-]*)"
    r"(?:\s+\"(?P<name>(?:\\.|[^\"\\])*)\")?"  # optional "name"
    r"(?P<attrs>(?:\s+\[[^\]]*\])*)"           # zero or more [attr=value]
    r"(?P<rest>:.*)?$"
)


def _build_locator(page, role: str, name: str, occurrence: int):
    """Return a Playwright Locator addressing the `occurrence`-th node with
    this (role, name) pair. None if Playwright can't construct it.

    `exact=True` matters — without it, Playwright matches names by regex,
    so a heading "Evolution" matches "Cyanobacteria and the evolution
    of photosynthesis" too, triggering strict-mode violations when we
    later call .evaluate(). We always want the exact match the ARIA
    snapshot reported.
    """
    try:
        if name:
            loc = page.get_by_role(role, name=name, exact=True)
        else:
            loc = page.get_by_role(role)
        if occurrence > 0:
            loc = loc.nth(occurrence)
        return loc
    except Exception:
        return None


def parse_aria_snapshot(yaml_text: str, page):
    """Walk the Playwright aria_snapshot YAML output line by line. For each
    line whose role is in `_REF_ROLES`, assign a `@e<n>` ref and build a
    Playwright Locator. Returns (refs_map, tree_text)."""
    refs_map: dict[str, dict] = {}
    counter: dict[tuple[str, str], int] = {}
    out_lines: list[str] = []

    for line in yaml_text.split("\n"):
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        if not m:
            # Metadata lines like `- /url: ...` or `- text: ...` are
            # preserved in the tree for context but don't get refs.
            out_lines.append(line.rstrip())
            continue
        role = m.group("role").lower()
        # Skip lines like `- /url: ...` (role starts with /). The regex
        # above wouldn't match them, but defensive.
        if role.startswith("/"):
            out_lines.append(line.rstrip())
            continue
        name = m.group("name") or ""
        if name:
            # YAML-style escape: \" → "
            name = name.replace('\\"', '"').replace("\\\\", "\\")
        attrs = m.group("attrs") or ""

        if role in _REF_ROLES and len(refs_map) < MAX_REFS:
            key = (role, name)
            occurrence = counter.get(key, 0)
            counter[key] = occurrence + 1
            ref = f"@e{len(refs_map) + 1}"
            refs_map[ref] = {
                "role": role,
                "name": name,
                "occurrence": occurrence,
                "attrs": attrs,
                "locator": _build_locator(page, role, name, occurrence),
            }
            # Prepend the ref to the line, keeping original indent.
            indent = m.group("indent") or ""
            display_name = (
                f' "{name[:MAX_NAME_LEN - 1]}…"'
                if len(name) > MAX_NAME_LEN
                else (f' "{name}"' if name else "")
            )
            out_lines.append(f"{indent}- {ref} {role}{display_name}{attrs}")
        else:
            # Keep the line for structural context but don't add a ref.
            out_lines.append(line.rstrip())

    tree_text = "\n".join(out_lines)
    if len(tree_text) > _MAX_TREE_CHARS:
        tree_text = tree_text[:_MAX_TREE_CHARS] + f"\n…[tree truncated, {len(tree_text)} total chars]"
    return refs_map, tree_text


async def resolve_locator(app: FastAPI, page, sel: str | None):
    """If `sel` is an @e<n> ref, return the stored Locator. Otherwise
    return None and the caller can use `sel` as a raw selector string."""
    if not sel or not sel.startswith("@e"):
        return None
    refs = getattr(app.state, "session_refs", None) or {}
    entry = refs.get(sel)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown ref {sel!r}. Refs invalidate on goto/reload/back/"
                "forward — call action='snapshot' to get a fresh set."
            ),
        )
    locator = entry.get("locator")
    if locator is None:
        raise HTTPException(
            status_code=400,
            detail=f"ref {sel!r} has no locator (was built on a different page)",
        )
    return locator


def invalidate_refs(app: FastAPI) -> None:
    """Clear the @ref → Locator map. Call on any nav (goto/reload/back/forward)
    — the DOM has changed, refs from the previous page point at nothing."""
    app.state.session_refs = {}


def level_from_attrs(attrs_str: str) -> int:
    """Extract `level=N` from an attribute string like ` [level=2]`."""
    m = re.search(r"\[level=(\d+)\]", attrs_str or "")
    return int(m.group(1)) if m else 0


# Section-text JS — runs in the page when the @ref points at a heading.
# Walks forward from the heading, collecting innerText of every following
# sibling, until it hits another heading of same-or-higher level.
#
# Wikipedia (and many sites) wrap H2s in a container like
# <div class="mw-heading"><h2>...</h2><span class="mw-editsection">...</span></div>.
# Walking siblings of the H2 itself returns just "[edit]" because the H2 is
# the last child of its wrapper. We detect this and walk from the wrapper
# instead, scanning subsequent siblings for any heading that ends the section.
SECTION_TEXT_JS = """
(el) => {
  if (!el) return '';
  // Find the H1-H6 element. el might already be one, or a thin wrapper.
  let h = /^H[1-6]$/i.test(el.tagName)
    ? el
    : (el.querySelector ? el.querySelector('h1,h2,h3,h4,h5,h6') : null);
  if (!h) return (el.innerText || '').trim();
  const level = parseInt(h.tagName[1]);
  // If h is wrapped in a thin container whose ONLY heading is h (typical
  // pattern: Wikipedia's .mw-heading), walk from the wrapper so the
  // section's first content sibling actually becomes accessible. But
  // only ascend ONCE — going further hits the article body, which
  // contains many headings, and we'd grab everything.
  let walkFrom = h;
  const parent = h.parentElement;
  if (
    parent &&
    parent !== document.body &&
    parent.querySelectorAll('h1,h2,h3,h4,h5,h6').length === 1
  ) {
    walkFrom = parent;
  }
  function levelOf(node) {
    if (/^H[1-6]$/i.test(node.tagName || '')) return parseInt(node.tagName[1]);
    if (node.querySelector) {
      const sub = node.querySelector('h1,h2,h3,h4,h5,h6');
      return sub ? parseInt(sub.tagName[1]) : 0;
    }
    return 0;
  }
  const parts = [(walkFrom.innerText || '').trim()];
  let cur = walkFrom.nextElementSibling;
  // Safety cap on total chars walked — wikipedias can have very long
  // sections; we want enough for the LLM to reason but not the entire
  // article. The downstream _do_text further truncates to 8000.
  let total = parts[0].length;
  while (cur && total < 20000) {
    const lvl = levelOf(cur);
    if (lvl > 0 && lvl <= level) break;
    const t = (cur.innerText || '').trim();
    if (t) {
      parts.push(t);
      total += t.length;
    }
    cur = cur.nextElementSibling;
  }
  return parts.join('\\n\\n');
}
"""
