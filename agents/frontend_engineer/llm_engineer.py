"""LLM engineer — reads the user's workspace, writes blocks, commits.

Mirrors the Block Canvas PoC's `/api/command` endpoint:
  1. Build a system prompt (base + skills + cautions).
  2. Build a user prompt that dumps the user's full workspace state
     (README, TOPICS, every block's .md and .js).
  3. Append the user's command.
  4. Call the LLM. Expect a response shaped like:

         > short plan line 1
         > short plan line 2
         <<<FILES>>>
         ### blocks/<id>.js
         ```js
         ({ ... })
         ```
         ### blocks/<id>.md
         ```md
         ...
         ```
         ### deleted
         - old-id
         <<<END>>>
         <<<CAUTION>>>
         - <one rule learned>
         <<<END>>>

  5. Parse the FILES block. Path-safety-check each entry. Write files.
     Delete listed blocks. Regenerate TOPICS.md. Commit.
  6. Append any CAUTION line to the user's CAUTIOUS.md.
  7. Return an EngineerResult with the changed/deleted block ids so the
     tool layer can fan them out as UIUpdate events.

For v1 we use the non-streaming `generate_cached` and emit the synthetic
"thinking → answer" SSE flow at the ask layer. Token-level streaming of
the engineer's plan lines is a follow-up.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional
from uuid import UUID

from infra.contracts.ui import BlockSource
from infra.model.llm import generate_cached, stream_cached

from agents.frontend_engineer import workspace as ws


_AGENT_DIR = Path(__file__).parent
_SKILLS_DIR = _AGENT_DIR / "skills"
_REPO_ROOT = _AGENT_DIR.resolve().parents[1]
_TEMPLATES_DIR = _REPO_ROOT / "frontend" / "templates" / "blocks"

# Skills are discovered from `skills/*.md` and routed per-turn (lazy loading).
# Frontmatter declares: name, when, keywords, always (bool), needs_templates (bool).
# See ARCHITECTURE.md and tools.md for the full protocol.

_BASE_PROMPT = """\
You are the **frontend_engineer** — an LLM agent that writes browser-side
"blocks" for a user-facing canvas. You receive the user's full workspace
state (README, TOPICS, existing blocks) plus a command from the teacher
persona, and your job is to update that workspace to match what was asked.

**Before emitting any code, do these in order. The order is non-negotiable:**

  1. **READ** — the workspace dump below has the user's README, TOPICS,
     CAUTIOUS, and every existing block. Read it. If a block already on
     the canvas covers the request, say so in your plan and stop.
  2. **COLLECT** — the template reference below has every template under
     `frontend/templates/blocks/*`. Match the user's command against each
     template's `keywords:` line. The template already obeys the project's
     layout, fonts, color, and behavior contracts; copying a template
     chunk is always cheaper and safer than handwriting code.
  3. **WRITE** — emit the smallest possible FILES diff. Only files you
     create or modify. Never re-emit unchanged content. Handwrite fresh
     JS only when steps 1 and 2 yield nothing usable.

**REFUSE diagram-shaped requests.** If the teacher's command asks you to
author a block that displays a flow, sequence, hierarchy, mind map, class
diagram, ER diagram, gantt, sankey, timeline, chart (bar/line/pie/scatter),
comparison, or any node-and-edge structure: do NOT write JavaScript for
this. The teacher has a separate tool, `interactive_graph`, that renders
all of these from a Mermaid string — fast, deterministic, ephemeral. Per-
node JS does not belong in the user's workspace; it accumulates and
pollutes the layout the user has actually customized.

In this case emit ONLY:

  > diagram-shaped request — teacher should call interactive_graph(
  >   name='...', mermaid='flowchart LR ...') instead of request_new_block.

…and NO FILES block, NO CAUTION block. The teacher will see your plan
line in the tool result and re-route correctly.

Every byte of LLM-generated JS is a chance to break a template-faithful
block; every retry is a cost the user pays. Less code = fewer retries.

**REPORTING IS NON-NEGOTIABLE.** Every block you author MUST call
`helpers.reportState({...})` at all of these moments:

  1. ON MOUNT — fire one report inside `run()` before returning, so the
     teacher knows the surface exists even before any user interaction.
     Pick a `kind:` that describes what the block IS (e.g. `'pdf'`,
     `'passage'`, `'upload'`, `'graph'`, `'launcher'`) and a `content:`
     that summarizes the empty/idle state.
  2. ON EVERY MEANINGFUL STATE CHANGE — text input, selection, file
     chosen, page scrolled, sub-doc loaded, error rendered. Each report
     should reflect the *current* user-visible state, not a delta.
  3. ON COMPLETION EDGES — when the user finishes a discrete unit of
     work the teacher should react to (upload finished, doc fully
     rendered, form submitted), include `completed: true` in the
     report. The perception cache fires `BlockCompletedEvent` on the
     `false → true` transition, which wakes the teacher's tool loop.
     Include `completed: false` (or omit) for ordinary state updates so
     the teacher isn't woken on every keystroke.

