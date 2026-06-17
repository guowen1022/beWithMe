# Architecture Principles — the North Star

> **This file is the frozen yardstick. Do not edit it casually.**
>
> It is *not* a description of the code. `ARCHITECTURE.md` is the **map** — what the
> program actually is, written so a language model can understand the system. This file is
> the **North Star** — what *good* looks like for beWithMe. There are many valid ways to
> build good software; this document pins down the one *we* have chosen. Every review round
> (see [`PROCESS.md`](./PROCESS.md)) measures finished work against the principles below.

---

## Why these principles exist: minimize the model's perplexity

beWithMe is built **by a language model**. The model is the primary developer — it reads the
code, plans the change, writes it, and verifies it. So the single most important property of
this architecture, right now, is that it **reduces the perplexity of the model working on
it**.

Perplexity is uncertainty about the next correct token. For a developing model it is
uncertainty about the next correct *edit*: what will this change break, where does this data
come from, can I trust what this function claims to do. Every bit of hidden coupling, every
untestable seam, every clever-but-opaque line raises that uncertainty — and a less certain
model writes worse code, more slowly, with more mistakes.

The three principles below are three ways to lower it:

- **Decoupling** bounds *what* the model must hold in mind to make a change.
- **Self-contained** bounds *how* the model verifies a change is correct.
- **Easy to understand** bounds *how hard each line is to predict* in the first place.

They are not separate goals. They are one goal — a system a model can reason about with
confidence — seen from three angles. When two of them appear to conflict, resolve toward
whichever lowers total perplexity for the next developer (who is a model).

---

## 1. Decoupling — the default, not an optimization

A growing application stays healthy only if its parts are decoupled. We treat the decoupled
design as **the correct way**, not a trade-off we reach for when something gets big. Each
capability is a **standalone service or sidecar** that does not reach into another's
internals; pieces talk across explicit boundaries, never through shared guts.

Coupling is the thing that makes a system get *harder* to change as it grows — one edit
ripples into places you didn't expect. Decoupling is how we keep the cost of a change flat as
the codebase expands: a change lands in one module, behind one boundary, and stops there.

In beWithMe this is literally the shape of the system: each capability is its own FastAPI
sidecar; the shell is the only public face and proxies to the rest; a persona never imports
the silicon brain — it calls it over HTTP; domains exchange typed contracts, not internal
objects. Any one piece can be developed, run, and replaced without dragging the others along.

- ✅ A standalone service/sidecar per capability. HTTP + typed-contract boundaries. A change
  whose blast radius is one module.
- ❌ One module importing another's internals. A "convenience" coupling that welds two
  pieces together. A change that forces synchronized edits across many modules.

**For the model:** decoupling shrinks the context a change requires. To edit a decoupled
module the model needs that module and its contract — not the whole system. It no longer has
to guess at hidden cross-effects, because there are none. Smaller, self-evident context is
lower perplexity.

## 2. Self-contained — so every piece is easy to test

Every module and sidecar is **self-contained**: it can be understood, started, and **tested
on its own**. Decoupling is only real if you can exercise a piece in isolation — a boundary
you can't test alone isn't really a boundary, it's a coupling you haven't noticed yet.

Self-contained means a module carries what it needs and reaches its dependencies through
mockable seams (an HTTP client, a typed contract), so you can stand it up with fakes and test
its behavior without the rest of the system running. In beWithMe the persona runs against a
*fake* `SiliconBrainClient`; each sidecar boots independently; the end-to-end suite exercises
the real boundaries while individual units run alone.

- ✅ A clear public surface with mockable dependencies. A unit test that passes without
  booting the full topology. A sidecar that starts by itself.
- ❌ A module that only works when everything else is up. Hidden global state shared across
  boundaries. Tests that require the whole world to be running.

**For the model:** a self-contained module is one the model can change and *verify* in a tight
local loop — write a test, run it, see green — without standing up the entire system. That
closes the feedback loop fast and locally, which is exactly what a model-driven workflow
depends on, and it bounds how much the model must reason about to trust its own change.

## 3. Easy to understand — readability beats cleverness

Code optimizes for **being understood**. When a clearer implementation costs some time or
performance against a cleverer one, we choose the clear one — and when we notice something
has become hard to follow, we **correct it quickly** toward the readable form rather than
leaving a clever knot for the next reader to untangle.

A reader — human or model — must be able to understand a piece *without deep inference*:
plain control flow, names that say what they mean, data flow you can follow, and a short
comment wherever the intent isn't already obvious. Obscure optimization that hurts clarity is
a defect, not an achievement. We optimize for performance only where it's measured and needed,
and when we must, we **say so in a comment** so the trade-off is legible instead of mysterious.

- ✅ Straight-line logic, descriptive names, obvious data flow. A clear version chosen over a
  clever one. A performance hack that is local, justified, and commented.
- ❌ A micro-optimization that obscures what the code does. Cleverness with no measured reason.
  A hot-path trick left unexplained for the next reader to reverse-engineer.

**For the model:** readable code *is* low-perplexity code. The model predicts the next correct
edit far more reliably when the code plainly says what it does. Cleverness that saves
microseconds but forces a reader to simulate the program in their head costs far more than it
saves — every future edit, by every future model, pays that comprehension tax again.

---

## Amending

These principles are frozen on purpose. If one genuinely needs to change, that is a
deliberate decision, not a silent edit:

1. Open a review round whose scope is the amendment itself.
2. Record in the ledger *which* principle, *why* it no longer serves beWithMe, and *what*
   replaces it.
3. Only then edit this file — and note the amendment date inline on the changed principle.

A principle that's inconvenient is not the same as a principle that's wrong. The default is
to fix the code, not the North Star.
