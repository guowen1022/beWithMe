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

def auth_headers(user_id: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "X-User-Id": user_id}


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
) -> dict:
    """Single POST to /api/ask/stream. Parses the SSE stream and returns
    a result dict including the full `answer_text`, the assembled prompt
    parts (from the route's existing `debug` SSE event), and timing.

    Pass `document_id` when the question references an uploaded PDF — the
    route stores it on the Interaction row so the doc/Q&A link survives.

    On any HTTP / transport error returns the same dict shape with an
    extra `error` key set."""
    payload: dict = {
        "passage_text": passage,
        "selected_text": selected_text,
        "question": question,
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
        "error": err,
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
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    with open(run_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    with open(run_dir / "comments.md", "w", encoding="utf-8") as f:
        f.write(comments_header)


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
