# Unified skill-loading framework — the selection menu as a skillforge tunable (proposal)

> **Status: PROPOSED — not executed.** Design for the "one skill framework" that unifies the
> three (four) overlapping skill-loading mechanisms and makes skill *selection* a first-class,
> skillforge-tunable subsystem. This is the concretization of the LOCKED decisions in
> `brainstorm/tool-refining/` — **D1** (Skill / Playbook / Tool glossary, `02-model-recursive-tree.md`)
> and **D21 / S1–S4** (`09-skill-selection.md`, "the menu + selection prompt are themselves
> tunables"). It fits into `ARCHITECTURE.md` as a new leaf-level subsystem (`infra/skills/`) and
> extends the existing skillforge adapter (`infra/skillforge_client.py`), not a new coupling.
>
> **Scope.** This proposal is the *framework design* + a *first slice*. The first slice tunes
> exactly one menu (the canvas-writer's visual-guide tree); the framework section defines the
> shape every other menu grows into. It is **not** the refine-loop build — skillforge's
> `refine_auto` + real eval backend are still unbuilt (their `TODO.md` items 3–4); this proposal
> lands the *enable + bounded-config injection* half (the proven Manim pattern) and the telemetry,
> leaving auto-refinement to follow on that foundation.
>
> **Relationship to prior work.** Mirrors the shape of the tool-authorization proposal
> (`2026-06-17-tool-authorization.md`): one declarative mechanism replacing implicit scatter, an
> `infra` leaf home, add-only protocol impact, a worked example, and a conformance check. Where
> that proposal governed *which tools a persona may select*, this governs *which skills a persona
> is offered and how that offer is tuned*.

> **⚠️ ENG-REVIEW OUTCOME (2026-07-09) — first slice REDUCED to the minimal spine.** A
> `/plan-eng-review` round cut the first slice; the two decisions below **supersede** the
> "First slice" and telemetry-signal sections further down (kept for design context):
> 1. **Keep the tree FLAT** (today's 2 leaves). Add the tunable hook + selection telemetry
>    directly in `canvas_guides.py` (~1 file). **Do NOT** build `infra/skills/` yet, **do NOT**
>    deepen the tree, **do NOT** tag the 23 static skills — all deferred to a later slice, when the
>    engineer's `_route_skills` deletion becomes the *second* consumer that justifies the extraction
>    (Rule of Three; Principle 3, the same deferral logic as the tool-authorization proposal).
> 2. **Outcome signal = a synchronous, server-side proxy**, measured at the answer pass: after the
>    writer calls `load_guide(['plot'])`, did it author a well-formed ` ```plot ` fence?
>    `outcome_scalar = 1.0` if the picked modality produced a valid fence, `0.0` if it produced a
>    *different* modality's fence, **`null` (neutral)** if it peeked then authored no fence (don't
>    penalize correct model restraint). The cross-process render-success signal (frontend
>    `plot.js` → telemetry POST) is a *later* layer per D7 — it does NOT transfer from the Manim
>    pattern because selection (server) and render (frontend) split across the process boundary.
> The reduced plan is at the bottom under **"Implementation plan (REDUCED — eng-reviewed first slice)."**

## Problem

The word "skill" is overloaded across **four** unrelated mechanisms, and *selection* — the
highest-value decision in the agent loop (**D21**: "use-or-not-use is crucial… selection is the
top of the loop") — is scattered across three of them, none tunable except by hand-editing a
Python dict or a `.md` file:

| Mechanism | Where | Selection style | Tunable? |
|---|---|---|---|
| `load_skill` static concat | `workshop/__init__.py`, called by every prompt builder | **none** — ~23 skills glued into the system prompt unconditionally | ✗ (edit the builder) |
| `load_guide` lazy tree | `persona/teacher/prompts/canvas_guides.py` (`GUIDE_TREE`) | LLM calls a tool over a thin menu — **the good pattern** | ✗ (edit the dict) |
| `_route_skills` keyword router | `agents/frontend_engineer/llm_engineer.py:232` | deterministic substring scoring — **a hardcoded router** | ✗ (edit keywords) |
| `public/skills/*.js` renderers | `infra/render/note_md.py:136`, `services/persona/routers/skills.py` | not selection — **delivery** | n/a |

Consequences the model (the primary developer, per `PRINCIPLES.md`) pays for every day:

- **Three vocabularies for one idea.** A change to "how skills are picked" means understanding
  three unrelated code paths. High perplexity, exactly what the North Star forbids.
- **The keyword router is the wrong shape.** `kw in cmd` substring matching (`llm_engineer.py:248`)
  over-matches (`"add"` ⊂ `"ladder"`) and is a hardcoded `if/else`-equivalent — the precise
  anti-pattern S1 rules out ("model-driven judgment, **not hardcoded routing**").
- **The static concat pays full cost every turn.** A flowchart turn still carries plot syntax; a
  text answer still carries canvas verbs. No laziness, no conditioning.
- **skillforge cannot reach the highest-value knob.** Today it tunes one *tool* description
  (`tool.present_coordinate_grid`). The menu — which skills are offered, their summaries, their
  ordering, the selection prompt — is the "hardest, highest-value refinement" (S4), and nothing
  can touch it because there is no menu *object* to register as a tunable.

## The model: one menu loader, three node roles

Adopt the **D1 glossary** as the framework's only vocabulary — no fourth word:

- **Skill** — the *selection* decision at a node ("act directly / which method / decompose").
- **Playbook** — the method/know-how body (a `.md` skill file today).
- **Tool** — the deterministic primitive/leaf (a service call; a `public/skills/*.js` renderer).

The unification is **one LLM-navigated, lazy, tree-shaped menu loader** — the generalized
`load_guide` menu (S1: "exactly today's `load_guide` menu, generalized") — that *replaces* the
static concat's implicit selection **and** the keyword router. Skill bodies stay `.md` files; the
`.js` renderers stay the delivery Tool. Its four locked mechanics (`09-skill-selection.md`) become
the loader's contract:

- **S1 — LLM-navigated menu, retrieval-narrowed.** The persona sees a thin menu of candidate
  Playbooks (summaries) and picks; embedding-narrow to top-k when the menu is large. Never a
  substring router.
- **S2 — "act directly / none" is always in the menu.** The loop degrades to bare-LM; structure
  is opt-in. (This is also the fail-open default.)
- **S3 — availability conditioned by domain** via a `conditioning` tag on each node —
  **Spine** (always) / **Cluster** (loaded by active domain) / **Unlock** (lazy, menu-only). The
  menu is filtered by the active domain *before* the LLM sees it.
- **S4 — the menu + selection prompt are themselves the tunable.** Which nodes are offered, their
  summaries, ordering, and the selection prompt are what skillforge resolves. This is the lift.

**Key reframing:** the existing static-concat-per-pass *is already a crude Cluster mechanism* —
each builder (`answer.py`, `voice_answer.py`, …) hand-assembles the cluster for its turn kind. The
unification doesn't invent conditioning; it **formalizes** what the pass builders do today into a
`conditioning` tag the loader reads, and promotes the visual guides from "hand-selected" to
"menu-navigated."

## Where it lives

The loader is shared by teacher *and* engineer, so by the dep-graph rule (`ARCHITECTURE.md §2.5`,
invariant 1) it is an **`infra` leaf** — the same precedent as `agent_loop.py`, which moved to
`infra/model/` so a second persona could drive it without crossing the persona boundary.

```
infra/skills/
  registry.py     # discover skill files across roots, parse frontmatter → SkillNode
  menu.py         # build the conditioned menu (Spine+Cluster eager, Unlock as menu), render, resolve a pick
  tunable.py      # the skillforge hook: fold resolve().config over the menu (bounded), emit telemetry
  __init__.py     # public surface: build_menu(domain, ...), load(node_ids), the load tool factory
```

Skill **files stay per-domain** — `persona/teacher/skills/`, `workshop/canvas/skills/`,
`agents/frontend_engineer/skills/` — registered as roots exactly as `workshop/__init__.py:_ROOTS`
does today. `infra/skills/` owns the *mechanism*; the domains own the *content*. No new import
edge: persona → infra (allowed); infra never imports persona (invariant 1 preserved).

`load_skill` (`workshop/__init__.py`) becomes the registry's file-reading primitive; `load_guide`
(`canvas_guides.py`) becomes the first *caller* of the generic `infra/skills` menu rather than a
bespoke tree; `_route_skills` is deleted and the engineer's five skills become menu nodes with
`conditioning` tags (their keyword lists become embedding-narrow hints, not a substring gate).

## The skillforge lift — the menu as a `selection` tunable

Register each menu as a `Tunable` with `kind="selection"` (already in `TunableKind`;
`skillforge/core/contracts.py:20`). The tunable id convention mirrors the tool convention
(`tool.<name>`): **`skill_menu.<id>`**, e.g. `skill_menu.canvas_guides`.

`resolve().config` is an open dict the *host* interprets (skillforge enforces no schema — bounding
is the host's job, per the Manim precedent). The menu tunable reads four bounded keys, each
fail-open to the code-defined default:

| config key | effect | bound (host-side, the choke point) |
|---|---|---|
| `offer` | node ids to add/drop from the menu | ⊆ the code-registered node set; never invents a node |
| `summaries` | per-node one-line summary override | `tuned_text`-style: non-empty `str`, `max_len` (≈240) |
| `order` | menu ordering | permutation of offered ids; unknown ids ignored |
| `select_prompt` | selection-prompt preamble override | `tuned_text`-style bounded string (≈2000) |

The injection **copies the proven pattern verbatim** — the same choke points already guarding the
Manim tool: `skillforge_client.tuned_text()` (the `max_len` string clamp,
`infra/skillforge_client.py:108`) and the `min(...)`-within-code-bounds clamp
(`present_coordinate_grid.py:67`). Applied centrally, once, where the menu is built — the analogue
of `manifest.py:567-574` applying the description override for every tunable tool. skillforge can
**narrow or relabel** the menu; it can never inject a node the code didn't register or blow the
LLM-facing size bound.

**Telemetry** (per selection, emitted from the load-tool executor, `collect_result` shape already
built at `infra/skillforge_client.py:136`): `tunable_id="skill_menu.<id>"`, `variant_version` (so
the outcome attributes to the exact menu that ran), `result.ok` = did the picked path execute, and
`outcome_scalar` — the **layered signal**, per D7/D3, *not* an either/or:

1. **Selection/render success = the deterministic gate.** Cheapest, hack-proof necessary condition
   ("the picked guide produced a non-empty render / the tool ran"). **Start here** (D3: refine at
   the layer with the cleanest signal first).
2. **LLM-judge quality** on the Playbook — layered on top: was the pick *appropriate*, not just
   non-crashing.
3. **Block-engagement = the online `outcome_scalar`** (maestro + HLR) — validates *after*
   promotion and drives rollback monitoring; **never gates alone** (anti-Goodhart, D7/D9).

**What this proposal does NOT build** (skillforge's own open gaps, `skillforge/TODO.md` 3–4): the
`refine_auto` loop, the LLM Proposer/Judge wiring (orphaned today), and a real host eval backend
(ToyBackend is hardcoded). Those consume this proposal's output (a registered menu tunable +
telemetry); they are the next proposal, not this one. This lands **enable + bounded-config
injection + telemetry** — the same half that shipped for the Manim tool — so the menu becomes
*hand-tunable and measured* now, *auto-refined* later, with zero rework.

## First slice — the canvas-guide tree (the whole spine, minimal blast radius)

The canvas-writer's guide tree is the ideal proof: already lazy, already menu-shaped, smallest
real tree, one lane (`{writer}`), and it maps onto every framework part at once.

1. **Deepen the tree.** `GUIDE_TREE` (`canvas_guides.py:29`) grows from two flat leaves to a real
   depth-2 tree the depth machinery (`MAX_GUIDE_DEPTH`, `_root_ids`, child menus) already supports:
   `plot → {timeseries, scatter, annotated}`, `mermaid → {flowchart, sequence, state}`. The writer
   loads only the sub-recipe it needs.
2. **Route it through `infra/skills`.** `render_root_menu()` / `get_guide()` become thin callers of
   the generic `infra/skills.menu` (S1/S2/S3), not bespoke code. `load_guide` the *tool* is
   unchanged at the persona boundary (`manifest.py:402`, lane `{writer}`).
3. **Register `skill_menu.canvas_guides`** as a `kind="selection"` tunable; fold `resolve().config`
   (bounded) over the menu; **default-off + fail-open** (empty `skillforge_edge_url` → baseline
   menu, byte-for-byte today's behavior — invariant preserved by construction).
4. **Emit selection telemetry** with render-success as `outcome_scalar` (gate signal), reusing the
   Manim tool's three emit sites as the template.

**Worked example** (the whole spine in one turn, the way `end_session` was walked in the authz
proposal):

> Teacher (Layer 1) judges a claim needs a time-series plot and hands the writer (Layer 2) the
> recipe. The writer's prompt carries `canvas_writer_core` (Cluster, eager) + the **conditioned
> menu** for `skill_menu.canvas_guides`: `[act-directly, plot, mermaid]` (S2 default present). It
> calls `load_guide(['plot','timeseries'])`; the loader resolves the menu against the skillforge
> snapshot (champion variant may have reordered/relabeled it), returns the `timeseries` Playbook
> body, and the writer authors the fence. The `public/skills/plot.js` **Tool** renders it
> (delivery leaf, untouched). On mount, telemetry fires: `skill_menu.canvas_guides`,
> `variant_version=<champion>`, `ok=true`, `outcome_scalar=1.0`. A mount failure banks `0.0` — no
> phantom win (the Manim rule, `present_coordinate_grid.py:104`).

Nothing outside the writer lane changes; the slice ships green and default-off.

## Migration — tiering the static-concat skills (Spine / Cluster / Unlock)

The ~18 teacher skills + 5 canvas skills get a `conditioning` frontmatter tag. **Only Unlock
becomes menu-lazy; Spine stays always-on; Cluster is what the pass builders already do, now
declared instead of hardcoded.** Proposed classification (the migration work, done incrementally —
each pass stays green):

| Tier | Skills | Loaded |
|---|---|---|
| **Spine** (always) | `teaching_principle` | every teacher turn, eager |
| **Cluster: output** (by channel) | `answer_format` (text), `lane_a_voice` (voice) | the active channel's, eager |
| **Cluster: canvas** | `canvas_persona`, `canvas_writer_core`, `workshop/canvas/{grid,layering,lifecycle,state_kinds,tool_verbs}` | canvas-touching turns, eager |
| **Cluster: route/lead** | `router`, `lead_brief`, `lead_routing`, `research_policy` | the lead/route pass, eager |
| **Cluster: gate** | `reflect_policy`, `respond_to_speech`, `posture_honoring`, `stream_emission` | ambient/reflect turns, eager |
| **Cluster: session/goals** | `session_control`, `goal_planning`, `summarize_session` | session-control / planning turns, eager |
| **Unlock** (lazy menu) | `canvas_writer_plot`, `canvas_writer_mermaid` (+ the new depth-2 recipes) | via the menu only |

This is deliberately conservative: it does **not** try to make always-on pedagogy pay a menu
round-trip. It formalizes the pass builders' implicit clusters and moves only the genuinely
optional visual recipes to lazy. The static-concat collapse is a *later* slice; this proposal only
requires the tags exist and the canvas Unlock tier route through the menu.

## Composition with the existing fences

The menu sits *inside* the established filters — it does not replace them. Visible-tool set stays
`grant ∩ lane ∩ mode` (§4.4/§4.5); the **menu conditions the Playbooks a persona is offered**,
one layer further in: `grant ∩ lane ∩ mode ∩ conditioning`, with `resolve().config` tuning the
last term. The `public/skills/*.js` renderer path is preserved unchanged as the delivery Tool
(it deliberately bypasses the note sanitizer to allow `<video>`; unifying must not fight that).

## What stays out of scope / limitations

- **No `refine_auto` here.** This lands enable + bounded injection + telemetry only. Auto-refine
  needs skillforge's ToyBackend replaced (`register_backend` with `benchmark/`) + the Proposer/Judge
  wired — a separate proposal that consumes this one.
- **Domain signal is static tags first.** S3's `conditioning` is a static frontmatter tag now,
  wired to a real classifier/ContextBlock signal "at integration" (S3's own deferral).
- **Retrieval-narrowing (S1 top-k) is deferred** until a menu is actually large; the canvas menu is
  small enough to show whole. Noted, not built.
- **The coarse edge** (per the authz proposal): `common`/`canvas` clusters shared by teacher and
  engineer can't yet express "engineer has canvas but not `speak`" — same known limitation, same
  future deny-set fix.

## Extension point & constraints (noted, not built)

Three things this design settled that bound the *later* slices — recorded so the seams are documented
without expanding the first slice.

**1. `resolve()` is a provider cascade, not a skillforge call.** Today `resolve(id)` reads the
skillforge snapshot and fail-opens to a baseline. Generalize it to a **source-agnostic provider
cascade** — cheapest-first, escalate on miss:

```
resolve(id):  [1] RULE (code)  →  [2] RAG (retrieve)  →  [3] SKILLFORGE (learned)  →  [0] BASELINE (floor)
```

A rule or RAG provider lets a tunable's config *or injected context* come from deterministic code or a
retrieval store instead of the learned variant — which widens the contract from "a bounded knob" to a
resolved **{knob, context}** bundle (RAG can inject retrieved exemplars/facts, not just tune a value).
**Build only the skillforge + baseline providers now; add rule/RAG providers when a real consumer needs
one** — the same no-premature-abstraction discipline as the first-slice reduction. One caveat to make
explicit *per tunable*: **a rule that answers first freezes that case out of the learning loop** —
deterministic where you want a guardrail, learned everywhere else. The cascade order is a decision, not
an accident.

**2. Selection folds into a call the model already makes — never a dedicated round-trip.** A menu pick
is a tool call = a second LLM call. That is fine for the canvas menu (it pays that round-trip today),
but generalizing the menu anywhere else must ride an **existing** decision the model already makes (the
teacher's routing pass), not add a pick-call per turn. Hard constraint for every later slice: no
dedicated selection call.

**3. The loop's missing joint is an *independent* drift detector.** The provider cascade above is the
cheap-first *effector* side; it does **not** close the refine loop. Deciding *per query whether the
cheap provider is still right* needs a detector with an independent vantage + ground-truth calibration —
**not skillforge's own LLM grading its own output** (2026 self-evaluation-bias evidence: a generator
judging itself trends toward missing its own errors). This is the research frontier, out of scope here;
noted so the nicer effector isn't mistaken for the detector still to be built.

## Why this fits the principles

- **Decoupling.** Three selection paths + a keyword router collapse to one mechanism behind one
  boundary (`infra/skills`). No new import edge; `infra` stays the leaf. A change to "how skills
  are picked" now lands in one module — flat blast radius.
- **Self-contained.** `registry`/`menu`/`tunable` are pure over frontmatter + a snapshot dict →
  unit-testable with a fake snapshot (`_set_for_test` already exists, `skillforge_client.py:171`),
  no topology. Fail-open to baseline means the whole subsystem tests green with skillforge absent.
- **Easy to understand.** "Which skills are offered" becomes a conditioned menu + a 4-key config,
  not a substring router plus 10 hand-assembled `load_skill` call sites. The overloaded word gets
  one glossary (D1). Lower perplexity for the next model.

## Protocol impact

Add-only, no existing owner must change:

- **`SkillNode`** gains a `conditioning` frontmatter field (`spine|cluster:<name>|unlock`), default
  `cluster` — an add-only extension; untagged skills keep today's behavior.
- **skillforge**: reuse `kind="selection"` (no new `TunableKind` needed). New tunable-id convention
  `skill_menu.<id>`. `resolve().config` keys (`offer/summaries/order/select_prompt`) are host-side
  convention — skillforge already treats `config` as opaque.
- **`infra/skillforge_client.py`**: no change — the existing `resolve()` / `tuned_text()` /
  `collect_result()` cover it. This proposal is a new *consumer* of the built adapter.

## Implementation plan (first slice)

1. `infra/skills/registry.py` — discover the existing roots, parse frontmatter (incl. new
   `conditioning`), `SkillNode` dataclass. Port `workshop/__init__.py:load_skill` as the reader.
2. `infra/skills/menu.py` — build/condition/render/resolve the menu (S1–S3); "act-directly/none"
   always injected (S2). Unit tests with hand-built nodes.
3. `infra/skills/tunable.py` — fold a bounded `resolve().config` over the menu (copy `tuned_text`
   clamps); the selection-telemetry emit helper.
4. Deepen `GUIDE_TREE` to depth-2 (`plot/*`, `mermaid/*`); re-express `canvas_guides.render_root_menu`
   / `get_guide` as `infra/skills` callers. `load_guide` tool + lane `{writer}` unchanged.
5. Register `skill_menu.canvas_guides`; wire injection at menu-build; emit telemetry at the three
   Manim-style sites. Default-off + fail-open assert.
6. Tag the 23 skills with `conditioning` per the table (behavior-neutral: Cluster = today's default).
7. Tests: menu conditions correctly; bounded config can reorder/relabel but not inject/oversize;
   fail-open with no edge returns the baseline menu byte-for-byte; e2e canvas-writer draws a plot
   and banks `outcome_scalar=1.0`, a forced render failure banks `0.0`.

Each step ships green and default-off; the engineer's `_route_skills` deletion and the
static-concat collapse are explicitly *later* slices on this foundation.

## Implementation plan (REDUCED — the eng-reviewed first slice)

> This supersedes the plan above per the eng-review outcome. ~1-2 files, no new subsystem.

1. **`canvas_guides.py` — fold a bounded tunable over the FLAT menu.** In `render_root_menu()` /
   `get_guide()`, read `skillforge_client.resolve("skill_menu.canvas_guides").config` and apply the
   four bounded keys (`offer` ⊆ registered ids; `summaries`/`select_prompt` via the **existing**
   `skillforge_client.tuned_text()` clamp — no second helper, per DRY; `order` = permutation of
   offered ids). Menu build stays **uncached** (the lru_cache footgun — bodies stay cached, menu does
   not). Fail-open: empty edge url → today's baseline menu byte-for-byte.
2. **Outcome signal at the answer pass.** Capture the `load_guide` id(s) selected this turn in the
   turn context; at answer finalization compare against the authored fence modality. Emit
   `collect_result("skill_menu.canvas_guides", variant_version=<resolved>, ok=…, outcome_scalar=…)`:
   `1.0` modality-match, `0.0` modality-mismatch, **omit/`null` when no fence authored** (neutral).
   Reuse the existing `collect_result` (`skillforge_client.py:136`); no frontend change, no
   correlation_id plumbing.
3. **Register `skill_menu.canvas_guides`** as a `kind="selection"` tunable (baseline variant =
   today's menu); default-off remains the shipped state.
4. **Tests** (all 9 from the coverage view): fail-open baseline byte-for-byte; `offer`/`summaries`/
   `order`/`select_prompt` bounded (oversize→baseline, unknown id ignored); menu reflects a refreshed
   snapshot (not cached); outcome `1.0`/`0.0`/`null` for match/mismatch/peek-then-prose; e2e teacher
   draws a plot → banks `1.0`.

**Deferred to slice 2 (the engineer consumer):** `infra/skills/` extraction, tree-deepening + the 6
Playbook `.md` bodies + `MAX_GUIDE_DEPTH` bump, the 23-skill Spine/Cluster/Unlock tagging, deleting
`_route_skills`, and the cross-process render-success signal.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES_RESOLVED | scope reduced (minimal spine); outcome-signal decided (sync proxy); 1 DRY, 1 lru-cache footgun, 1 signal-semantics caveat |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | n/a (no UI) | — |

**UNRESOLVED:** none — both forks decided (slice scope → minimal spine; outcome signal → synchronous proxy).
**VERDICT:** ENG CLEARED — first slice reduced and de-risked; ready to implement when greenlit.
