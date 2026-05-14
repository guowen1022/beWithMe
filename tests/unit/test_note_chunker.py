"""Unit tests for the block-group markdown chunker."""
from __future__ import annotations

from workshop.canvas.tools._note_chunker import chunk_note_markdown


def test_empty_returns_empty() -> None:
    assert chunk_note_markdown("") == []
    assert chunk_note_markdown("   \n\n  ") == []


def test_single_block_one_chunk() -> None:
    md = "A short paragraph with no heading."
    chunks = chunk_note_markdown(md)
    assert len(chunks) == 1
    block_start, block_end, text = chunks[0]
    assert block_start == 0
    assert block_end == 0
    assert text == md


def test_heading_prefix_attached_to_body() -> None:
    md = "## Attention\n\nThe query attends to keys via scaled dot-product."
    chunks = chunk_note_markdown(md, target_words=1000)
    # Heading + paragraph fit in one chunk; chunk starts with the heading.
    assert len(chunks) == 1
    _, _, text = chunks[0]
    assert text.startswith("## Attention")
    assert "scaled dot-product" in text


def test_new_heading_flushes_chunk() -> None:
    md = (
        "## Section A\n\n"
        "Body A — one short sentence.\n\n"
        "## Section B\n\n"
        "Body B — another short sentence."
    )
    chunks = chunk_note_markdown(md, target_words=1000)
    # Even though both sections would fit under target_words, the heading
    # boundary forces a flush so chunks don't span sections.
    assert len(chunks) == 2
    assert chunks[0][2].startswith("## Section A")
    assert "Body A" in chunks[0][2]
    assert chunks[1][2].startswith("## Section B")
    assert "Body B" in chunks[1][2]


def test_target_words_split_within_section() -> None:
    # Two long paragraphs under one heading. Target small enough to force a
    # split between them. The second chunk should carry the heading prefix.
    para1 = " ".join(["alpha"] * 80)
    para2 = " ".join(["beta"] * 80)
    md = f"## Topic\n\n{para1}\n\n{para2}"
    chunks = chunk_note_markdown(md, target_words=50)
    assert len(chunks) >= 2
    # First chunk: heading + para1.
    assert chunks[0][2].startswith("## Topic")
    assert "alpha" in chunks[0][2]
    # Last chunk: para2 with heading prefix re-attached.
    last_text = chunks[-1][2]
    assert last_text.startswith("## Topic")
    assert "beta" in last_text


def test_fenced_code_block_kept_intact() -> None:
    md = (
        "## Code\n\n"
        "Intro paragraph.\n\n"
        "```python\n"
        "def f():\n"
        "    return 1\n"
        "```\n\n"
        "Trailing paragraph."
    )
    chunks = chunk_note_markdown(md, target_words=1000)
    # All three non-heading blocks fit in one chunk under the heading.
    assert len(chunks) == 1
    text = chunks[0][2]
    assert "```python" in text
    assert "def f():" in text


def test_block_ranges_cover_all_blocks_exactly_once() -> None:
    md = (
        "## A\n\npara 1\n\n## B\n\npara 2\n\npara 3\n\n## C\n\npara 4"
    )
    chunks = chunk_note_markdown(md, target_words=1000)
    # Walk the (start, end) ranges; every index 0..N-1 should be covered
    # exactly once.
    covered: list[int] = []
    for start, end, _ in chunks:
        assert start <= end
        covered.extend(range(start, end + 1))
    assert covered == sorted(covered)  # in order
    assert len(set(covered)) == len(covered)  # no duplicates
    assert covered[0] == 0  # starts at 0
