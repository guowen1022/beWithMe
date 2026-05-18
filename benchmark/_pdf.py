"""Minimal pure-Python PDF writer.

We need a PDF fixture for the file-understanding benchmark, and the rest
of the project pulls in pypdf for *reading* PDFs (not writing). Rather
than adding reportlab as a dep just for the benchmark, this module emits
a small, valid single-/multi-page text PDF that pypdf's `PdfReader` can
parse text out of. ASCII-only — the WinAnsiEncoding font isn't a full
Unicode font, so non-ASCII chars are stripped before writing.

Spec: PDF 1.4 (subset). One Helvetica font, one MediaBox (Letter),
wrapped lines flowed across as many pages as needed.
"""

from __future__ import annotations

import textwrap


PAGE_WIDTH = 612
PAGE_HEIGHT = 792
MARGIN_LEFT = 50
MARGIN_TOP = 750  # y position of first line (bottom-left origin)
FONT_SIZE = 11
LINE_LEADING = 14  # ≈ 1.27× font size
WRAP_WIDTH = 90
LINES_PER_PAGE = 50


def _escape(s: str) -> str:
    """PDF text-string escapes: backslash, parens, and strip non-ASCII."""
    s = s.encode("ascii", "ignore").decode("ascii")
    return s.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")


def _wrap_to_pages(text: str) -> list[list[str]]:
    """Wrap `text` into pages of LINES_PER_PAGE lines each. Paragraph
    breaks (double newlines) yield a blank line."""
    lines: list[str] = []
    paragraphs = [p.rstrip() for p in text.split("\n\n")]
    for i, para in enumerate(paragraphs):
        wrapped = textwrap.wrap(para, width=WRAP_WIDTH) if para else [""]
        lines.extend(wrapped or [""])
        if i < len(paragraphs) - 1:
            lines.append("")
    return [
        lines[i : i + LINES_PER_PAGE]
        for i in range(0, len(lines), LINES_PER_PAGE)
    ] or [[""]]


def _content_stream(page_lines: list[str]) -> bytes:
    out: list[str] = [
        "BT",
        f"/F1 {FONT_SIZE} Tf",
        f"{LINE_LEADING} TL",
        f"{MARGIN_LEFT} {MARGIN_TOP} Td",
    ]
    for ln in page_lines:
        out.append(f"({_escape(ln)}) Tj T*")
    out.append("ET")
    return "\n".join(out).encode("latin-1")


def text_to_pdf_bytes(text: str) -> bytes:
    """Return PDF bytes containing `text`. Suitable for direct POST to
    `/api/documents/upload` as multipart file content."""
    pages = _wrap_to_pages(text)
    n_pages = len(pages)

    # Object layout:
    #   1 Catalog
    #   2 Pages
    #   3,5,7,...   Page objects
    #   4,6,8,...   Content streams
    #   last       Font
    page_ids = [3 + 2 * i for i in range(n_pages)]
    content_ids = [pid + 1 for pid in page_ids]
    font_id = page_ids[-1] + 2 if page_ids else 3

    objs: list[bytes] = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("latin-1")
    )
    for pid, cid, plines in zip(page_ids, content_ids, pages):
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Contents {cid} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode("latin-1")
        )
        stream = _content_stream(plines)
        objs.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )
    objs.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )

    out = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_offset = len(out)
    n_obj = len(objs)
    out += f"xref\n0 {n_obj + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {n_obj + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")
    return out
