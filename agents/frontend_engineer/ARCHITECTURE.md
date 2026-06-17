# frontend_engineer — architecture

> A lightweight Claude-Code-shaped agent scoped to **one specific project**:
> the per-user block canvas. Its job is to take a teacher-or-user command and
> produce the *smallest possible* change to a per-user git workspace of
> browser-side blocks. Less code generated = fewer retries the user pays for.

This doc is the source of truth for how the agent is structured. The
project-wide `ARCHITECTURE.md` at the repo root governs where this agent
sits in the larger system; this file governs what happens *inside* it.

---

## 1. Mission in one sentence

Translate a natural-language command into the minimum FILES diff over a
per-user block workspace, while leaving template-faithful layout, fonts,
and behavior untouched.

## 2. The loop — read, collect, then write

Every turn follows this order. It is non-negotiable.

```
┌─ READ ────────────────────────────────────────────────────────────────┐
│  Workspace dump: README, TOPICS, every existing block (.js + .md),    │
│  CAUTIOUS.md. Provided automatically by `_workspace_dump()`.          │
└──────────────────────────────────┬────────────────────────────────────┘
                                   ↓
┌─ COLLECT ─────────────────────────────────────────────────────────────┐
│  Template reference: every template under                             │
│  `frontend/templates/blocks/*.{js,md}` is injected as reference.      │
│  Score templates against the user's keywords. Pick the best match.    │
│  If two templates compose, agree on a shared bus topic.               │
└──────────────────────────────────┬────────────────────────────────────┘
                                   ↓
┌─ WRITE ───────────────────────────────────────────────────────────────┐
│  Emit the smallest possible FILES block. Only files you create or     │
│  modify. Never re-emit unchanged content. Prefer copying a template   │
│  to handwriting code. Handwrite only if the read+collect steps        │
│  produced nothing usable.                                             │
└───────────────────────────────────────────────────────────────────────┘
```

**Why the order matters.** Every byte of LLM-generated JS is a chance to
break a template that already obeys the project's layout/fonts/design.
Reading first prevents duplicating an existing block; collecting first
prevents reinventing a template; writing last keeps the diff minimal.

## 3. The three operation skills

The agent is organized around **operations**, not artifact internals.

| Skill | Operation | First-class verb |
|---|---|---|
| `skills/new_block.md` | Add a block to the canvas | template-match → copy → adapt |
| `skills/repositioning.md` | Move an existing block; do not change its body | grid-only edit |
| `skills/remove_or_hide.md` | Drop a block, or hide without deleting | `### deleted` (today) |

A fourth file, `skills/principles.md`, is loaded first and carries the
read-first / collect-first / then-write directive that every operation
inherits.

## 4. Layered structure of the agent itself

```
agents/frontend_engineer/
├── ARCHITECTURE.md           ← this file
├── tools.md                  ← verb catalog (LLM input/output protocol)
├── llm_engineer.py           ← turn loop: route skills → prompt → LLM → parse → write → commit
├── build.py                  ← entry point used by tools/request_ui_block.py
├── workspace.py              ← per-user git workspace I/O (read/write/commit)
├── prompt.py                 ← (placeholder; system prompt is built in llm_engineer.py)
└── skills/
    ├── principles.md         ← always-loaded; read → collect → write
    ├── new_block.md          ← operation: add (loads templates)
    ├── repositioning.md      ← operation: move
    └── remove_or_hide.md     ← operation: drop or hide
```

Templates live outside the agent at `frontend/templates/blocks/*.{js,md}`
and are loaded into the prompt only when a routed skill declares
`needs_templates: true` (today: just `new_block`). The agent never
writes templates — only blocks under the user's workspace.

### 4.1 Skill registry & lazy loading

Skills are discovered from `skills/*.md` at load time. Each file declares
YAML frontmatter:

```yaml
---
name: new_block
keywords: add, create, build, ...
needs_templates: true   # optional; default false
always: false            # optional; default false. true = loaded every turn
when: One-line description shown in the always-visible skill index.
---
```

Per turn, the framework:

1. Builds a **constant system prompt** containing `_BASE_PROMPT`, the
   skill index (one line per skill, name + when), and the bodies of
   skills marked `always: true`. This prefix is cacheable across all
   turns and all users.
2. Builds a **routed passage** containing only the skills whose
   keywords overlap the current command, plus the template reference
   if any of them needs it. The passage rides in the user-passage slot
   (alongside the workspace dump).
3. If no operation skill matches the command, all of them are loaded as
   a safe fallback so the LLM can still classify and act.

The result: a "delete" turn carries ~3k chars of routed content;
a "create" turn carries ~30k (templates included). Old design loaded
~30k unconditionally.

The skill index is **always visible** to the LLM even when a body is
not loaded, so the LLM can see what exists and surface a routing
miss in its plan lines. (Re-prompting on miss is a planned addition.)

## 5. Tool set (the verb catalog)

The agent's "tools" are the verbs the LLM is allowed to emit through the
text protocol. See `tools.md` for the full catalog. Summary:

| Verb | Today's protocol | Status |
|---|---|---|
| Read workspace | auto-injected | ✅ |
| Read template reference | auto-injected | ✅ |
| Add / replace block | `### blocks/<id>.{js,md}` + fenced body | ✅ |
| Remove block | `### deleted` + id | ✅ |
| Reposition block | full re-emit of `<id>.js` | ⚠️ partial-edit verb planned |
| Hide block | (no first-class verb) | ❌ planned: `### hidden` toggle |
| Append caution | `<<<CAUTION>>>` block | ✅ |

## 6. Invariants

1. The agent never edits files outside the user's workspace allowlist
   (`README.md`, `blocks/<id>.js`, `blocks/<id>.md`).
2. The agent never edits `frontend/templates/blocks/*` — templates are
   reference-only, copied chunk-by-chunk into new blocks.
3. The agent never re-emits an unchanged file. If nothing about block X
   needs to change, X is not in the FILES block.
4. Block ids are kebab-case (`/^[a-z0-9][a-z0-9-]*$/`).
5. Every write goes through `workspace.write_files`, which path-checks
   each entry; unsafe paths are rejected, not silently skipped.
6. Every turn ends in a git commit (or no-op if the FILES block was
   empty), so the user's history is the audit trail.

## 7. Trajectory — making the operations cheaper

Today, "reposition" and "hide" both require re-emitting the whole
`<id>.js`. That contradicts the mission (minimum LLM-generated code).
Two protocol additions move the agent toward the operation-shaped goal:

1. **Partial-grid edit.** Add a `### grid blocks/<id>` section that
   accepts only `{ x, y, w, h }`. The parser merges into the existing
   block source on disk. Repositioning becomes a 4-number patch.
2. **Hide toggle.** Add a `### hidden` / `### shown` section listing
   block ids. The runtime skips render for hidden ids. Hide stops being
   a delete-or-rewrite choice.

Both land independently of the skills restructure; the skill files
already describe the target shape and note today's fallback.

## 8. What the agent does NOT do

- Decide *what* the user wants — that's the calling persona's job. The
  agent receives a command and acts.
- Touch the static frontend (`frontend/app/`, `frontend/components/`).
  Mutations land only inside the user's `blocks/` workspace.
- Run code. The browser is the sandbox; the agent commits source and
  trusts the runtime to mount it.
- Manage cross-user state. Each turn is scoped to one `user_id`.

---

## 9. Owner reference — protocol & collisions (architecture-review Step 4)

> Recorded by the architecture-review **owner simulation** so the engineer owner can work without
> changing the shared protocol. See [`../../architecture-review/PROCESS.md`](../../architecture-review/PROCESS.md) Step 4.

- **Protocol I consume:** `infra.contracts.ui.BlockSource` (my output shape); `infra.sandbox`
  (validation); the per-user git workspace on disk; the Anthropic agents API.
- **Protocol I provide:** `BlockSource` artifacts + `list_blocks(user_id)`, and the persisted
  workspace that the **canvas** tools (`mount_template`, `request_ui_block`) read and deliver.
- **Can I work alone?** Yes — block-generation logic, skills, and the workspace format are mine.
- **Collisions:** `infra/contracts/ui.py:BlockSource`; the `request_ui_block` seam in
  `workshop/canvas/tools` (canvas owner).
- **Boundary rules:** reached only through the `request_ui_block` tool boundary (no persona imports
  my code; I import no persona internals); generated source passes `infra.sandbox` before delivery
  (Principle 9 — LLM-authored UI is gated).