If a block displays content to the user that the teacher cannot see via
read_media after the change lands, that block is broken — fix the
reporting, not the prompt. Templates that opt out of `autosnapshot:
false` MUST emit structured reports themselves; the framework's
auto-snapshot fallback is disabled for them.

You MUST emit your response in this exact shape, with NOTHING else
between sections:

  1. 1–4 short plan lines, each starting with "> " and under 80 chars,
     describing what you're about to do.

  2. A FILES block:
       <<<FILES>>>
       ### <path>
       ```<lang>
       <content>
       ```
       ### <path>
       ```<lang>
       <content>
       ```
       ### deleted
       - <block-id-1>
       - <block-id-2>
       <<<END>>>

     Allowed paths:
       - README.md
       - blocks/<kebab-id>.js
       - blocks/<kebab-id>.md
     The "### deleted" section is optional; list block ids (no extension)
     to remove. If nothing is deleted, omit the section entirely.
     Keep your changes minimal: only emit files you are creating or
     modifying. Do NOT re-emit unchanged files.

  3. (Optional) A CAUTION block:
       <<<CAUTION>>>
       - <one short rule learned from this turn, if any>
       <<<END>>>
     Only include this if the turn taught you something durable about
     this user's preferences or a mistake you want to avoid repeating.

Critical rules:
  - Never invent paths outside the allowlist above.
  - Never re-emit a block unchanged.
  - When you delete a block, also remove its .md (the runtime does that;
    just list the id once in "### deleted").
  - You may COPY chunks from `frontend/templates/blocks/*` (provided
    below as reference). Adapt id, grid, topics; don't paste verbatim.
"""


def _load_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


# ---------- skill registry + router (lazy loading) ----------


_SKILL_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    name: str
    file: Path
    when: str = ""
    keywords: list[str] = field(default_factory=list)
    always: bool = False
    needs_templates: bool = False


def _parse_skill_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (metadata, body-without-frontmatter). Empty dict if absent."""
    m = _SKILL_FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    body = text[m.end():].strip()
    return meta, body


def _load_skill_registry() -> list[Skill]:
    """Discover every `skills/*.md` and parse its frontmatter."""
    registry: list[Skill] = []
    if not _SKILLS_DIR.is_dir():
        return registry
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        text = _load_text(path)
        if not text.strip():
            continue
        meta, _body = _parse_skill_frontmatter(text)
        registry.append(Skill(
            name=meta.get("name", path.stem),
            file=path,
            when=meta.get("when", ""),
            keywords=[k.strip().lower() for k in meta.get("keywords", "").split(",") if k.strip()],
            always=meta.get("always", "").lower() == "true",
            needs_templates=meta.get("needs_templates", "").lower() == "true",
        ))
    return registry


def _route_skills(registry: list[Skill], command: str) -> list[Skill]:
    """Pick the skills that should be loaded for this command.

    Always-loaded skills are returned unconditionally. Operation skills are
    scored by keyword overlap with the command; the highest-scoring tier
    wins. If no operation skill matches, all are loaded as a safe fallback
    so the LLM can still classify and act.
    """
    cmd = command.lower()
    chosen = [s for s in registry if s.always]

    operation = [s for s in registry if not s.always]
    scored: list[tuple[int, Skill]] = []
    for s in operation:
        if not s.keywords:
            continue
        score = sum(1 for kw in s.keywords if kw and kw in cmd)
        if score > 0:
            scored.append((score, s))

    if scored:
        scored.sort(key=lambda t: (-t[0], t[1].name))
        top = scored[0][0]
        for score, s in scored:
            if score == top:
                chosen.append(s)
            else:
                break
    else:
        # Ambiguous command — fall back to loading every operation skill
        # so the LLM can pick the right one rather than guessing wrong.
        chosen.extend(operation)

    return chosen


def _build_skill_index(registry: list[Skill]) -> str:
    lines = ["# Available skills (lazy-loaded — see body below for the ones picked this turn)"]
    for s in registry:
        marker = "always" if s.always else "lazy"
        when = s.when or "(no description)"
        lines.append(f"- **{s.name}** [{marker}] — {when}")
    return "\n".join(lines)


