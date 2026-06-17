# F6 — module disassembly plan (proposal)

> **✅ EXECUTED 2026-06-17.** Status updated from proposal to done. Actual result: `ask.py` 735→426,
> `browser/main.py` 1138→103, `answer.py` 293→88 (`assemble()` ~40-line orchestrator). 33 e2e green,
> leaf grep clean. Note the browser route modules landed flat (`browse.py`, `web_view_routes.py`),
> not under a `routers/` subpackage — to avoid clashing with the existing `services/browser/web_view.py`.
>
> Original proposal for the finding **F6** (oversized modules, D3 readability). A **pure refactor**:
> no behavior change, verified entirely by the existing e2e suite. Tie-in: Principle 3 (readability) + a bonus on Principle 2 (the extracted helpers become
> unit-testable in isolation, which today they aren't).

Scope: the three files the baseline flagged — `services/persona/routers/ask.py` (~735 lines),
`services/browser/main.py` (~1138 lines), and `persona/teacher/contexts/answer.py:assemble()` (~180
lines). The goal is **thin orchestrators calling cohesive, named helpers** — not maximum file count.
Where the structure-mapping suggested 8–10 fragments, this plan deliberately consolidates to the few
seams that actually pay for themselves.

---

## A. `services/persona/routers/ask.py` → 2 extractions

The router tangles three concerns: HTTP/stream orchestration, **addressee routing** (the `/block` and
`app_operator` early-return paths), and the **voice/auto-speak** state machine. Pull the latter two
out; the `@router.post` handlers + the `run_generation()` spine stay.

| Extract | Into | Contents | ~lines |
|---|---|---|---|
| **Addressee routing** | `services/persona/routers/_ask_addressee.py` | `BLOCK_TRIGGER` regex, `_match_template`, `_block_trigger_stream`, `_app_operator_stream`, and a single `route_addressee(body, user_id) -> StreamingResponse \| None` the handler calls before the teacher path | ~160 |
| **Voice / auto-speak** | `services/persona/routers/_ask_voice.py` | `_resolve_active_channel`, `_strip_for_speech`, `SENTENCE_BOUNDARY`, and an `AutoSpeakBuffer` class encapsulating the sentence-buffer / suppress / flush state currently inlined in `run_generation` (lines ~445–579) | ~140 |

**Stays in `ask.py`:** both `@router.post` handlers, the pre-flight (client/talk-pref/engagement),
the `run_generation()` generator spine (now calling `AutoSpeakBuffer`), interaction persistence, the
SSE queue wiring. Result: ~735 → **~420 lines**, and the two gnarliest concerns become independently
readable + testable.

> Deliberately *not* extracted (avoid over-fragmentation): title-parsing, timing, persistence, SSE
> formatting. They're small and read fine where they are; splitting them into 5 more files would hurt,
> not help.

---

## B. `services/browser/main.py` → 4 extractions (a real split — it's 41KB)

A single-file action dispatcher with ~25 `_do_*` handlers + ARIA snapshotting + two route families.
All extracted code reads the shared Playwright state via `app.state` (passed in as `app`), so there
are **no circular imports** — everything flows downward from `main.py`.

| Extract | Into | Contents | ~lines |
|---|---|---|---|
| **Headless session** | `services/browser/session.py` | every `_do_*` handler, `SessionRequest`, the `_SESSION_HANDLERS` dispatch + `POST /browser/session`, plus the session helpers (`_require_session`, `_close_session`, response capture, `_read_state`) | ~600 |
| **ARIA snapshot + @ref** | `services/browser/snapshot.py` | `parse_aria_snapshot`, `resolve_locator`, `invalidate_refs`, the `_REF_*` constants + `SECTION_TEXT_JS` | ~200 |
| **web_view routes** | `services/browser/routers/web_view.py` | the `WebView*Body` models + the 7 `/browser/web_view/*` endpoints (delegating to the existing `web_view.py` `WebViewClient`) | ~90 |
| **handoff / render** | `services/browser/routers/handoff.py` | `/browser/handoff`, `/browser/resume`, `/browser/render` + their models | ~120 |

**Stays in `main.py`:** the docstring, `lifespan()` (Playwright context init/teardown + all
`app.state` setup), the small public `GET /browser/status` + `/selection`, the `DesktopUnavailable`
exception handler, and the `include_router` wiring. Result: ~1138 → **~150 lines**.

> If `session.py` at ~600 lines still reads heavy, the follow-up is to make `services/browser/session/`
> a package (`handlers.py` / `state.py`), but ship the flat split first and reassess.

---

## C. `persona/teacher/contexts/answer.py:assemble()` → 1 helper module

`assemble()` is a 180-line sequence of ~10 numbered phases (profile, prefs, concepts, embed, doc RAG,
past summaries, history, canvas, prompt build, maestro frame) that mixes retrieval with orchestration.
Extract each phase into a named helper in **one** new module; `assemble()` becomes a ~40-line spine
where the phase order — and the data flow between phases — is visible at a glance.

- **New:** `persona/teacher/contexts/_answer_parts.py` — the ~10 phase helpers, each with one concern
  and explicit inputs/outputs (e.g. `load_user_profiles(...)`, `embed_and_boost_query(...)`,
  `retrieve_document_context(...)`, `capture_canvas_state(...)`, `select_and_build_prompt(...)`,
  `apply_maestro_frame(...)`).
- **`answer.py`** keeps `assemble()` as the orchestrator + the existing `_search_past_summaries` /
  `_fetch_session_history` helpers it already has.

> One module, not the 5 the mapping suggested — for ~180 lines, a single `_answer_parts.py` keeps the
> phases together where the spine can see them. Bonus: each phase helper is now unit-testable.

---

## Sequencing (each step ships green; verify between)

Order by risk, lowest first. Every step is behavior-preserving — the guard is the existing e2e suite.

1. **C (answer.py)** — pure function extraction, no HTTP. Verify: `pytest tests/e2e/test_ask_flow.py`.
2. **A (ask.py)** — extract addressee + voice. Verify: `tests/e2e/test_ask_flow.py`,
   `test_speak_channels.py`, `test_mount_template.py` (the `/block` + app_operator + auto-speak paths).
3. **B (browser/main.py)** — the big split. Verify: the browser e2e (`test_e2e.py` browser cases) +
   `import services.browser.main`. Do `snapshot.py` first (leaf), then `session.py`, then the two
   route modules, then thin `main.py`.

After each: `import` smoke for the touched sidecar + the relevant e2e. No new dep-graph edges are
introduced (all extractions stay within their service/persona module), so the leaf greps stay clean.

## Verification (whole F6)

```bash
# behavior unchanged — the e2e suite is the guard
.venv/bin/python -m pytest tests/e2e/test_ask_flow.py tests/e2e/test_speak_channels.py \
  tests/e2e/test_mount_template.py tests/e2e/test_e2e.py -q
# sidecars still import
.venv/bin/python -c "import services.persona.main, services.browser.main; print('OK')"
# no new cross-module coupling
grep -rnE "^(from|import) (app|silicon_brain|persona|services)\." infra/   # still 0
```

Success = every touched e2e test stays green and each oversized file drops to the target size
(ask.py ~420, browser/main.py ~150, assemble() ~40). Nothing about the wire contract, the tool
manifest order, or the SSE event shapes changes.
