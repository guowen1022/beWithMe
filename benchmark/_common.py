"""Shared helpers for the benchmark sub-packages.

Every benchmark family (`benchmark.model_behavior`, `benchmark.goal_planning`,
`benchmark.file_understanding`) imports from here so the HTTP/SSE plumbing,
DB reset, file-upload helpers, and run-artifact writers stay in one place.
Adapted from the older monolithic `benchmark/runner.py`.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
import yaml
from dotenv import load_dotenv

from benchmark._pdf import text_to_pdf_bytes

# Load .env so env_snapshot() sees the same LLM_PROVIDER / LLM_MODEL the
# backend services use — the backend loads it at import via each module's
# config.py, but the benchmark CLI is a separate process and won't pick
# it up otherwise.
load_dotenv()


DB_URL = "postgresql://weng@localhost/bewithme"


# ---- DB ----

async def reset_db() -> None:
    """Wipe the rows the benchmark cares about. Same tables the old runner
    cleared — kept verbatim so behaviour matches across the migration."""
    conn = await asyncpg.connect(DB_URL)
    for table in (
        "concept_edges",
        "concept_nodes",
        "interactions",
        "document_chunks",
        "documents",
        "learning_preferences",
        "profile",
        "users",
    ):
        await conn.execute(f"DELETE FROM {table}")
    await conn.close()
    print("[reset] Database cleared")


# ---- HTTP ----

def auth_headers(
    user_id: str, *, device_class: str | None = None
) -> dict[str, str]:
    """Auth headers for benchmark requests.

    `device_class` (desktop|tablet|phone) is forwarded as `X-Device-Class`
    so the persona router resolves the right talk channel + prompt
    builder. Omitted by default to keep existing call sites unchanged.
    """
    h = {"Content-Type": "application/json", "X-User-Id": user_id}
    if device_class:
        h["X-Device-Class"] = device_class
    return h


async def create_or_get_user(client: httpx.AsyncClient, username: str) -> str:
    resp = await client.post("/api/users", json={"username": username})
    if resp.status_code == 409:
        resp = await client.get("/api/users")
        users = resp.json()
        return next(u["id"] for u in users if u["username"] == username)
    resp.raise_for_status()
    return resp.json()["id"]


async def set_profile(client: httpx.AsyncClient, headers: dict, profile: str) -> None:
    await client.put(
        "/api/profile", headers=headers, json={"self_description": profile}
    )


async def seed_preferences(
    client: httpx.AsyncClient, headers: dict, preferences: dict | None
) -> dict | None:
    """PUT user-stated preferences. Pydantic v2 ignores unknown fields by
    default, so YAML keys for upcoming Layer-2 traits won't break this once
    they're added. Returns the server's view after the put, or None when
    nothing was seeded."""
    if not preferences:
        return None
    resp = await client.put("/api/preferences", headers=headers, json=preferences)
    resp.raise_for_status()
    return resp.json()


async def set_talk_preference(
    client: httpx.AsyncClient, headers: dict, pref: dict | None
) -> dict | None:
    """PUT a per-device-class talk preference. Accepts a partial dict like
    `{"desktop": "voice"}` and fills missing fields from the user's
    current preference so the PUT (which requires all three of desktop/
    tablet/phone) succeeds. Returns the server's view after the put, or
    None when nothing was seeded.
    """
    if not pref:
        return None
    fallback = {"desktop": "both", "tablet": "both", "phone": "text"}
    try:
        cur_resp = await client.get("/api/talk-preference", headers=headers)
        if cur_resp.status_code == 200:
            base = cur_resp.json()
        else:
            base = fallback
    except Exception:
        base = fallback
    payload = {**base, **{k: v for k, v in pref.items() if v}}
    resp = await client.put("/api/talk-preference", headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


async def end_session(client: httpx.AsyncClient, headers: dict, session_id: str) -> None:
    try:
        resp = await client.post(f"/api/sessions/{session_id}/end", headers=headers)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [end session] ERROR: {e}", flush=True)


async def safe_json(
    client: httpx.AsyncClient, path: str, headers: dict, fallback: Any
) -> Any:
    try:
        r = await client.get(path, headers=headers)
        if r.status_code != 200:
            print(f"[warn] {path} -> {r.status_code}; using fallback")
            return fallback
        return r.json()
    except Exception as e:
        print(f"[warn] {path} failed: {type(e).__name__}: {e}; using fallback")
        return fallback


async def ask_one(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    passage: str,
    selected_text: str | None,
    question: str,
    session_id: str,
    document_id: str | None = None,
    encourage_visual: bool = False,
) -> dict:
    """Single POST to /api/ask/stream. Parses the SSE stream and returns
    a result dict including the full `answer_text`, the assembled prompt
    parts (from the route's existing `debug` SSE event), and timing.

    Pass `document_id` when the question references an uploaded PDF — the
    route stores it on the Interaction row so the doc/Q&A link survives.

    When `encourage_visual=True`, a short one-line nudge is appended to
    the question — neutral wording that reminds the teacher its diagram /
    speak tools are available without dictating that they be used. The
    final phrasing lands in the SSE `debug.dynamic_user` so reviewers can
    see exactly what was asked.

    On any HTTP / transport error returns the same dict shape with an
    extra `error` key set."""
    effective_question = question
    if encourage_visual:
        effective_question = (
            f"{question}\n\n"
            "(Use a diagram via interactive_graph or speak the answer "
            "out loud if either would genuinely help — otherwise just "
            "answer in text.)"
        )
    payload: dict = {
        "passage_text": passage,
        "selected_text": selected_text,
        "question": effective_question,
        "session_id": session_id,
    }
    if document_id:
        payload["document_id"] = document_id
    start = time.time()
    try:
        resp = await client.post("/api/ask/stream", headers=headers, json=payload)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return _error_result(question, selected_text, str(e))
    except (
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.ReadTimeout,
        httpx.ConnectError,
    ) as e:
        return _error_result(question, selected_text, f"{type(e).__name__}: {e}")

    answer_text = ""
    prompt_parts: dict | None = None
    usage: dict = {}
    tool_calls: list[dict] = []
    title = None

    for line in resp.text.split("\n"):
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        et = event.get("type")
        if et == "answer":
            answer_text = event.get("answer", "")
            title = event.get("title")
        elif et == "debug":
            prompt_parts = {
                "static_system": event.get("static_system", ""),
                "static_user_passage": event.get("static_user_passage", ""),
                "dynamic_user": event.get("dynamic_user", ""),
                "prior_message_count": event.get("prior_message_count", 0),
            }
            usage = event.get("usage", {}) or usage
        elif et == "tool_call":
            tool_calls.append(
                {"name": event.get("name"), "arguments": event.get("arguments", {})}
            )

    elapsed = time.time() - start
    concepts_line = ""
    for ln in answer_text.split("\n"):
        if ln.strip().upper().startswith("CONCEPTS:"):
            concepts_line = ln.strip()
            break

    return {
        "question": question,
        "selected_text": selected_text,
        "answer_text": answer_text,
        "answer_length": len(answer_text),
        "title": title,
        "concepts_line": concepts_line,
        "elapsed_seconds": round(elapsed, 2),
        "prompt_parts": prompt_parts,
        "usage": usage,
        "tool_calls": tool_calls,
        "multimodal": _build_multimodal(tool_calls),
        "encourage_visual": encourage_visual,
    }


def _error_result(question: str, selected_text: str | None, err: str) -> dict:
    return {
        "question": question,
        "selected_text": selected_text,
        "answer_text": "",
        "answer_length": 0,
        "title": None,
        "concepts_line": "",
        "elapsed_seconds": 0.0,
        "prompt_parts": None,
        "usage": {},
        "tool_calls": [],
        "multimodal": _build_multimodal([]),
        "error": err,
    }


# ---- Multi-modal output normalization ----
#
# The teacher persona exposes diagram / voice / canvas tools (see
# `persona/teacher/tools/manifest.py`). When the LLM invokes one, the
# /api/ask/stream route forwards it as a `tool_call` SSE event with the
# tool's name + arguments. _build_multimodal() reshapes the raw
# tool_calls list into review-friendly buckets so results.json and
# answers.md can render the spoken text, the mermaid source, and the
# canvas mutations without anyone having to grok argument schemas.

_MERMAID_KIND_FIRST_WORD = {
    "flowchart": "flowchart",
    "graph": "flowchart",
    "sequencediagram": "sequence",
    "classdiagram": "class",
    "statediagram": "state",
    "statediagram-v2": "state",
    "erdiagram": "er",
    "journey": "journey",
    "gantt": "gantt",
    "pie": "pie",
    "mindmap": "mindmap",
    "timeline": "timeline",
    "sankey-beta": "sankey",
    "xychart-beta": "xychart",
    "quadrantchart": "quadrant",
    "requirementdiagram": "requirement",
    "gitgraph": "gitgraph",
}


def _detect_mermaid_kind(mermaid: str) -> str:
    """Best-effort: pull the first non-empty line and map its first
    token to a short kind label ('flowchart', 'sequence', ...). Returns
    'unknown' if we can't tell — used only as a hint in the review
    artifacts, never load-bearing."""
    for raw in (mermaid or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        first = line.split(None, 1)[0].lower().rstrip(":")
        return _MERMAID_KIND_FIRST_WORD.get(first, first or "unknown")
    return "unknown"


def _build_multimodal(tool_calls: list[dict]) -> dict:
    """Normalize raw tool_call events into category buckets.

    Each tool the teacher can invoke maps to one bucket; unknown tools
    fall through into `other_tool_names` so we don't silently drop new
    additions to the manifest. Caller still gets the full raw list via
    the sibling `tool_calls` field on the result row."""
    spoken: list[dict] = []
    diagrams: list[dict] = []
    annotations: list[dict] = []
    mounted_templates: list[dict] = []
    delegations: list[dict] = []
    other: list[str] = []
    for tc in tool_calls:
        name = (tc.get("name") or "").strip()
        args = tc.get("arguments") or {}
        if name == "speak":
            spoken.append({
                "text": args.get("text", ""),
                "channel": args.get("channel") or "both",
                "source": "tool",
            })
        elif name == "interactive_graph":
            mermaid = args.get("mermaid", "") or ""
            diagrams.append({
                "name": args.get("name") or "main",
                "kind": _detect_mermaid_kind(mermaid),
                "mermaid": mermaid,
                "highlight_node": args.get("highlight_node"),
                "clear": bool(args.get("clear")),
            })
        elif name == "point_arrow":
            label = args.get("label") or ""
            summary = (
                f"arrow {args.get('from_block_id', '?')} → "
                f"{args.get('to_block_id', '?')}"
            )
            if label:
                summary += f" [{label}]"
            annotations.append({"tool": name, "summary": summary})
        elif name == "mount_template":
            params = args.get("params") or {}
            mounted_templates.append({
                "template": args.get("template", ""),
                "slug": args.get("slug"),
                "params_keys": sorted(params.keys()) if isinstance(params, dict) else [],
            })
        elif name in ("request_new_block", "request_ui_block"):
            delegations.append({
                "tool": name,
                "description": args.get("description", ""),
            })
        elif name:
            other.append(name)
    return {
        "spoken": spoken,
        "diagrams": diagrams,
        "annotations": annotations,
        "mounted_templates": mounted_templates,
        "delegations": delegations,
        "other_tool_names": other,
        "tool_call_count": len(tool_calls),
    }


# ---- YAML + filesystem ----

def load_yaml(path: pathlib.Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def list_runnable(package_root: pathlib.Path, *, require_key: str) -> list[str]:
    """Return slugs of subdirectories under `package_root` whose
    `questions.yaml` has a non-empty top-level `require_key`. Used to
    distinguish runnable regions/topics from empty stub slots."""
    out: list[str] = []
    for child in sorted(package_root.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        qpath = child / "questions.yaml"
        if not qpath.exists():
            continue
        try:
            content = yaml.safe_load(qpath.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(content, dict) and content.get(require_key):
            out.append(child.name)
    return out


# ---- File uploads ----
#
# A `file:` block can appear inside any benchmark YAML where a passage
# would otherwise be inline (e.g. a `model_behavior` session) or as the
# top-level subject of a `file_understanding` slug. Shape:
#
#   file:
#     kind: pdf            # or video, audio, image
#     text_source: |       # PDF only — runner materializes a PDF at runtime
#       ...                # from this text. No binary fixtures in git.
#     # OR
#     path: "..."          # local file path, slug-relative or repo-relative
#     filename: "..."      # optional override for the upload filename
#
# `upload_file_decl()` returns a normalized dict the caller plugs into
# `/api/ask/stream` payloads.

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _resolve_file_path(slug_dir: pathlib.Path, raw_path: str) -> pathlib.Path:
    """Try absolute, slug-relative, repo-root-relative. First hit wins."""
    candidates = [
        pathlib.Path(raw_path),
        slug_dir / raw_path,
        REPO_ROOT / raw_path,
    ]
    for c in candidates:
        if c.is_absolute() and c.exists():
            return c
        if c.exists():
            return c.resolve()
    raise FileNotFoundError(
        f"File path {raw_path!r} not found. Tried: {[str(c) for c in candidates]}"
    )


def _multipart_headers(headers: dict) -> dict:
    """Drop Content-Type — httpx sets the multipart boundary itself."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


async def upload_file_decl(
    client: httpx.AsyncClient,
    headers: dict,
    file_decl: dict,
    *,
    slug_dir: pathlib.Path,
    default_filename: str,
) -> dict:
    """Upload a YAML `file:` declaration. Returns a normalized dict:

      {
        "kind": "pdf" | "video" | "audio" | "image",
        "document_id": str | None,        # set only for kind=pdf
        "passage_text": str,              # extracted text (pdf) or path hint (media)
        "media_path": str | None,         # server-side path for media; None for pdf
        "file_ref": {...},                # safe-to-serialize summary for results.json
      }
    """
    kind = (file_decl.get("kind") or "").lower()
    if kind not in {"pdf", "video", "audio", "image"}:
        raise SystemExit(
            f"file.kind must be one of pdf/video/audio/image (got {kind!r})."
        )

    if kind == "pdf":
        text_source = file_decl.get("text_source")
        path_decl = file_decl.get("path")
        filename = file_decl.get("filename") or default_filename
        if text_source:
            pdf_bytes = text_to_pdf_bytes(text_source)
            files = {"file": (filename, pdf_bytes, "application/pdf")}
        elif path_decl:
            fp = _resolve_file_path(slug_dir, path_decl)
            files = {"file": (fp.name, fp.read_bytes(), "application/pdf")}
            filename = fp.name
        else:
            raise SystemExit(
                "file.kind=pdf requires either `text_source:` or `path:`."
            )
        resp = await client.post(
            "/api/documents/upload",
            headers=_multipart_headers(headers),
            files=files,
        )
        resp.raise_for_status()
        upload = resp.json()
        return {
            "kind": "pdf",
            "document_id": upload["id"],
            "passage_text": upload["text"],
            "media_path": None,
            "file_ref": {
                "kind": "pdf",
                "document_id": upload["id"],
                "filename": upload["filename"],
                "pages": upload["pages"],
                "extracted_chars": len(upload["text"]),
            },
        }

    # video / audio / image
    path_decl = file_decl.get("path")
    if not path_decl:
        raise SystemExit(f"file.kind={kind} requires a `path:` to a local file.")
    fp = _resolve_file_path(slug_dir, path_decl)
    files = {"file": (fp.name, fp.read_bytes(), "application/octet-stream")}
    resp = await client.post(
        "/api/media/upload",
        headers=_multipart_headers(headers),
        files=files,
    )
    resp.raise_for_status()
    upload = resp.json()
    tool = "look_at_video" if kind == "video" else "look_at_image"
    passage = (
        f"User attached a {kind} file at: {upload['path']}\n"
        f"(Use {tool} to inspect.)"
    )
    return {
        "kind": kind,
        "document_id": None,
        "passage_text": passage,
        "media_path": upload["path"],
        "file_ref": {
            "kind": kind,
            "path": upload["path"],
            "filename": upload["filename"],
            "size": upload["size"],
        },
    }


# ---- Metadata helpers ----

def git_sha() -> dict:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
        return {"git_sha": sha, "git_dirty": dirty}
    except Exception:
        return {"git_sha": None, "git_dirty": None}


def env_snapshot() -> dict:
    keys = (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "VISION_PROVIDER",
        "DOUBAO_VISION_MODEL",
    )
    return {k: os.environ.get(k) for k in keys}


def write_run_artifacts(
    run_dir: pathlib.Path,
    *,
    results: dict,
    metadata: dict,
    comments_header: str,
    answers_md: str | None = None,
) -> None:
    """Write the standard per-round files. When `answers_md` is provided,
    also write an `answers.md` transcript next to them — this is the
    human-readable view of a round (inline mermaid, spoken snippets,
    tool summaries) and is rendered by callers via `render_answers_md`."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    with open(run_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    with open(run_dir / "comments.md", "w", encoding="utf-8") as f:
        f.write(comments_header)
    if answers_md is not None:
        with open(run_dir / "answers.md", "w", encoding="utf-8") as f:
            f.write(answers_md)


def render_answers_md(
    *,
    title: str,
    profile: str,
    device_class: str | None,
    talk_preference: dict | None,
    answers: list[dict],
) -> str:
    """Render the round's question/answer/multi-modal transcript as
    markdown. Inlines mermaid diagrams in fenced ```mermaid blocks so
    GitHub and most previewers render them without extra tooling. Voice
    output from the `speak` tool is shown in a quoted block; canvas
    annotations / templates / delegations are listed as short bullets.

    Designed to be the artifact a human reviewer scans for "did the
    teacher use the right medium?" — results.json stays as the
    machine-grading source of truth."""
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    if profile:
        lines.append("**Profile**")
        lines.append("")
        for ln in profile.strip().splitlines():
            lines.append(f"> {ln}")
        lines.append("")
    meta_bits = []
    if device_class:
        meta_bits.append(f"device_class={device_class}")
    if talk_preference:
        tp_str = ", ".join(f"{k}={v}" for k, v in talk_preference.items())
        meta_bits.append(f"talk_preference=({tp_str})")
    if meta_bits:
        lines.append(f"_{' · '.join(meta_bits)}_")
        lines.append("")

    current_session: str | None = object()  # sentinel that won't compare equal
    for idx, a in enumerate(answers, start=1):
        session_title = a.get("session_title") or ""
        if session_title != current_session:
            current_session = session_title
            if session_title:
                lines.append(f"## Session: {session_title}")
                lines.append("")

        q = a.get("question") or ""
        sel = a.get("selected_text")
        lines.append(f"### Q{idx}. {q}")
        if sel:
            lines.append(f"**selected:** “{sel}”")
        lines.append("")

        if a.get("error"):
            lines.append(f"> **ERROR**: {a['error']}")
            lines.append("")
            continue

        title_str = a.get("title")
        if title_str:
            lines.append(f"**Title:** {title_str}")
            lines.append("")

        answer_text = (a.get("answer_text") or "").strip()
        if answer_text:
            lines.append("**Answer**")
            lines.append("")
            for ln in answer_text.splitlines():
                lines.append(f"> {ln}" if ln else ">")
            lines.append("")

        mm = a.get("multimodal") or {}
        for spoken in mm.get("spoken", []):
            ch = spoken.get("channel", "both")
            lines.append(f"**Spoken (channel={ch})**")
            lines.append("")
            for ln in (spoken.get("text") or "").splitlines():
                lines.append(f"> {ln}" if ln else ">")
            lines.append("")
        for diag in mm.get("diagrams", []):
            name = diag.get("name") or "main"
            kind = diag.get("kind") or "unknown"
            if diag.get("clear"):
                lines.append(f"**Diagram cleared — `{name}`** ({kind})")
                lines.append("")
                continue
            lines.append(f"**Diagram — `{name}`** ({kind})")
            lines.append("")
            lines.append("```mermaid")
            lines.append((diag.get("mermaid") or "").rstrip())
            lines.append("```")
            hn = diag.get("highlight_node")
            if hn:
                lines.append(f"_highlight: `{hn}`_")
            lines.append("")
        for ann in mm.get("annotations", []):
            lines.append(f"- **{ann.get('tool')}**: {ann.get('summary')}")
        if mm.get("annotations"):
            lines.append("")
        for tmpl in mm.get("mounted_templates", []):
            slug = tmpl.get("slug") or "(auto)"
            keys = ", ".join(tmpl.get("params_keys") or []) or "—"
            lines.append(
                f"- **mount_template** `{tmpl.get('template')}` "
                f"slug=`{slug}` params=[{keys}]"
            )
        if mm.get("mounted_templates"):
            lines.append("")
        for deleg in mm.get("delegations", []):
            lines.append(
                f"- **{deleg.get('tool')}**: {deleg.get('description')}"
            )
        if mm.get("delegations"):
            lines.append("")
        if mm.get("other_tool_names"):
            lines.append(
                "_other tools: " + ", ".join(mm["other_tool_names"]) + "_"
            )
            lines.append("")

        usage = a.get("usage") or {}
        tc_count = mm.get("tool_call_count", 0)
        bits = []
        if tc_count:
            bits.append(f"tool_calls={tc_count}")
        if usage.get("input_tokens") is not None:
            bits.append(f"in={usage.get('input_tokens')}")
        if usage.get("output_tokens") is not None:
            bits.append(f"out={usage.get('output_tokens')}")
        if a.get("elapsed_seconds"):
            bits.append(f"elapsed={a['elapsed_seconds']}s")
        if bits:
            lines.append("_" + " · ".join(bits) + "_")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def comments_template(round_id: str, label: str) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return (
        f"# Comments for round {round_id} — {label}\n\n"
        f"Add reviewer sections below. Free-form markdown.\n\n"
        f"<!-- Example:\n"
        f"## Reviewer: human (you) — {today}\n"
        f"- Q1: …\n"
        f"-->\n"
    )
