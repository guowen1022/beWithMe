# Tool authorization — persona capabilities (proposal)

> **✅ EXECUTED 2026-06-18 (branch `spec/tool-authorization`).** Implemented and tested — 10
> authz unit tests (`tests/unit/test_tool_authz.py`); 30 manifest + 216 unit + 167 e2e green;
> dep-graph leaf clean. Design for trajectory step 4 (`ARCHITECTURE.md §7`); normative summary
> in `ARCHITECTURE.md §4.4`.
>
> **Reconciliation (2026-06-18).** The `end_session` capability shipped first, on its own
> (commit `fa274a4`, *"teacher can end the session — model-routed, two-stage"*) **without** this
> authorization layer. That commit is audited against this standard below (see *Conformance*) —
> it **passes**: it is forward-compatible, no rework needed when authz lands. This proposal was
> also simplified from an earlier two-sided-consent design to **domain-grants** after that
> commit showed nothing in the codebase actually selects a foreign tool.
>
> **Scope.** This proposal is *authorization only* — which tools each persona-LLM may select.
> It is **not** about **dispatch** (how a persona routes a turn to a tool set); that is a
> separate, orthogonal concern, partially realized today and documented in `ARCHITECTURE.md
> §4.5`. The two compose (dispatch chooses *inside* the authz fence) but are designed apart.

## Problem

A persona is the untrusted decision-maker — the LLM emits the tool calls. Today nothing bounds
which tools a given persona may select: the only gate is membership in the list
`build_tools(user_id)` returns, and `agent_loop._execute_tool_calls`
(`infra/model/agent_loop.py:47`) dispatches by name with no ownership check. The "per-persona
allowlist" `§4.2` promises is implicit — "whatever the manifest imported." So there is no
enforced answer to "may the engineer end a session?" or "may the teacher sign the user out?"

## The model: domain grants

Every tool belongs to one **domain** (≈ its package): `common` (`tools/`), `canvas`
(`workshop/canvas/tools/`), `teacher` (`persona/teacher/tools/`), `app`
(`persona/app_operator/tools/`), `engineer` (`agents/frontend_engineer/`).

Each persona carries a **capability grant** — the set of domains it may select from. One rule:

    # infra/model/authz.py
    @dataclass(frozen=True)
    class CapabilityGrant:
        persona: str
        domains: frozenset[ToolDomain]

    def authorize(grant: CapabilityGrant, spec: ToolSpec) -> bool:
        return spec.domain in grant.domains

`ToolSpec` (`infra/model/tools.py`) gains one field with a safe default — an add-only extension
of protocol **P5**, so existing owners are untouched:

    @dataclass
    class ToolSpec:
        name: str; description: str; params_schema: dict; executor: ToolExecutor
        domain: ToolDomain = ToolDomain.COMMON     # NEW

Grants (`persona/<name>/tools/grants.py`):

    TEACHER      = CapabilityGrant("teacher",      {TEACHER, COMMON, CANVAS})
    APP_OPERATOR = CapabilityGrant("app_operator", {APP})
    ENGINEER     = CapabilityGrant("engineer",     {ENGINEER, COMMON, CANVAS})

The consequences fall straight out of the table — no per-tool flags needed:
- `switch_user` is app-only because **only** app_operator holds `app`.
- the engineer can never select `end_session` (teacher domain).
- the teacher can never select `replace_page` (engineer domain).

## Effects are not selections

`end_session` (teacher) produces the `go_home` effect — an app-scoped `AppAction` — by composing
it **inside its own executor** (`end_session.py:49`, `enqueue_for_user(AppAction("go_home"))`),
below the tool layer. The LLM never *selected* `go_home`; trusted teacher code did. So authz —
which gates *selection* — has nothing to act on, and correctly permits it. Governing which tools
may *emit* which app-scoped contracts would be a separate, stricter layer; explicitly out of
scope. (This is why the model is selection-gated, not effect-gated: the LLM is untrusted, the
executor is vetted code.)

## Enforcement — two points

- **Assembly (primary).** `build_tools()` / `build_session_tools()` filter candidate specs
  through `authorize()`, so the LLM never sees a tool outside its grant. Least privilege at the
  prompt.
- **Dispatch (assertion).** `_execute_tool_calls` re-checks: `if not authorize(grant, spec):
    return {"error": "tool '<name>' not in persona '<p>' grant"}`. Catches assembly drift / a
  stale name leaking via shared history. The loop takes the active `grant` as a new argument
  (`persona → infra`, allowed; infra never imports persona).

**Composition with lanes and modes.** The grant is the *outer* fence. Inside it: the existing
`_TOOL_LANES` filter (`persona/teacher/tools/manifest.py:432`) and the per-turn **mode** the
persona's dispatcher opens (§4.5). Visible set = `grant ∩ lane ∩ mode`. Capability = *may*,
lane/mode = *appropriate now*, prompt = *should*.

## Worked example: ending a session (as it actually ships)

The flow is dispatch (§4.5) + authz (this doc) working together:

