# architecture-review/

A standing discipline for keeping beWithMe true to the architecture we chose. As work
finishes, a review round double-checks it against a fixed set of principles, and every round
is written down. The point is to catch drift early — before the codebase quietly becomes a
different system than the one we designed.

## The two documents, and why they're different

- **[`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the map.** *Descriptive.* What the program
  actually is, written so a language model can understand the system. It evolves as the code
  evolves.
- **[`PRINCIPLES.md`](./PRINCIPLES.md) — the North Star.** *Prescriptive, and frozen.* "This
  way, not that way; this is good, that is bad." There are many valid ways to build good
  software — this doc pins down the one *we* have chosen for beWithMe. It is the yardstick.

The map tells you where things are. The North Star tells you whether they're where they
*should* be.

## What's in here

| File | Role |
|---|---|
| [`PRINCIPLES.md`](./PRINCIPLES.md) | The frozen North Star — good vs. bad, per principle. **Don't edit casually.** |
| [`PROCESS.md`](./PROCESS.md) | How one review round runs: reviewer, inputs, evidence, output. |
| [`LEDGER.md`](./LEDGER.md) | Index of all rounds, newest on top, with per-round scores. |
| [`ledger/`](./ledger/) | One markdown file per round: `YYYY-MM-DD-<slug>.md`. |

## Running a round (in one breath)

A capable, long-context model loads [`PRINCIPLES.md`](./PRINCIPLES.md),
[`../ARCHITECTURE.md`](../ARCHITECTURE.md), the last ≥10 ledger records, and the work under
review, then runs **four steps**:

1. **Reconcile the map** — find every place `ARCHITECTURE.md` disagrees with the code and, one
   by one, fix the doc, fix the code, or surface the ambiguous ones.
2. **Score the dimensions** — score each part of the repo 0–10 on Decoupling, Self-contained,
   and Readability (standard rubric in `PROCESS.md`), and compare against history to see what
   got better or worse.
3. **Triage open issues** — walk every still-open finding and resolve it or keep it.
4. **Simulate the owners** — role-play one owner per module and confirm each can advance their
   own workstream without changing the shared protocol; flag any boundary leak.

It writes one ledger entry + one index line, and commits any code/doc changes the steps
required. Full procedure, rubric, and entry template are in [`PROCESS.md`](./PROCESS.md).

## The one rule about the North Star

`PRINCIPLES.md` is frozen on purpose. When code disagrees with it, the default is to fix the
code, not the principle. Changing a principle is a deliberate, recorded amendment (see the
*Amending* section at the bottom of `PRINCIPLES.md`) — never a silent edit.
