# Review Process — how one round runs

A review round double-checks the system against the North Star
([`PRINCIPLES.md`](./PRINCIPLES.md)) and keeps the map ([`ARCHITECTURE.md`](../ARCHITECTURE.md))
honest. It is a **judgment task performed by a capable model**, not a script. A round both
*measures* and *reconciles*: it can update code or docs (Steps 1 and 3) and it quantifies the
system's health against each principle (Step 2). Everything it does is written to the ledger.

## Who reviews

A high-capability, long-context model (Opus-class), run as a dedicated pass — fresh eyes, not
the same agent that wrote the code. The reviewer must hold the principles, the map, the diff,
and the last several ledger records in context at once and reason across all of them.

## When

- **Continuously** — after each finished unit of work (a feature, a refactor, a PR).
- **Ad hoc** — any time someone wants to audit a slice of the system, or do a full sweep.

## Inputs the reviewer loads first

1. [`PRINCIPLES.md`](./PRINCIPLES.md) — the three dimensions it scores against.
2. [`ARCHITECTURE.md`](../ARCHITECTURE.md) — the map; the reference for what each part *should* be.
3. The last **≥10 ledger records** under [`ledger/`](./ledger/) — the history it compares against.
4. The work under review — `git diff` since the last ledger entry, or a named scope.
5. The actual code — read as deeply as a finding or a score requires.

Mechanical evidence the reviewer gathers and cites (inputs to judgment, never the verdict):

```bash
# Dep-graph — each should return zero hits (Decoupling evidence)
grep -rnE "^(from|import) (app|silicon_brain|persona|services)\." infra/
grep -rnE "^(from|import) (app|persona|services)\." silicon_brain/
grep -rnE "^(from|import) silicon_brain" persona/   # ignore TYPE_CHECKING-guarded blocks

.venv/bin/python -m pytest tests/unit/test_user_data_map.py -q   # data-ownership guard
.venv/bin/python -c "import persona.teacher, services.persona.main, services.knowledge.main; print('OK')"
.venv/bin/python -m pytest tests/e2e/ -q                          # boundaries still work
```

---

## The four steps

### Step 1 — Reconcile the map

**Goal: `ARCHITECTURE.md` reflects the program as it actually is.** Walk the map against the
code and surface every place they disagree — anything outdated, aspirational-as-fact, or
plainly wrong. (Known starting example: §2.5 says `Base` lives in `infra/db.py`, while §10's
import smoke still reads `from silicon_brain.db import Base`.)

Handle each discrepancy **one by one**. For each, decide which side is the truth:

- **The code is right, the doc is stale** → update `ARCHITECTURE.md` to match reality.
- **The doc is the intended design, the code drifted** → update the code to match the doc
  (or, if that's a large change, open it as a finding in Step 3 and note the doc is
  aspirational until then).
- **Ambiguous which is correct** → surface it to the user; don't silently pick.

Record every discrepancy and its resolution (fixed-doc / fixed-code / deferred-to-finding) in
the ledger entry. A round that ends with the map and code in agreement is the goal.

### Step 2 — Score the dimensions

**Goal: a quantified, comparable health score per principle, tracked over time.** Read
`PRINCIPLES.md`, then score each **part of the repository** on each of the three dimensions,
using the standard rubric below. "Parts" are the architectural units: each domain (`infra`,
`silicon_brain`, `persona/<name>`, `tools`) and each sidecar (`services/<name>`), plus
`frontend`/`desktop`. A scoped round scores the parts the work touched; a full sweep scores
all parts.

For each part, assign **0–10** on each dimension:

**D1 · Decoupling** — how independent is this part from the rest?
| Band | Meaning |
|---|---|
| 9–10 | Talks to others only via HTTP + typed contracts; imports no other domain's internals; a change here stays here. |
| 7–8  | Mostly decoupled; one or two minor leaks, no cross-domain internal imports. |
| 4–6  | Real coupling — imports another domain's internals, or a change here reliably forces edits elsewhere. |
| 1–3  | Welded to other parts; can't be reasoned about without them. |
| 0    | No boundary at all. |

