# Decision log — `feat/tuning-scenario-regions`

Producer side of the anti-overfitting fix: beWithMe's eval scenarios now carry
`spec["region"]` and `spec["split"]` so skillforge can split train/holdout and
gate per region instead of behind one aggregate mean.

This file records judgment calls that were **not** specified in the task, so a
reviewer can audit them without reverse-engineering the diff. I had no
interactive channel while doing the work.

---

## 1. Train/holdout assignment of the 8 curated scenarios

**Chosen** (5 train / 3 holdout = 62.5% train):

| # | input | region | split | why |
|---|---|---|---|---|
| 1 | plot the parabola y = x^2 over [-3, 3] | plot | **holdout** | `guard: True` — guards are gate-side by definition |
| 2 | scatter of height vs weight with a fit line | plot | train | clear-cut plot anchor |
| 3 | flowchart of the checkout process | mermaid | train | clear-cut mermaid anchor |
| 4 | sequence diagram of the API handshake | mermaid | train | clear-cut mermaid anchor |
| 5 | bar chart of quarterly sales by region | mermaid | train | the registry's most counterintuitive rule |
| 6 | diagram of y = sin(x) from 0 to 2π | plot | train | borderline: "diagram" wording, plot content |
| 7 | show how the shell, persona, and knowledge services talk | mermaid | **holdout** | borderline mermaid — the mermaid-side regression detector |
| 8 | visualize how reaction rate changes with temperature | plot | **holdout** | borderline plot — plot-side regression detector |

Resulting distribution: train = 2 plot + 3 mermaid; holdout = 2 plot + 1 mermaid.

**Two principles drove this, beyond the required invariant:**

1. **The counterintuitive rules go in train.** Scenario 5 ("bar/line charts
   route to mermaid even though they sound numeric") is the one rule a
   proposer cannot possibly infer from first principles. Withholding it would
   not test the proposer, it would just guarantee failure on a rule nobody told
   it about — noise, not signal.
2. **Holdout must not be all easy wins.** A holdout of only clear-cut cases
   ("sequence diagram of X") passes almost any menu and therefore proves
   nothing at the gate. So holdout deliberately carries two *borderline* rows
   (7, 8) plus the guard. Borderline rows are the sensitive regression
   detectors.

**Rejected alternative A — put the borderline set in train, clear-cut in
holdout.** This maximizes proposer headroom (borderline cases are where a
better menu can actually move the pick). Rejected because it makes the gate
blind exactly where regressions happen: the observed real-world failure was a
*subtle* break, which a clear-cut holdout would sail past.

**Rejected alternative B — swap #4 and #7** (clear-cut sequence diagram to
holdout, borderline services-diagram to train). Rejected for the same reason as
A: it would leave holdout's single mermaid row a clear-cut one.

**What would make a different choice right:** if a refine round shows the
proposer failing scenario 5-style routing *because it never had enough
borderline examples to generalize from*, move #8 to train and accept a weaker
plot-side gate. That is a real tradeoff, not a bug — with 8 rows you cannot
have both a rich train set and a rich holdout set.

## 2. Keeping both regions on both sides with only 8 scenarios

The invariant (a region present only in train is a region whose regression the
gate structurally cannot detect) is now an explicit named test —
`test_both_regions_on_both_sides_of_split` in
`tests/unit/test_tuning_scenarios.py` — not an incidental property of the data.
I verified it has teeth by mutating the set in memory so no mermaid row remains
in holdout; the test fails with a message naming the missing side.

**The honest limitation:** with 8 rows and a ~60/40 split, holdout has 3 slots.
Both regions fit, but one region necessarily gets a **single** holdout row
(here: mermaid, scenario 7). One row is enough to satisfy the invariant and
enough to catch a *total* collapse of that region; it is thin for catching a
small degradation, and it is stochastic (the scorer replays a real writer).
This is called out in the `scenarios.py` docstring as a known limit rather than
a design target. It self-corrects as `capture.py` grows the set from real
traffic — that is the main reason the capture policy below matters.

I deliberately did **not** invent new curated scenarios to fatten holdout.
Synthetic rows written by me to pad a partition would be exactly the kind of
eval-set inflation that makes a gate look healthier than it is.

## 3. Capture split policy — assigned by origin

`from_failure → train`, `from_traffic → holdout`, `region = expect_guide`.
This was specified in the task; I implemented it as given and documented the
reasoning in the `capture.py` docstring. Recorded here only because of the
consequence worth watching: over time this fills train with real failures and
holdout with real successes, which is a *better* balance than the curated set
achieves — but it also means holdout grows only as fast as the 10% success
sample allows (cap 20). The existing sampling/cap/truncation/guard policy is
untouched; tests pin it.

## 4. Where the task framing was wrong

**Task 3 said to "confirm the new fields flow through" to the registration POST
body.** They do, with no code change required: `registration.py` builds the
POST spec as `{k: v for k, v in sc.items() if k != "guard"}`, so any key added
to a scenario dict is forwarded automatically. I added a test pinning this
(`test_scenario_specs_carry_region_and_split`) rather than new plumbing. This
is the one task item that needed **zero** production-code change — worth
flagging so a reviewer does not go looking for a diff that should not exist.

Everything else in the framing matched the code as I found it.

## 5. Scope — what I deliberately did NOT do

- **Did not touch the live skillforge store.** No POSTs, no deletes, no
  re-registration. The live dev store's 10 pre-existing rows carry neither
  field and, because dedup is by `spec["input"]` and the eval service exposes
  only add/delete (no update endpoint), they will stay untagged on every future
  boot. Fixing that is a deliberate operator action (prune, then re-register),
  not something this branch should auto-pilot. Documented in the
  `registration.py` docstring and the commit message.
