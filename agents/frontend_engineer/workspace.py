"""Per-user canvas workspace + git management.

Each user has a workspace at `data/canvases/<user_id>/` that mirrors the
Block Canvas PoC's `.users/<user>/` layout:

    data/canvases/<user_id>/
    ├── .git/
    ├── README.md         -- engineer-written; what the user is building
    ├── TOPICS.md         -- auto-generated from blocks; bus topic catalog
    ├── CAUTIOUS.md       -- append-only; engineer-learned mistakes/preferences
    └── blocks/
        ├── <id>.js       -- parens-wrapped block source
        └── <id>.md       -- block design doc

Workflow per ask:
  1. ensure_workspace(user_id)
  2. snapshot = read_snapshot(user_id)
  3. build LLM prompt (caller does this) → stream → parse FILES block
  4. write_files(user_id, [...]) writes everything to disk
  5. regenerate_topics(user_id) rewrites TOPICS.md from blocks
  6. commit(user_id, message) makes a git commit
  7. read_snapshot(user_id) again to compute the delta sent to the client

Storage location: `data/canvases/` is gitignored at the project level. The
workspaces are per-installation user state, not source code.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from uuid import UUID


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANVASES_ROOT = _REPO_ROOT / "data" / "canvases"


_KEBAB = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SUBSCRIBES = re.compile(r"subscribes\s*:\s*\[([^\]]*)\]")
_PUBLISHES = re.compile(r"publishes\s*:\s*\[([^\]]*)\]")
_TOPIC_LITERAL = re.compile(r"['\"]([^'\"]+)['\"]")


@dataclass(frozen=True)
class BlockFile:
    id: str
    js: str
    md: str = ""


@dataclass
class WorkspaceSnapshot:
    user_id: str
    readme: str = ""
    topics_md: str = ""
    cautious: str = ""
    blocks: dict[str, BlockFile] = field(default_factory=dict)


@dataclass(frozen=True)
class FileWrite:
    """A file the engineer wants to write. `path` is relative to the workspace."""
    path: str
    content: str


def _user_dir(user_id: UUID | str) -> Path:
    return _CANVASES_ROOT / str(user_id)


def is_safe_path(rel_path: str) -> bool:
    """Allowlist the only paths the engineer is permitted to write."""
    if not rel_path or rel_path != rel_path.strip() or "\\" in rel_path:
        return False
    if ".." in rel_path.split("/") or rel_path.startswith("/"):
        return False
    if rel_path in {"README.md", "TOPICS.md", "CAUTIOUS.md"}:
        return True
    if rel_path.startswith("blocks/"):
        rest = rel_path[len("blocks/") :]
        if "/" in rest:
            return False
        stem, dot, ext = rest.partition(".")
        if dot != "." or ext not in {"js", "md"}:
            return False
        return bool(_KEBAB.match(stem))
    return False


def _run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )


def ensure_workspace(user_id: UUID | str) -> Path:
    """Create the user's workspace + git repo if missing. Idempotent."""
    workspace = _user_dir(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "blocks").mkdir(exist_ok=True)
    if not (workspace / "README.md").exists():
        (workspace / "README.md").write_text(
            f"# Canvas for user {user_id}\n\n(empty — ask the engineer to add a block)\n",
            encoding="utf-8",
        )
    if not (workspace / "TOPICS.md").exists():
        (workspace / "TOPICS.md").write_text(
            "# Bus topics\n\n(none yet)\n", encoding="utf-8"
        )
    if not (workspace / "CAUTIOUS.md").exists():
        (workspace / "CAUTIOUS.md").write_text(
            "# Cautions\n\n(none yet)\n", encoding="utf-8"
        )
    if not (workspace / ".git").exists():
        _run_git(workspace, "init", "-b", "main")
        # Local-only commits — don't require a global git identity.
        _run_git(workspace, "config", "user.email", "engineer@bewithme.local")
        _run_git(workspace, "config", "user.name", "frontend_engineer")
        _run_git(workspace, "add", "-A")
        _run_git(workspace, "commit", "-m", "seed", "--allow-empty")
    return workspace


def read_snapshot(user_id: UUID | str) -> WorkspaceSnapshot:
    """Read all engineer-relevant files for the user. Cheap; called per ask."""
    workspace = ensure_workspace(user_id)
    snap = WorkspaceSnapshot(user_id=str(user_id))
    snap.readme = (workspace / "README.md").read_text(encoding="utf-8") if (workspace / "README.md").exists() else ""
    snap.topics_md = (workspace / "TOPICS.md").read_text(encoding="utf-8") if (workspace / "TOPICS.md").exists() else ""
    snap.cautious = (workspace / "CAUTIOUS.md").read_text(encoding="utf-8") if (workspace / "CAUTIOUS.md").exists() else ""
    blocks_dir = workspace / "blocks"
    if blocks_dir.exists():
        for js_path in sorted(blocks_dir.glob("*.js")):
            block_id = js_path.stem
            md_path = js_path.with_suffix(".md")
            snap.blocks[block_id] = BlockFile(
                id=block_id,
                js=js_path.read_text(encoding="utf-8"),
                md=md_path.read_text(encoding="utf-8") if md_path.exists() else "",
            )
    return snap


