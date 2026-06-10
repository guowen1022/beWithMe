"""Markdown → note HTML pipeline.

Phase 2.5: the canvas writer authors `note` content in CommonMark
markdown with three extensions:

  1. `==hi==`         → `<mark>hi</mark>`  (custom inline rule, ~25 lines)
  2. ```mermaid``` fenced code blocks become `<div class="bw-diagram"
     data-src="..."></div>` which the existing `process()` then
     renders to inline SVG.
  3. `$$...$$` / `$...$` → `<div class="math">` / `<span class="math">` —
     KaTeX renders these client-side in note.js after innerHTML is set.
     The sanitizer keeps the `math` class; `block`/`inline` are stripped.

Inline HTML is enabled so the writer can drop in occasional `<strong>`,
`<mark>`, `<span class="accent">` etc. for cases markdown alone can't
express. Everything still passes through the same nh3 allowlist via
`process()` — the markdown layer is purely a more comfortable author
surface for the LLM. No new sanitizer.

The renderer wraps its output in `<div class="card card-hero">…</div>`
so the existing `.bw-card .card-hero` CSS rules apply unchanged.
"""
from __future__ import annotations

import re
from typing import Final

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from infra.render.note import process as preprocess_note


_MERMAID_INFO_LANGS: Final = ("mermaid",)


def _mark_plugin(md: MarkdownIt) -> None:
    """Register a `==text==` inline rule that emits <mark>text</mark>.

    Mirrors the markdown-it.js `markdown-it-mark` plugin: two `=` chars
    on each side, no surrounding whitespace adjacent to `=`, content
    can span multiple inline tokens (so `==**bold** word==` works).
    """

    def tokenize_mark(state, silent):
        start = state.pos
        src = state.src
        # Need at least 4 chars for ==X==
        if start + 4 > len(src):
            return False
        if src[start] != "=" or src[start + 1] != "=":
            return False
        # Opening can't be followed by whitespace
        if src[start + 2].isspace():
            return False

        # Scan for the closing `==`. Bail on EOF or on a newline boundary
        # that's separated from the opening by another `==` (avoid
        # eating across paragraphs).
        scan = start + 2
        while scan < len(src) - 1:
            if src[scan] == "=" and src[scan + 1] == "=":
                # Closing can't be preceded by whitespace
                if not src[scan - 1].isspace():
                    break
            if src[scan] == "\n" and scan + 1 < len(src) and src[scan + 1] == "\n":
                return False
            scan += 1
        else:
            return False

        if silent:
            return True

        # Emit <mark>...inline children...</mark>
        token_open = state.push("mark_open", "mark", 1)
        token_open.markup = "=="

        # Recursively tokenize the inner content. We swap state.pos
        # and state.posMax so the inline parser walks just the inner
        # range, then restore.
        inner_start = start + 2
        inner_end = scan
        state.pos = inner_start
        state.posMax = inner_end
        state.md.inline.tokenize(state)
        state.pos = scan + 2
        state.posMax = len(src)

        token_close = state.push("mark_close", "mark", -1)
        token_close.markup = "=="
        return True

    md.inline.ruler.after("emphasis", "mark", tokenize_mark)

    # Default open/close render — minimal HTML.
    def render_mark_open(*_args):
        return "<mark>"

    def render_mark_close(*_args):
        return "</mark>"

    md.add_render_rule("mark_open", render_mark_open)
    md.add_render_rule("mark_close", render_mark_close)