- **Did not add an update or re-register endpoint.** Out of scope, and it would
  have been the wrong place to decide the migration semantics for live rows.
- **Did not restart or interfere with the running sidecars** (8000-8008) or
  skillforge services (8100-8105).

## 6. Test-environment note (not a code change)

The fresh worktree lacked `.env` and `frontend/node_modules` (both gitignored),
which produced 17 environment-only failures unrelated to this change
(`marked` / `mermaid` npm modules missing). I symlinked both from the main
checkout to get a genuine green run. The symlinks are gitignored and are not
part of the commit. No source file was modified to make tests pass.

---

# Round 2 — `oracle_regime` + partial-tagging detection (2026-07-19)

Follow-up to skillforge `4418160`, which published the host-author contract for
`oracle_regime` and scenario `region`. Two changes, both in
`services/tuning/registration.py`.

## 7. `oracle_regime: "validate"` — and why the obvious one-line fix is a no-op

Declared as a named module constant, `_ORACLE_REGIME = "validate"`, with the
justification in a comment above it rather than in a commit message nobody will
re-read. The short version: `quality` for this tunable comes from an LLM judge
scoring how well a menu *steers* the writer — pedagogy, which a judge only
approximates. Measured run-to-run variance on the live model, identical
body/config/scenario: **p(deviate) 0.00 on clear-cut scenarios, up to 0.33 on
the borderline ones**. The borderline scenarios are precisely the ones carrying
the refinement headroom, so the noisiest part of the signal is the part that
decides promotions. That is a proxy, not ground truth; auto-promotion on it is
not defensible. `validate` leaves propose/evaluate/gate untouched and only puts
a person on the final promotion.

The value is pinned by an equality test (`== "validate"`) rather than a
membership check, because skillforge treats an **unrecognized** regime string as
non-gated — a typo doesn't fail loudly, it silently restores auto-promotion.

**Where the task framing was incomplete.** The task said registration "currently
sends no `oracle_regime`, so it silently defaults to `reference`" — correct — and
asked me to add the field. Adding the field alone would have changed nothing.
The `POST /api/tunables` call was nested under `if not champion:`, and our live
tunable already has a champion (v1). That branch never runs again for an
onboarded tunable, so the new field would have been **dead code** and the live
tunable would have kept auto-promoting — the exact outcome the task exists to
prevent. Landing the intent required hoisting the tunable POST out of the
champion guard.

That hoist is safe, and specifically because of the upsert semantics the task
quoted. `store.register_tunable` (skillforge `store/store.py:35`) inserts when
absent; otherwise it touches **only** `oracle_regime`, and only when tightening
(`row.oracle_regime != oracle_regime and oracle_regime != "reference"`).
Champion, enabled, and description are untouched. So re-declaring on every boot
is idempotent. The `/variants` and `/enabled` POSTs stay behind the champion
guard — those are the ones that must never repeat, and the updated
`test_existing_tunable_and_scenarios_only_upserts_host` now asserts both halves:
the tunable POST happens, the variant/enabled POSTs do not.

I verified the guard has teeth by mutating the code back to the nested form:
2 tests fail, including the named regression test.

## 8. Partial tagging — warn, never delete

`_warn_on_partial_tagging()` runs after the remote scenario list is fetched and
prints a single `[tuning] WARNING: ...` line naming the offending ids and the
remedy. It follows the module's existing idiom (`print(..., flush=True)`, the
same thing `main.py` uses for the registration summary) — no logger introduced.
The offending rows also come back in the summary dict as `scenarios_untagged`,
so `POST /register` surfaces them to an operator, not just to stdout.

**Warn-only was a constraint, and it is the right one.** The untagged rows in
the live store include captured production failures — the only genuine headroom
this loop has. Auto-deleting them to make a coverage metric look clean would
destroy the most valuable data in the system to fix a reporting problem. The
test `test_check_never_deletes` pins this against a fake client that *records*
deletes rather than merely lacking the method, so the assertion is real.

**Detection is `region` OR `split` missing**, not both — a row with one label and
not the other is just as broken for gating, and `test_warns_when_only_one_label_
field_is_missing` covers it. The check is gated on the local set actually being
tagged (`local_tagged`), so it stays quiet in a hypothetical future where
beWithMe stops tagging.

**Fail-open**, per the task: the whole body is wrapped, and any exception prints
a one-line skip notice and returns `[]`. A diagnostic must never be the thing
that blocks boot.

## 9. One adjacent fix the fail-open test forced

Writing `test_check_is_fail_open_on_unexpected_row_shape` exposed a
**pre-existing** fragility that is not mine: the dedup comprehension
`{(row.get("spec") or {}).get("input") for row in ...}` raises `AttributeError`
on any non-dict row, so a malformed store response took registration down before
my check ever ran. Registration is documented as fail-open, so this was a real
(if unlikely) gap. Fixed minimally by filtering to dicts once, feeding both the
dedup and the label check from that clean list. Behavior for well-formed data is
byte-identical. Flagging it because it is the one edit in this round that is not
strictly one of the two requested tasks.

## 10. Scope — what I did NOT do

- **No live-state changes.** No POST/DELETE against any running skillforge
  service; the 10 untagged rows in the live store are untouched. This branch
  only makes them *visible* on the next boot. Pruning them stays the operator's
  call, as does the `refine_auto` / stale `tool.speak` work tracked elsewhere.
- **No update endpoint.** Still the right call, and now explicitly the reason
  the warning has to name deletion as the remedy.
- **Did not restart or touch running services** (8000-8008, 8100-8105).
