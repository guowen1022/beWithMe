# frontend_engineer — tool set

The agent's "tools" are the verbs the LLM may emit through the FILES /
deleted / CAUTION text protocol, plus the inputs the framework injects
into every prompt. This catalog is normative: anything not listed here
is not a verb the agent can use.

## Inputs (provided automatically — the LLM does not request them)

| Input | Source | Shape | Loading |
|---|---|---|---|
| Workspace dump | `_workspace_dump(snap)` | `README.md`, `TOPICS.md`, `CAUTIOUS.md`, every `blocks/<id>.{md,js}` | every turn |
| Skill index | `_build_skill_index(registry)` | one line per skill (name + when) | every turn (in system prompt) |
| Always-loaded skill bodies | `skills/*.md` with `always: true` | parsed body (frontmatter stripped) | every turn (in system prompt) |
| Routed skill bodies | `skills/*.md` whose `keywords` overlap the command | parsed bodies, separated by `---` | per-turn (lazy) |
| Template reference | `_load_template_reference()` | `frontend/templates/blocks/*.{md,js}` dumped verbatim | per-turn, only if a routed skill sets `needs_templates: true` |

### Skill frontmatter

Every `skills/*.md` opens with YAML frontmatter:

```yaml
---
name: <skill name>           # used in plan lines and the skill index
keywords: a, b, c            # comma-separated; substring-matched against the command, lowercased
when: <one-line description> # shown in the always-visible skill index
always: true | false         # optional; default false. true = loaded every turn
needs_templates: true | false # optional; default false. true = template reference rides along
---
```

### Routing rules

- Always-loaded skills (e.g. `principles`) are included in the system prompt unconditionally.
- For non-always skills, the router scores each by keyword overlap with the command. The highest-scoring tier wins; ties all load.
- If no skill scores, every operation skill loads as a safe fallback.
- The skill index is always visible — the LLM can see what exists even when a body wasn't loaded.

## Outputs (the verbs the LLM emits)

### `### README.md` — rewrite the user's README
Write the full new README body in a fenced block. Use to record what was
built or to keep a running design log the user reads.

### `### blocks/<id>.js` — add or replace a block
Body is the parens-wrapped block expression. Schema in
`skills/new_block.md`. **Replacing** an existing block means the LLM
fully re-emits the source; partial edits are not yet supported (see
`### grid` below).

### `### blocks/<id>.md` — add or replace a block's design doc
Markdown sibling to the `.js`. Should describe purpose, topics
published/subscribed, layout intent. Not loaded by the runtime — used
by future turns to understand what each block does.

### `### deleted` — remove blocks
List block ids (no extension), one per line, prefixed with `-`. The
runtime drops the `.js` and `.md` for each id and de-mounts the block.

### `<<<CAUTION>>>` — append a learned rule
One short line of guidance the agent learned this turn. Stored to
`CAUTIOUS.md` and surfaced in the next turn's workspace dump.

## Planned verbs (described in skills, not yet parsed)

These are the partial-edit verbs that bring "reposition" and "hide"
in line with the mission of minimum LLM-generated code.

### `### grid blocks/<id>` — reposition without rewriting body
Body is a JSON-ish object: `{ x, y, w, h }`. Parser merges into the
existing block source on disk. Repositioning becomes a 4-number patch
instead of a full file re-emission.

### `### hidden` / `### shown` — toggle render without deleting
List block ids. The runtime stops rendering hidden ids but keeps the
files. Reverts via `### shown` listing the same ids.

Until these land, repositioning means full re-emission of the block,
and hiding means either deleting or rewriting the block's `style`.
The agent should pick "delete and re-add later" over "rewrite to hide"
when the user is ambivalent — deletion is reversible from git history
and avoids touching the template-faithful body.

## Rules of engagement

- Verbs are not free. Every emitted file is parsed and written. Every
  rewrite of a template-derived block is a chance to corrupt layout/
  fonts/behavior. Prefer the smallest verb that satisfies the request.
- Order of operations on a turn: read, collect, write. Never write a
  new block without first checking whether a template or an existing
  block already covers the request.
- One verb per intent. Don't pair `### deleted` with a re-add of the
  same id under a slightly different shape — that's a reposition or a
  rename, not a delete-then-create.
- Path safety: only `README.md`, `blocks/<kebab-id>.js`, and
  `blocks/<kebab-id>.md` are accepted. Other paths are rejected with a
  log line and dropped silently from the FILES block.
