"""Sanitizer table tests for the note preprocessor.

These tests deliberately do NOT exercise the Mermaid render path (which is
slow and requires a browser) — they feed input with NO `bw-diagram` divs so
`process()` short-circuits the renderer. Pipeline-level coverage (with
diagrams) is in test_note_pipeline.py.
"""
from __future__ import annotations

import asyncio

import pytest

from infra.render.note import process


def _run(html: str) -> str:
    return asyncio.run(process(html))


# ---- malicious inputs that must be neutralized -----------------------------

MALICIOUS_CASES: list[tuple[str, str, str]] = [
    ("script tag",          '<div class="card"><script>alert(1)</script></div>',          "<script"),
    ("event handler",       '<div class="card"><p onclick="x()">hi</p></div>',            "onclick"),
    ("javascript: href",    '<div class="card"><a href="javascript:alert(1)">x</a></div>',"javascript:"),
    ("data: image",         '<div class="card"><img src="data:image/png;base64,AA"/></div>',"data:image"),
    ("iframe",              '<div class="card"><iframe src="https://x"></iframe></div>',  "<iframe"),
    ("inline style",        '<div class="card"><p style="color:red">x</p></div>',         'style="color'),
    ("disallowed tag",      '<div class="card"><table><tr><td>x</td></tr></table></div>', "<table"),
    ("form",                '<div class="card"><form><input/></form></div>',              "<form"),
    ("svg passthrough",     '<div class="card"><svg><script>x</script></svg></div>',      "<svg"),
    ("foreignObject",       '<div class="card"><svg><foreignObject>x</foreignObject></svg></div>', "foreignObject"),
    ("link tag",            '<div class="card"><link rel="stylesheet" href="https://e/x.css"/></div>', "<link"),
    ("meta tag",            '<div class="card"><meta http-equiv="refresh" content="0"/></div>', "<meta"),
    ("http href",           '<div class="card"><a href="http://x">x</a></div>',           'href="http:'),
    ("vbscript href",       '<div class="card"><a href="vbscript:msgbox">x</a></div>',    "vbscript:"),
    ("unknown class",       '<div class="card unknown-class">x</div>',                    "unknown-class"),
    ("style block",         '<div class="card"><style>body{display:none}</style></div>',  "<style"),
    ("button tag",          '<div class="card"><button onclick="x()">go</button></div>',  "<button"),
    ("input tag",           '<div class="card"><input type="text"/></div>',               "<input"),
    ("video tag",           '<div class="card"><video src="https://x"></video></div>',    "<video"),
    ("audio tag",           '<div class="card"><audio src="https://x"></audio></div>',    "<audio"),
    ("canvas tag",          '<div class="card"><canvas></canvas></div>',                  "<canvas"),
    ("object tag",          '<div class="card"><object data="https://x"></object></div>', "<object"),
    ("embed tag",           '<div class="card"><embed src="https://x"/></div>',           "<embed"),
    ("img http src",        '<div class="card"><img src="http://x/y.png"/></div>',        'src="http:'),
    ("onerror img",         '<div class="card"><img src="https://x/y.png" onerror="alert(1)"/></div>', "onerror"),
    ("nested attack",       '<div class="card"><p><span><script>alert(1)</script></span></p></div>', "<script"),
    ("html entity bypass",  '<div class="card"><p>&lt;script&gt;alert(1)&lt;/script&gt;</p></div>', "<script>alert"),
    ("class with bad token",'<div class="accent unknown-class card">x</div>',             "unknown-class"),
    ("disallowed h5",       '<div class="card"><h5>too deep</h5></div>',                  "<h5>"),
    ("disallowed h6",       '<div class="card"><h6>too deep</h6></div>',                  "<h6>"),
]


@pytest.mark.parametrize("name,html,forbidden", MALICIOUS_CASES, ids=[c[0] for c in MALICIOUS_CASES])
def test_malicious_inputs_are_stripped(name: str, html: str, forbidden: str) -> None:
    out = _run(html).lower()
    assert forbidden.lower() not in out, f"{name}: '{forbidden}' survived sanitization"


# ---- allowed inputs that must round-trip ----------------------------------

ALLOWED_CASES: list[tuple[str, str, str]] = [
    ("card class",         '<div class="card"><p>x</p></div>',                'class="card"'),
    ("card-hero",          '<div class="card card-hero"><p>x</p></div>',     "card-hero"),
    ("accent span",        '<div class="card"><span class="accent">x</span></div>', "accent"),
    ("t-display heading",  '<div class="card"><h2 class="t-display">T</h2></div>',  "t-display"),
    ("mark highlight",     '<div class="card"><p>a <mark>b</mark> c</p></div>',     "<mark>"),
    ("ins / del",          '<div class="card"><p><ins>add</ins> <del>old</del></p></div>', "<ins>"),
    ("https link",         '<div class="card"><a href="https://example.com/x">x</a></div>', "https://example.com"),
    ("https img w/ dims",  '<div class="card"><img class="bw-image aspect-16-9" src="https://e/x.png" alt="x" width="800" height="450"/></div>', "https://e/x.png"),
    ("ul / li",            '<div class="card"><ul><li>a</li><li>b</li></ul></div>',  "<ul>"),
    ("ol / li",            '<div class="card"><ol><li>1</li></ol></div>',            "<ol>"),
    ("h1 through h4",      '<div class="card"><h1>1</h1><h2>2</h2><h3>3</h3><h4>4</h4></div>', "<h4>"),
    ("blockquote",         '<div class="card"><blockquote>q</blockquote></div>',     "<blockquote>"),
    ("strong + em",        '<div class="card"><p><strong>s</strong> <em>e</em></p></div>', "<strong>"),
    ("code inline",        '<div class="card"><p><code>x</code></p></div>',          "<code>"),
    ("hr + br",            '<div class="card"><p>a<br/>b</p><hr/></div>',            "<hr"),
    ("compare card",       '<div class="card card-compare"><div class="col">L</div><div class="col">R</div></div>', "card-compare"),
    ("revision marks",     '<div class="card"><span class="revision-add">+a</span></div>', "revision-add"),
    ("aspect class",       '<div class="card"><img class="bw-image aspect-4-3" src="https://e/x.png" alt="x"/></div>', "aspect-4-3"),
    ("nested containers",  '<div class="card"><section class="row"><div class="col pad-md">x</div></section></div>', "pad-md"),
    ("tone classes",       '<div class="card"><span class="danger">!</span><span class="success">✓</span></div>', "danger"),
]


@pytest.mark.parametrize("name,html,must_contain", ALLOWED_CASES, ids=[c[0] for c in ALLOWED_CASES])
def test_allowed_inputs_round_trip(name: str, html: str, must_contain: str) -> None:
    out = _run(html)
    assert must_contain in out, f"{name}: expected '{must_contain}' in sanitized output: {out!r}"


def test_empty_input_returns_empty_string() -> None:
    assert _run("") == ""
    assert _run("   \n\t  ") == ""


def test_link_gets_rel_noopener() -> None:
    out = _run('<div class="card"><a href="https://example.com">x</a></div>')
    assert 'rel="noopener noreferrer"' in out