1. **Stage 1 — the teacher's model routes.** The fast line carries the teaching tool set
   *plus* `request_session_control` (a teacher-domain signal tool). Guided by the
   `session_routing` skill, the model calls it **only** when it judges the user wants out
   ("I'm done") rather than asking a question ("explain the OSI *session* layer"). Authz: both
   the teaching tools and `request_session_control` are `teacher`/`common`/`canvas` — all in
   the teacher's grant. ✅
2. **Stage 2 — the teacher's model acts.** The spoken reply + canvas draw are suppressed;
   `build_session_tools` opens `{end_session}` (teacher domain). The model selects
   `end_session`; its executor saves transcript + summary (`POST /api/sessions/{id}/end`) and
   composes the `go_home` effect. Authz: `end_session` is teacher domain → in grant. ✅ The
   `go_home` `AppAction` is a composed effect, not a selection → authz doesn't gate it. ✅

No cross-domain *grant* is needed anywhere. The teacher only ever selects teacher/common/canvas
tools; the one app-scoped effect is trusted-executor composition.

## Conformance — does the shipped `end_session` (fa274a4) meet this standard?

**Verdict: PASS — forward-compatible, no rework needed when authz lands.**

| Selection point (fa274a4) | Tool | Domain | In teacher grant? |
|---|---|---|---|
| Stage-1 fast line | teaching set + `request_session_control` | common / canvas / teacher | ✅ |
| Stage-2 session set | `end_session` (`build_session_tools`) | teacher | ✅ |

Every tool the teacher's LLM can select sits in `{teacher, common, canvas}`. No persona selects
a foreign tool. The cross-domain *effect* (`go_home`) is composed by `end_session`'s executor
— permitted by design (effects ≠ selections).

Two non-blocking observations:
- **Minor coupling.** `end_session` hard-codes `AppAction(action="go_home")`, duplicating the
  action string that app_operator's `go_home` tool also emits. Not an authz violation (the
  `AppAction` contract is shared infra, P1), but a small DRY smell — a shared action-name
  constant in `infra/contracts/ui.py` would remove the duplication. Optional.
- **Now enforced (2026-06-18).** `end_session` and `request_session_control` are tagged
  `domain=TEACHER`; the teacher manifest filters through `TEACHER_GRANT`. The conformance above
  is verified by `tests/unit/test_tool_authz.py` (`test_teacher_*` / `test_app_operator_*`).

## What stays denied (the model has teeth)

- `switch_user` (app, owner of `{app}` is app_operator only) — no other persona can select it.
- engineer cannot select `end_session` (teacher) — matches §4.2.
- teacher cannot select `replace_page` / `update_widget` (engineer).

## The coarse edge (known limitation)

`common` and `canvas` are shared by teacher and engineer. Domain grants cannot express "engineer
has canvas but must not `speak`". When that need arrives, add a small per-persona **deny-set**
(`grant.deny: frozenset[str]`) or split the domain. Documented, not built — no consumer yet.

## Future extension — cross-domain grants (no consumer today)

If a future persona must select a *specific* foreign tool (e.g. a `helper` that may select the
app's `go_home` directly, as a real LLM choice rather than a composed effect), reintroduce a
two-sided handshake: tag the tool `grantable` and add it to the consumer's grant. Deferred
deliberately — nothing selects a foreign tool today, so shipping that machinery now would be
speculative (Principle 3).

## Why this fits the principles

- **Decoupling.** No new import edges; `infra` stays the leaf. Replaces the implicit "manifest
  imported it" allowlist with one declarative grant per persona.
- **Self-contained.** `authorize()` is a pure function over a dataclass and an enum →
  unit-testable with zero topology.
- **Easy to understand.** "Who may call what" = a 3-row table + a one-line rule, not 500 lines
  of manifest imports. Domain-grants over two-sided-consent is the readable choice with no
  feature lost (nothing needs the extra machinery).

## Protocol impact (P5)

Add-only: one optional `ToolSpec.domain` field (default `common`) + one optional `grant` arg to
`agent_loop.run()`. No existing owner must change.

## Implementation plan

> ✅ **Executed 2026-06-18** — all steps below landed on branch `spec/tool-authorization`.

1. `infra/model/tools.py` — add `ToolDomain` enum + `ToolSpec.domain` (default `common`).
2. `infra/model/authz.py` — `CapabilityGrant` + `authorize()` + unit tests.
3. Tag domains: `tools/*` → COMMON, `workshop/canvas/tools/*` → CANVAS,
   `persona/teacher/tools/*` → TEACHER (incl. `end_session`, `request_session_control`),
   `persona/app_operator/tools/*` → APP, `agents/frontend_engineer/*` → ENGINEER.
4. `persona/{teacher,app_operator}/tools/grants.py` — the grants; `build_tools()` /
   `build_session_tools()` filter through `authorize()`.
5. `agent_loop.run()` + `_execute_tool_calls` — accept `grant`, re-check on dispatch.
6. e2e: teacher selecting `end_session` succeeds; a synthetic engineer-selects-`end_session`
   and teacher-selects-`switch_user` both return the authz error.

(Dispatch — §4.5 — is **not** part of this plan; it already exists for the teacher and is its
own architecture.)