def _build_markdown() -> MarkdownIt:
    """Construct the note markdown parser. Single global; no per-
    request state in markdown-it instances."""
    md = MarkdownIt("commonmark", {"html": True, "linkify": False, "breaks": False})
    md.enable(["table", "strikethrough"])
    _mark_plugin(md)
    # Parses $$...$$ (block) and $...$ (inline) into
    # <div class="math block"> / <span class="math inline"> elements.
    # The sanitizer keeps the "math" class; note.js renders them via KaTeX.
    md.use(dollarmath_plugin, allow_space=True, allow_digits=False)

    # Override fence renderer so ```mermaid fences become bw-diagram
    # divs that the existing note.process() will pick up and
    # render to SVG.
    default_fence = md.renderer.rules.get("fence")

    def render_fence(tokens, idx, options, env):
        token: Token = tokens[idx]
        info = (token.info or "").strip().split()
        lang = info[0] if info else ""
        if lang in _MERMAID_INFO_LANGS:
            # data-src must be HTML-attribute-safe — escape quotes.
            src = token.content or ""
            escaped = src.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            return f'<div class="bw-diagram" data-src="{escaped}"></div>\n'
        # ```plot {...}``` → coordinate-plot skill
        # ```skill:name {...}``` → named skill (extensible: drop a new .js file in
        # frontend/public/skills/ and it's available immediately, no code change needed)
        if lang == "plot" or lang.startswith("skill:"):
            skill_name = "coordinate-plot" if lang == "plot" else lang[len("skill:"):]
            src = (token.content or "").strip()
            escaped = src.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            return f'<div data-skill="{skill_name}" data-config="{escaped}"></div>\n'
        if default_fence is not None:
            return default_fence(tokens, idx, options, env)
        # Fallback: render as <pre><code>; will be stripped by sanitizer
        # if not in the allowlist (which it isn't), so this is unlikely
        # to land on the canvas.
        return f"<pre><code>{token.content}</code></pre>\n"

    md.renderer.rules["fence"] = render_fence
    return md


_MD = _build_markdown()


# Container shells the writer can name via a leading `<!--shell: ...-->`
# comment. Default is `card card-hero`; alternatives let the writer
# request a callout-styled card. Anything not in this set falls back
# to `card card-hero`.
_SHELLS = {
    "hero":      "card card-hero",
    "default":   "card card-hero",
    "callout":   "card card-callout",
    "compare":   "card card-compare",
    "timeline":  "card card-timeline",
    "definition":"card card-definition",
}
_SHELL_COMMENT_RE = re.compile(r"\A\s*<!--\s*shell:\s*([\w-]+)\s*-->\s*", re.IGNORECASE)


def _split_shell(md_text: str) -> tuple[str, str]:
    """Pull an optional leading `<!--shell: callout-->` directive off
    the markdown source. Returns (shell_classes, remaining_markdown)."""
    m = _SHELL_COMMENT_RE.match(md_text)
    if not m:
        return _SHELLS["default"], md_text
    name = m.group(1).strip().lower()
    classes = _SHELLS.get(name, _SHELLS["default"])
    return classes, md_text[m.end():]


async def render_markdown(md_text: str) -> str:
    """Render markdown → final note HTML (wrapped in the card shell).

    Steps:
      1. Strip an optional leading `<!--shell: …-->` directive.
      2. Parse markdown via markdown-it with the mark plugin enabled
         and the mermaid fence override active.
      3. Wrap output in the shell `<div class="card …">`.
      4. Hand the whole thing to `infra/render/note.process()` for
         sanitize + mermaid rendering + SVG inlining.

    Empty input → empty string.
    """
    md_text = (md_text or "").strip()
    if not md_text:
        return ""
    shell, body_md = _split_shell(md_text)
    body_html = _MD.render(body_md)
    wrapped = f'<div class="{shell}">{body_html}</div>'
    return await preprocess_note(wrapped)


async def render_markdown_fragment(md_text: str) -> str:
    """Render markdown → sanitized HTML *without* the card shell.

    Used by `edit_note` to convert per-op markdown snippets (e.g.
    `### New Section\\n\\n…`) into HTML the client can insert directly
    into an existing card body, without nesting another `.card` wrapper.

    Diagrams + sanitize still run via the shared pipeline.
    """
    md_text = (md_text or "").strip()
    if not md_text:
        return ""
    # Skip shell directive; per-op snippets shouldn't carry one.
    body_html = _MD.render(md_text)
    return await preprocess_note(body_html)


__all__ = ["render_markdown", "render_markdown_fragment"]