def _load_template_reference() -> str:
    """Dump every template's .md + .js. Only emitted when a routed skill
    sets `needs_templates: true` (today: just `new_block`)."""
    if not _TEMPLATES_DIR.is_dir():
        return ""
    parts: list[str] = ["# Template reference (frontend/templates/blocks/)"]
    for js_path in sorted(_TEMPLATES_DIR.glob("*.js")):
        md_path = js_path.with_suffix(".md")
        name = js_path.stem
        parts.append(f"## {name}")
        md_text = _load_text(md_path).strip()
        if md_text:
            parts.append("```md")
            parts.append(md_text)
            parts.append("```")
        parts.append("```js")
        parts.append(_load_text(js_path).rstrip())
        parts.append("```")
        parts.append("")
    return "\n".join(parts)


def _system_prompt() -> str:
    """Constant across turns — base prompt + skill index + always-loaded skill bodies.

    Cacheable as a single static prefix regardless of the user's command.
    """
    registry = _load_skill_registry()
    index = _build_skill_index(registry)
    always_bodies: list[str] = []
    for s in registry:
        if not s.always:
            continue
        _meta, body = _parse_skill_frontmatter(_load_text(s.file))
        if body:
            always_bodies.append(body)
    parts = [_BASE_PROMPT.strip(), index]
    parts.extend(always_bodies)
    return "\n\n---\n\n".join(p for p in parts if p.strip())


def _routed_passage(command: str) -> str:
    """Per-turn routed skill bodies + (optional) template reference.

    Appended to the workspace dump in the user-passage slot. Empty string
    if the always-loaded skills already cover everything.
    """
    registry = _load_skill_registry()
    chosen = [s for s in _route_skills(registry, command) if not s.always]
    if not chosen:
        return ""

    bodies: list[str] = []
    for s in chosen:
        _meta, body = _parse_skill_frontmatter(_load_text(s.file))
        if body:
            bodies.append(body)
    skills_section = "\n\n---\n\n".join(bodies)

    needs_templates = any(s.needs_templates for s in chosen)
    template_ref = _load_template_reference() if needs_templates else ""

    parts: list[str] = []
    if skills_section:
        header = "# Skills loaded for this turn — " + ", ".join(s.name for s in chosen)
        parts.append(header + "\n\n" + skills_section)
    if template_ref:
        parts.append(template_ref)
    return "\n\n---\n\n".join(parts)


def _workspace_dump(snap: ws.WorkspaceSnapshot) -> str:
    parts = [f'# Project state for user "{snap.user_id}"', ""]
    parts.append("### README.md")
    parts.append("```md")
    parts.append(snap.readme.strip() or "(empty)")
    parts.append("```")
    parts.append("")
    parts.append("### TOPICS.md (auto-generated, read-only)")
    parts.append("```md")
    parts.append(snap.topics_md.strip() or "(empty)")
    parts.append("```")
    parts.append("")
    if snap.cautious.strip() and snap.cautious.strip() != "# Cautions\n\n(none yet)":
        parts.append("### CAUTIOUS.md (read every time, do not repeat these)")
        parts.append("```md")
        parts.append(snap.cautious.strip())
        parts.append("```")
        parts.append("")
    if snap.blocks:
        for bid, bf in snap.blocks.items():
            parts.append(f"### blocks/{bid}.md")
            parts.append("```md")
            parts.append(bf.md.strip() or "(empty)")
            parts.append("```")
            parts.append(f"### blocks/{bid}.js")
            parts.append("```js")
            parts.append(bf.js.rstrip())
            parts.append("```")
            parts.append("")
    else:
        parts.append("(no blocks yet — the canvas is empty)")
    return "\n".join(parts)


# ---------- output parsing ----------


_FILES_OPEN = "<<<FILES>>>"
_BLOCK_END = "<<<END>>>"
_CAUTION_OPEN = "<<<CAUTION>>>"