def write_files(user_id: UUID | str, writes: Iterable[FileWrite]) -> list[str]:
    """Write files. Skips any whose path fails the safety check.

    Returns the list of paths actually written.
    """
    workspace = ensure_workspace(user_id)
    written: list[str] = []
    for w in writes:
        if not is_safe_path(w.path):
            print(f"[workspace] rejected unsafe path: {w.path!r}", flush=True)
            continue
        target = workspace / w.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(w.content, encoding="utf-8")
        written.append(w.path)
    return written


def delete_blocks(user_id: UUID | str, block_ids: Iterable[str]) -> list[str]:
    """Remove blocks/<id>.js and blocks/<id>.md for each id. Returns deleted ids."""
    workspace = ensure_workspace(user_id)
    deleted: list[str] = []
    for bid in block_ids:
        if not _KEBAB.match(bid):
            continue
        js = workspace / "blocks" / f"{bid}.js"
        md = workspace / "blocks" / f"{bid}.md"
        existed = False
        if js.exists():
            js.unlink()
            existed = True
        if md.exists():
            md.unlink()
            existed = True
        if existed:
            deleted.append(bid)
    return deleted


def regenerate_topics(user_id: UUID | str) -> None:
    """Walk every block .js and rewrite TOPICS.md from the subscribes/publishes
    arrays. Best-effort regex extraction — not a full JS parser. The engineer
    is told these are the canonical topic names so blocks can wire up."""
    workspace = ensure_workspace(user_id)
    pubs: dict[str, set[str]] = {}
    subs: dict[str, set[str]] = {}
    blocks_dir = workspace / "blocks"
    if blocks_dir.exists():
        for js_path in sorted(blocks_dir.glob("*.js")):
            content = js_path.read_text(encoding="utf-8")
            block_id = js_path.stem
            for src, store in ((_PUBLISHES, pubs), (_SUBSCRIBES, subs)):
                m = src.search(content)
                if not m:
                    continue
                for tm in _TOPIC_LITERAL.finditer(m.group(1)):
                    store.setdefault(tm.group(1), set()).add(block_id)

    lines = ["# Bus topics", ""]
    if not pubs and not subs:
        lines.append("(none yet)")
    else:
        all_topics = sorted(set(pubs) | set(subs))
        for topic in all_topics:
            ps = sorted(pubs.get(topic, set()))
            ss = sorted(subs.get(topic, set()))
            lines.append(f"## `{topic}`")
            if ps:
                lines.append(f"- published by: {', '.join(ps)}")
            if ss:
                lines.append(f"- subscribed by: {', '.join(ss)}")
            lines.append("")
    (workspace / "TOPICS.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def append_caution(user_id: UUID | str, body: str) -> None:
    """Append a caution to CAUTIOUS.md. The engineer learns from past failures."""
    if not body or not body.strip():
        return
    workspace = ensure_workspace(user_id)
    path = workspace / "CAUTIOUS.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Cautions\n\n"
    if existing.rstrip().endswith("(none yet)"):
        existing = "# Cautions\n\n"
    line = body.strip().splitlines()[0][:200]
    path.write_text(existing.rstrip() + f"\n- {line}\n", encoding="utf-8")


def commit(user_id: UUID | str, message: str) -> str | None:
    """Stage everything and commit. Returns the new HEAD sha, or None if clean."""
    workspace = ensure_workspace(user_id)
    msg = (message or "").strip()[:72] or "engineer turn"
    _run_git(workspace, "add", "-A")
    res = _run_git(workspace, "commit", "-m", msg)
    if res.returncode != 0:
        # nothing to commit
        return None
    head = _run_git(workspace, "rev-parse", "HEAD")
    sha = head.stdout.strip()
    return sha or None


def revert_one_step(user_id: UUID | str) -> str | None:
    """Mirror the PoC's undo: checkout files from the parent of HEAD, commit
    a 'revert: <prior subject>' on top. Returns the new HEAD sha or None."""
    workspace = ensure_workspace(user_id)
    log = _run_git(workspace, "log", "--format=%H%x09%s", "-n", "2")
    rows = [line for line in log.stdout.strip().splitlines() if line]
    if len(rows) < 2:
        return None
    prev_sha, prev_subject = rows[1].split("\t", 1)
    _run_git(workspace, "checkout", prev_sha, "--", ".")
    _run_git(workspace, "add", "-A")
    res = _run_git(workspace, "commit", "-m", f"revert: {prev_subject[:60]}")
    if res.returncode != 0:
        return None
    head = _run_git(workspace, "rev-parse", "HEAD")
    return head.stdout.strip() or None
