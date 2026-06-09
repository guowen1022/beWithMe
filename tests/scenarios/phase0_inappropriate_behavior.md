# Phase 0 — Inappropriate-behaviour log

Findings from the real-browser walkthrough of `tests/scenarios/phase0_scenarios.md`
that are **not** outright bugs (the code does what it was written to do) but
violate the user's reasonable expectation. Each is logged here rather than
fixed so the decision lives in one place and is reviewable as a whole.

Bugs uncovered by the same walkthrough are fixed in-place; only the
"working-as-written-but-undesirable" cases are here.

## Walkthrough provenance

Real-browser walkthrough on branch `feat/event-stream`, 2026-06-05, via
gstack `/browse`. User UUID `8e934c9a-6bbc-41ef-ba0b-ace860e9ed93`,
username `browse-phase0-1780655258-25274`. Five scenarios driven:

- Scenario 34 (K-group rendering) — PASS, see findings #2 + #3
- Scenario 35 (live tap update) — PASS, see finding #5
- Scenario 36 (mirror event grouping) — PASS, see finding #6
- Scenario 37 (TTL → expired badge) — PASS, see findings #4 + #5
- Scenario 38 (dismissed visible in history) — PASS

## Bugs fixed in the same session

- **NavBar component never mounted in `app/layout.tsx`** — users had no
  visible navigation at all on `/inbox`, `/mirror`, `/recommendations`.
  Defined-but-unused was the smell. Fixed in this PR by importing +
  rendering `<NavBar />` in `RootLayout`.

## Inappropriate-behaviour entries

### IB-1 — Homepage silently swallows 409 on duplicate username

**What happens.** On `/`, the "Enter your name → Go" picker POSTs to
`/api/users`. If the chosen username is already taken, the backend
returns 409. The frontend swallows the error: the input stays filled,
the button stays clickable, no toast/error is rendered. The user sees
"nothing happens" and reasonably concludes the app is broken.

**Reproduction.** Open `http://localhost:3000`, type a username that
already exists in the DB (e.g. `weng`), click Go. Observe: no state
change, no error message. The dev console shows
`Failed to load resource: 409 (Conflict)`.

**Why not fixed.** Backend behaviour is correct (usernames must be
unique). Frontend fix is in the picker component — out of scope for
this PR which is about the Maestro Phase-0 surface. Suggested fix:
display an inline error under the input when `/api/users` 409s,
something like "That name is taken — try another." Keep the Go button
disabled while showing the error.

### IB-2 — User picker is unbounded

**What happens.** `/` lists every user ever created in the DB as a
clickable button — including hundreds of `e2e-*` test users that pile
up over a long e2e session. The snapshot from this session showed 480
user buttons. The picker becomes unusable for picking the real user.

**Reproduction.** Run `pytest tests/e2e` 10× then visit `/`. The
picker has 100+ rows, no filter, no search.

**Why not fixed.** The clean fix has two parts and both touch other
PRs in flight: (a) frontend needs a search box + virtualized list, (b)
backend `/api/users` should support pagination + a name-prefix filter.
Suggested fix: ship search-as-you-type with a backend
`GET /api/users?prefix=...&limit=20`. Phase-1 wishlist.

### IB-3 — Singleton inbox cards have no header; K-group has one

**What happens.** In `/inbox`, a kickoff with K=1 renders the card
alone. A kickoff with K≥2 renders the cards under an "A few
directions:" header. Visually the singleton looks like a top-level
item and the K-group looks like a sub-section — but they're peers in
the data model (both are kickoffs). The screenshot at
`/tmp/phase0-step3-inbox-seeded.png` shows the singleton "Pivot to
transformer overview" sitting above the K-group with no group framing.

**Reproduction.** Seed two kickoffs for one user: one with two
candidates, one with one candidate. Visit `/inbox`. The K=1 looks
"more important" because it's at top and ungrouped.

**Why not fixed.** Either treatment can be argued — the singleton
header would be redundant ("A direction:" → "X"), while the K-group
header is meaningful ("A few directions: X / Y / Z"). The current
asymmetry just *feels* off but isn't wrong. Suggested fix when this
becomes load-bearing: always render under a small kickoff timestamp,
e.g. "From the Maestro · 6:30 PM", to give every kickoff equal visual
framing.

### IB-4 — Inbox ordering puts history above actionable items

**What happens.** `/inbox` renders kickoff groups in newest-first
order, regardless of whether their cards are pending (actionable) or
terminal (consumed / dismissed / expired). After the walkthrough, the
inbox showed a dismissed card at top, then the actionable K-group,
then an expired card at bottom — actionable rows visually buried in
history.

**Reproduction.** Run scenarios 34→35→37 in sequence; observe the
final state. Actionable cards mix in the middle of terminal cards.

**Why not fixed.** The ordering is "newest-kickoff first" — defensible
because that's how event-stream readers expect things. The right fix
is at the UI level: section the page into "Active" / "Recent" /
"Archived" and slot each card by `status`. That's a frontend re-layout
out of scope for the Maestro Phase-0 PR.

### IB-5 — "dismissed" and "consumed" badge colors are indistinguishable

**What happens.** Both badges use low-saturation neutral colors
(dismissed = `bg-gray-100 text-gray-600`, consumed = `bg-gray-50
text-gray-700`). At a glance they read as the same state. The
difference matters: consumed = "I followed through on it", dismissed
= "I rejected it" — that's the entire signal the IPS-trainer in PR-8
will learn from.

**Reproduction.** Open `/inbox` with at least one dismissed and one
consumed proposal. Compare the two badges visually.

**Why not fixed.** The training signal isn't degraded — the backend
distinguishes them perfectly and the events carry the right kinds.
The visual conflation only hurts the user's *own* mental model of
their inbox history. Suggested fix: dismissed → `bg-rose-50
text-rose-700` (subtle "rejected" red), consumed → keep emerald.
One-line tailwind change in `frontend/app/inbox/page.tsx`'s
`STATUS_LABEL` map.

### IB-6 — Mirror page is raw JSON

**What happens.** `/mirror` shows every event with a `JSON.stringify`
of the body, pre-formatted. Beautiful for engineers; opaque for the
target user. A user reading their own mirror cannot tell
`user.proposal_tapped` from `signal.environment_shift` without
guessing.

**Reproduction.** Seed activity for a user, visit `/mirror`. Show it
to a non-engineer. Watch them squint.

**Why not fixed.** Phase 0 positions `/mirror` as the **audit**
surface, not the user-facing surface — the SPEC explicitly calls it
"read-only in Phase 0" and is fine with developer-grade rendering.
Phase 1+ will introduce a user-facing "Your week" surface that takes
the same data and writes prose paragraphs.

## Coverage status

- All 38 scenarios in `tests/scenarios/phase0_scenarios.md` were
  exercised either as automated e2e (16 new + 17 pre-existing) or as
  manual browser walkthrough (5).
- One bug found and fixed in-place (NavBar mount).
- Six inappropriate-behaviour entries (above) — none block the Phase-0
  contract; tracked here for the Phase-1 frontend pass.