_HEADER_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)\n```")
_DELETED_LIST_RE = re.compile(r"^[\s-]*([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)


@dataclass
class ParsedOutput:
    plan_lines: list[str] = field(default_factory=list)
    file_writes: list[ws.FileWrite] = field(default_factory=list)
    deleted_ids: list[str] = field(default_factory=list)
    caution: str = ""


def _extract_block(text: str, open_tag: str) -> str | None:
    start = text.find(open_tag)
    if start < 0:
        return None
    after = start + len(open_tag)
    end = text.find(_BLOCK_END, after)
    if end < 0:
        end = len(text)
    return text[after:end]


def _parse_plan(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("<<<"):
            break
        if s.startswith(">"):
            out.append(s.lstrip(">").strip())
    return out


def _parse_files_block(body: str) -> tuple[list[ws.FileWrite], list[str]]:
    """Walk ### header / fenced-content pairs."""
    writes: list[ws.FileWrite] = []
    deleted: list[str] = []

    headers = list(_HEADER_RE.finditer(body))
    for i, h in enumerate(headers):
        path = h.group(1).strip()
        section_start = h.end()
        section_end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        section = body[section_start:section_end]
        if path.lower() == "deleted":
            for m in _DELETED_LIST_RE.finditer(section):
                bid = m.group(1)
                if bid:
                    deleted.append(bid)
            continue
        m = _FENCE_RE.search(section)
        if not m:
            continue
        content = m.group(1)
        if not ws.is_safe_path(path):
            print(f"[engineer.parse] rejected unsafe path: {path!r}", flush=True)
            continue
        writes.append(ws.FileWrite(path=path, content=content))
    return writes, deleted


def _parse_caution(text: str) -> str:
    body = _extract_block(text, _CAUTION_OPEN) or ""
    body = body.strip()
    if not body:
        return ""
    # Strip leading "- " bullets
    lines = [ln.strip().lstrip("-").strip() for ln in body.splitlines() if ln.strip()]
    return " ".join(lines)


def parse_output(text: str) -> ParsedOutput:
    out = ParsedOutput()
    out.plan_lines = _parse_plan(text)
    files_body = _extract_block(text, _FILES_OPEN)
    if files_body is not None:
        out.file_writes, out.deleted_ids = _parse_files_block(files_body)
    out.caution = _parse_caution(text)
    return out


# ---------- public API ----------


@dataclass
class EngineerResult:
    changed: list[BlockSource]      # newly added or modified blocks
    deleted: list[str]              # block ids removed
    plan_lines: list[str]           # short narration the LLM emitted
    sha: str | None                 # commit hash, or None if nothing changed
    caution: str = ""               # any rule the LLM appended


async def respond(
    user_id: UUID | str,
    command: str,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
) -> EngineerResult:
    """Run one engineer turn: read snapshot → LLM → parse → write → commit.

    If `on_delta` is provided, the engineer streams the LLM output and calls
    it with each chunk as it arrives. This lets callers narrate the
    engineer's plan/file output to the user while it's still being produced.
    """
    ws.ensure_workspace(user_id)
    before = ws.read_snapshot(user_id)

    static_system = _system_prompt()
    static_user_passage = _workspace_dump(before)
    routed = _routed_passage(command)
    if routed:
        static_user_passage = static_user_passage + "\n\n---\n\n" + routed
    dynamic_user = f"User command:\n\n{command.strip()}\n\nRespond now."

    user_uuid = UUID(user_id) if isinstance(user_id, str) else user_id

    if on_delta is None:
        text, _usage = await generate_cached(
            static_system,
            static_user_passage,
            dynamic_user,
            prior_messages=None,
            purpose="delegate-engineer",
            user_id=user_uuid,
        )
    else:
        chunks: list[str] = []
        async for evt in stream_cached(
            static_system,
            static_user_passage,
            dynamic_user,
            prior_messages=None,
            purpose="delegate-engineer",
            user_id=user_uuid,
        ):
            if evt["kind"] == "delta":
                chunks.append(evt["text"])
                await on_delta(evt["text"])
            elif evt["kind"] == "done":
                # done.text is the authoritative full text. Prefer it over
                # the concatenated chunks in case the provider re-emits a
                # final canonical form.
                text = evt.get("text") or "".join(chunks)
                break
        else:
            text = "".join(chunks)
    parsed = parse_output(text)

    written_paths = ws.write_files(user_id, parsed.file_writes)
    deleted_ids = ws.delete_blocks(user_id, parsed.deleted_ids)
    if written_paths or deleted_ids:
        ws.regenerate_topics(user_id)

    if parsed.caution:
        ws.append_caution(user_id, parsed.caution)

    sha = ws.commit(user_id, command)

    after = ws.read_snapshot(user_id)
    changed: list[BlockSource] = []
    for bid, bf in after.blocks.items():
        prev = before.blocks.get(bid)
        if prev is None or prev.js != bf.js:
            changed.append(BlockSource(
                id=bid,
                source=bf.js,
                design_doc=bf.md or None,
            ))

    return EngineerResult(
        changed=changed,
        deleted=deleted_ids,
        plan_lines=parsed.plan_lines,
        sha=sha,
        caution=parsed.caution,
    )


def list_blocks(user_id: UUID | str) -> list[BlockSource]:
    """Return every block currently in the user's workspace as BlockSources.
    Used by the canvas hydration endpoint to repopulate after a reload."""
    snap = ws.read_snapshot(user_id)
    return [
        BlockSource(id=bid, source=bf.js, design_doc=bf.md or None)
        for bid, bf in snap.blocks.items()
    ]