**D2 · Self-contained** — can this part be run and tested alone?
| Band | Meaning |
|---|---|
| 9–10 | Boots independently; deps behind mockable seams; unit tests pass without the full topology. |
| 7–8  | Testable alone with modest fakes; a couple of deps awkward to mock. |
| 4–6  | Needs significant scaffolding or a partial system up to test. |
| 1–3  | Only testable with the whole system running. |
| 0    | Not testable in isolation at all. |

**D3 · Easy to understand** — can a reader (model or human) follow it without deep inference?
| Band | Meaning |
|---|---|
| 9–10 | Plain control flow, clear names, obvious data flow; trade-offs commented. |
| 7–8  | Readable with minor rough spots. |
| 4–6  | Real effort to follow; unexplained cleverness in places. |
| 1–3  | Opaque; you must simulate it to understand it. |
| 0    | Unreadable. |

Then compare:

- **Against the last ≥10 ledger records** → per part × dimension, mark the trend
  (↑ improved / ↓ regressed / → flat) and the magnitude. This answers *worse or better*.
- **Against `ARCHITECTURE.md`** → does the part match its intended shape? This answers
  *right or wrong* (and usually feeds back into Step 1).

Report per-part scores **and** a headline per dimension that is the **weakest link (min)**
alongside the mean — so a single rotten part can't be averaged into looking healthy.

> The rubric is meant to be **stable** so trends stay comparable. Changing a band definition
> is like amending a principle: note it in the ledger entry so a trend break is visible, not
> mistaken for a real regression.

### Step 3 — Triage open issues

**Goal: the open-issue list is current and honest.** Walk every still-open finding from the
ledger **one by one**, and for each decide:

- **Resolve** — it's been fixed (in this round or since the last). Mark it resolved with the
  evidence (`file:line` or the commit/round that fixed it).
- **Keep** — still open. Carry it forward, and re-state its severity if it changed.

New problems found in Steps 1–2 are added here as fresh findings (status `open`), so they're
tracked the same way next round.

### Step 4 — Owner simulation (parallel ownership / protocol stability)

**Goal: prove the system supports independent workstreams — one owner per module, each free
to refactor *inside* their module — without anyone changing the shared protocol.** This is the
structural payoff of the principles: if decoupling and self-containment hold, owners don't
block each other, and the **protocol** is the only thing they must agree on. Run this at the
*end* of the round — it's the integration test for the architecture itself.

**The protocol** is the stable, cross-module interface surface. No single owner changes it
alone; touching it is a coordinated decision (an architecture conversation), not a unilateral
edit. It includes:

- `infra/contracts/*` DTOs — the wire types between domains.
- the dep-graph import boundaries (who may import whom) — Principles 1–2.
- the HTTP seams: `SiliconBrainClient` method signatures + the knowledge/sidecar endpoint paths.
- the `ToolSpec` shape + each persona's `build_tools(...)` manifest contract.
- the device/canvas seam: `infra/devices/delivery` (enqueue/subscribe), and
  `infra/topology.SERVICE_OFFSETS` + the route table.
- the persona dispatch boundary (the `addressee` contract).

**Procedure:**

1. **Assign owners** — partition the tree into owned modules, one owner each. Default set
   (adjust per round):

   | owner | territory |
   |---|---|
   | infra | `infra/` — the **protocol provider** (contracts, db, topology, model, devices, perception, rag, client) |
   | silicon_brain | `silicon_brain/` + `services/knowledge/` — user data + its HTTP face |
   | teacher | `persona/teacher/` |
   | app_operator | `persona/app_operator/` |
   | engineer | `agents/frontend_engineer/` |
   | canvas | `workshop/canvas/` |
   | maestro | `services/maestro/` |
   | shell | `services/shell/` + topology wiring |
   | frontend | `frontend/` + `desktop/` |

