"""Block-group chunker for markdown notes.

A note's .md source is a sequence of top-level markdown blocks — headings,
paragraphs, lists, fenced code, mermaid fences, tables, blockquotes. The
chunker walks them in order, groups them until ~`target_words` accumulates,
and flushes the group as one chunk. Headings act as soft chunk boundaries:
when a new heading appears, the current chunk flushes so the next chunk
starts under the fresh heading. Whichever heading most recently appeared is
prepended to a chunk body that doesn't already start with one, so semantic
context (`"## Section name\n\n<body>"`) survives into the embedding.

Pure function — no DB, no HTTP, no async. Lives next to `_note_cache.py`
because both pieces are part of the note authoring pipeline; the knowledge
sidecar stays ignorant of markdown grammar and just embeds the texts.
"""
from __future__ import annotations

from typing import List, Tuple

from markdown_it import MarkdownIt


_MD_PARSER = MarkdownIt("commonmark", {"html": True})
_MD_PARSER.enable(["table", "strikethrough"])


def _top_level_blocks(md: str) -> List[Tuple[str, str]]:
    """Tokenize `md` into top-level blocks. Returns [(kind, text), ...] in
    document order. `kind` is one of 'heading', 'paragraph', 'bullet_list',
    'ordered_list', 'fence', 'code_block', 'blockquote', 'table', 'hr',
    'html_block'. Inline children of containers stay nested inside the
    container's slice — we don't descend.
    """
    lines = md.splitlines()
    out: List[Tuple[str, str]] = []
    for tok in _MD_PARSER.parse(md):
        # Only level-0 tokens are top-level blocks. Take opening tokens of
        # containers, or self-contained leaf tokens (fence, code_block, hr,
        # html_block).
        if tok.level != 0:
            continue
        is_open = tok.type.endswith("_open")
        is_leaf = tok.type in ("fence", "code_block", "hr", "html_block")
        if not (is_open or is_leaf):
            continue
        if tok.map is None:
            continue
        ln_start, ln_end = tok.map
        text = "\n".join(lines[ln_start:ln_end]).rstrip()
        if not text.strip():
            continue
        kind = tok.type[:-5] if is_open else tok.type
        out.append((kind, text))
    return out


def chunk_note_markdown(
    md: str,
    *,
    target_words: int = 250,
) -> List[Tuple[int, int, str]]:
    """Group consecutive top-level markdown blocks into chunks of ~`target_words`.

    Returns `[(block_start, block_end, chunk_text), ...]` where the block
    indices are 0-based positions in the ordered top-level block list
    (inclusive on both ends), and `chunk_text` is markdown ready to embed —
    prefixed with the nearest preceding heading when the chunk's body
    doesn't already start with one.

    A heading always flushes whatever chunk is open, so chunks don't span
    two sections. A single oversized block (e.g. a long mermaid) is emitted
    as one chunk even if it exceeds `target_words`.
    """
    if not md or not md.strip():
        return []

    blocks = _top_level_blocks(md)
    if not blocks:
        return []

    chunks: List[Tuple[int, int, str]] = []
    last_heading: str = ""
    current_indices: List[int] = []
    current_kinds: List[str] = []
    current_text: List[str] = []
    current_words = 0

    def _has_body() -> bool:
        return any(k != "heading" for k in current_kinds)

    def flush() -> None:
        nonlocal current_indices, current_kinds, current_text, current_words
        if not current_indices:
            return
        body = "\n\n".join(current_text)
        if last_heading and not body.lstrip().startswith("#"):
            body = f"{last_heading}\n\n{body}"
        chunks.append((current_indices[0], current_indices[-1], body))
        current_indices = []
        current_kinds = []
        current_text = []
        current_words = 0

    for idx, (kind, text) in enumerate(blocks):
        words = len(text.split())
        if kind == "heading":
            # Heading boundary: flush only when the current chunk already has
            # a non-heading body. Trailing-heading-only buffers stay open so
            # the next paragraph carries the heading along with it.
            if _has_body():
                flush()
            last_heading = text
        elif _has_body() and current_words + words > target_words:
            flush()
        current_indices.append(idx)
        current_kinds.append(kind)
        current_text.append(text)
        current_words += words

    flush()
    return chunks


__all__ = ["chunk_note_markdown"]