2. **Simulate each owner** — a fresh agent role-plays the owner, defending their boundary. For
   each, report (with `file:line`): **(a) territory** — files I own; **(b) protocol I consume**
   — the shared interfaces I depend on; **(c) protocol I provide** — the interface others
   depend on me for; **(d) can I work alone?** — could I make a realistic in-module change
   without editing the protocol or another owner's files; **(e) collisions** — shared
   files/contracts two owners would both have to edit.

3. **Synthesize** — produce: the **protocol registry** (the off-limits-to-unilateral-change
   interfaces, each with its provider + consumers); the **conflict map** (seams where owners
   collide → these need coordination, not parallelism); and a **verdict per owner**
   (independent ✓ / blocked ✗ + the leak).

4. **Findings** — any place an owner *cannot* advance without touching the protocol or reaching
   into another module is a decoupling leak → a finding (feeds Step 3 next round).

The rule, echoed from the principles: **owners change their own internals freely; the protocol
changes only by agreement.** A round where every owner is independent ✓ and the protocol
registry is unchanged is the green state — that's exactly what lets N people build at once.

---

## What the round produces

1. One **ledger entry** at `ledger/YYYY-MM-DD-<scope-slug>.md` (template below).
2. One **index line** prepended to [`LEDGER.md`](./LEDGER.md) (newest on top).
3. Any **code/doc changes** made in Steps 1 and 3 (committed as normal work, referenced from
   the entry).

## Ledger entry template

```markdown
# <YYYY-MM-DD> — <scope slug>

- **Reviewer:** <model>
- **Git SHA:** <sha at review time>
- **Scope:** <diff range, directory, feature, or "full sweep">

## Step 1 — Map reconciliation
| # | Discrepancy (doc ↔ code) | Truth | Resolution |
|---|---|---|---|
| 1 | <where the map and code disagree> | <which side is right> | fixed-doc / fixed-code / deferred → F<n> |
<!-- "Map matches code — no discrepancies." if clean -->

## Step 2 — Dimension scores (0–10)
| Part | D1 Decoupling | D2 Self-contained | D3 Readability | Δ vs last | Note |
|---|---|---|---|---|---|
| <part> | <n> | <n> | <n> | ↑/↓/→ | <what moved and why> |
- **Headline:** D1 min <n> (mean <n>) · D2 min <n> (mean <n>) · D3 min <n> (mean <n>)
- **Trend vs last ≥10 records:** <better / worse / flat per dimension, naming what moved>
- **Vs ARCHITECTURE.md:** <parts that match their intended shape; mismatches → Step 1 or a finding>

## Step 3 — Open-issue triage
| Issue | Opened | Decision | Note / evidence |
|---|---|---|---|
| F<n> <title> | <date> | resolved / keep | <fix evidence, or why it stays open> |

## Step 4 — Ownership & protocol simulation
| Owner | Territory | Independent? | Leak / collision (if any) |
|---|---|---|---|
| <owner> | <paths> | ✓ / ✗ | <what would force a protocol change or a cross-owner edit> |
- **Protocol registry (off-limits to unilateral change):** <interfaces + provider → consumers>
- **Conflict map:** <shared seams needing coordination, not parallelism>
- **Verdict:** <can N owners work in parallel without touching the protocol? what blocks it?>

## New findings
### F<n> — <short title>
- **Dimension:** D1 / D2 / D3
- **Severity:** low / medium / high
- **Evidence:** `path/to/file.py:123` — <what's there and why it matters>
- **Recommended action:** <the fix, or "accept — here's why">
- **Status:** open

## Notes
<observations, open questions, things to watch next round>
```

**Severity guide:** `high` = breaks decoupling/testability at a load-bearing seam, or the map
now lies. `medium` = real erosion that still works today. `low` = a smell or placement nit.

## Index line format

In [`LEDGER.md`](./LEDGER.md), newest on top — carry the headline scores and open count so the
trend is scannable without opening each entry:

```
- [YYYY-MM-DD <slug>](ledger/YYYY-MM-DD-<slug>.md) — D1 <n> · D2 <n> · D3 <n> (<↑/↓/→ what moved>) · <n> open
```
