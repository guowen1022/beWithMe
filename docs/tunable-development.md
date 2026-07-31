# Developing a Tunable in beWithMe

## The principle

**A tunable is one decision point with one entry point, called by both production
and evaluation.** Not two code paths that agree by convention — one function, two
callers. Evaluation may differ in exactly one way: it stubs the child executors so
nothing is mounted, spoken, or persisted. Everything else — prompt construction,
model, token limits, iteration caps, tool surface — is the same object, not a copy.

**A stub drops the side effect, not the contract.** It still validates its
arguments and still reports failure the way the real child does. The first real run
after this refactor caught the counter-example: the authoring stubs returned success
on a call whose arguments had arrived truncated, where the real executor returns an
error and the loop grants the model a free retry. The retry existed in production and
not in the replay — a difference that is not a side effect, which is the same shape
as the bug this whole document is about.

## Why this is a rule and not a preference

`services/tuning/scorer.py::_replay` was a hand-copy of the canvas-writer loop in
`persona/teacher/writer.py`. Identical `max_tokens`, `max_iterations`,
`terminal_tools`, identical event handling. Every mechanical parameter matched.

The one thing that differed was an input: production passes a real
`voice_transcript`; the eval passed `""`. The writer's prompt ends *"Mount the note
(or do nothing if the spoken answer is complete on its own)"* — so with nothing to
mirror, it correctly did nothing, scored `0.0`, and the result was recorded as
`wrong_guide_opened`.

That ran for a month. Reproducible in one line:

| `voice_transcript` | result |
|---|---|
| `""` | `ok=false, quality=0.0, selected=[]` |
| a real spoken answer | `ok=true, quality=1.0` |

Two copies of one call will drift, and the drift is invisible because nothing
declares what must match.

## What a tunable declares

Three things, written down once and read by both callers:

- **inputs** — what a case must supply for this decision to run at all. Mark the
  ones that are genuinely required. An input that is required and empty is a bug,
  not a hard case.
- **calls** — the children this tunable hands work to. **A tool call is not a
  diagnostic.** It is the boundary between two tunables: the parent's output and
  the child's input on the same edge. `load_guide(ids)` *is* the menu's product,
  which is why ground truth is compared against it.
- **expected** — which field of a case holds the right answer, and which call it is
  checked against.

For `skill_menu.canvas_guides` (`services/tuning/registration.py`):

```
inputs:   input (required), transcript (required),
          canvas_state, existing_notes, related_notes
calls:    load_guide(ids), mount_template(params.markdown), edit_note(ops)
expected: expect_guide, matched against load_guide.ids
```

> **Declare the scenario's spec keys, not your parameter names.** skillforge checks
> a declared name against the scenario `spec`, which carries `input` and
> `transcript`. Declaring the Python parameter names (`question`,
> `voice_transcript`) would reject every stored scenario on the next `/evaluate`.

## What evaluation records

Three channels beside `{ok, quality, outcome}`:

- **calls** — what was handed to each child, with arguments. Recorded, never
  executed. `_AUTHORING_STUBS` in `services/tuning/scorer.py` is the stubbing, and
  it is passed to the shared entry point as `stub_executors` — the single legal
  difference from production. A child that was never called is *absent* from
  `calls`, not present-and-empty, so skillforge can report "it opened nothing" as a
  stated fact.
- **trace** — everything else that happened: text turns, tool results, stop reason,
  timings, errors. Both loops used to drop every non-`tool_call` event, which is why
  the model's own explanation — *"the spoken answer is empty, there's no content to
  mirror"* — was thrown away. That one sentence would have made a day-long
  investigation a five-minute one. Bounded to 40 entries / 2 000 chars per text
  block.
- **failed_because** — why `ok` is false. `except Exception: return _FAIL` used to
  make a crash indistinguishable from a wrong answer, indistinguishable from a
  deliberate decline. The taxonomy now separates them:

  | value | means |
  |---|---|
  | `missing_required_input:<name>` | a degenerate case — refused, not scored |
  | `no_ground_truth` | the scenario declares no `expect_guide` |
  | `not_offered:<id>` | the expected guide isn't on the candidate menu; it cannot win |
  | `declined:nothing_opened` | the writer judged the spoken answer complete on its own |
  | `wrong_guide:opened=[…] expected=<id>` | it opened something else |
  | `guide_render_failed:<id>` | the pick resolved to no real guide body |
  | `timeout:150.0s` | the replay exceeded the server-side ceiling |
  | `crashed:<ExcType>: <msg>` | an exception, named |

## Checklist for a new tunable

1. Identify the decision point. It is one function, not a pipeline.
2. Give it an explicit input/output contract and make production call it.
   `persona/teacher/canvas_writer_pass.py` is the worked example:
   `WriterInputs` in, `WriterPass` out, the loop's knobs as module constants.
3. Register it with skillforge declaring `inputs`, `calls`, `expected`
   (`services/tuning/registration.py::TunableSpec`).
4. Point the tuning sidecar at the *same* function; stub only child executors.
5. Verify a case missing a required input raises rather than scoring zero.
6. Confirm `calls`, `trace`, and `failed_because` come back populated.

## Anti-patterns

- **Reimplementing the call in the scorer.** The failure mode above. If you find
  yourself copying `max_tokens` or `terminal_tools` into the eval, stop —
  `tests/unit/test_canvas_writer_pass.py` fails on exactly that.
- **Defaulting a required input to empty.** A degenerate run that returns a
  plausible number is worse than a loud failure.
- **Deriving the result from tool calls.** Record the calls as the result; record
  everything else as trace. Do not reconstruct one from the other.
- **Collapsing failure modes.** One `_FAIL` for wrong / declined / crashed hides the
  only information worth having when something goes wrong.
- **Letting the eval assemble its own toolset.** Pass stubs to the shared builder
  instead. An eval that calls `build_tools` itself can quietly score a different
  lane than production runs.
- **Recording the raw event when the executor sees something else.** DeepSeek's tool
  channel delivers complete arguments inside `_raw_arguments` even on successful
  calls, and `mount_template`'s executor recovers them. The recorder read the raw
  event, so on 6 of 8 scenarios in the first real run the note was authored fine
  while the record said nothing was authored — and production's outcome telemetry
  had the same blind spot. `recovered_args()` normalizes once, for both callers. A
  recorder that sees less than the executor does is not recording the call.

## Why this generalises

The teacher speaks, types, and draws, and the draw path consumes the speak path's
output. Once each call boundary is declared and recorded, any child — a mount
executor, a subagent, later a tuned function body rather than a prompt — can become
a tunable in its own right. **The parent's recorded calls are already the child's
case set**, with no generation and no labelling needed.

## Pointers

- `persona/teacher/canvas_writer_pass.py` — the shared entry point.
- `persona/teacher/writer.py` — production: gather → call → report.
- `services/tuning/scorer.py` — evaluation: the same call, child executors stubbed.
- `services/tuning/registration.py` — where the contract is declared.
- `tests/unit/test_canvas_writer_pass.py` — the invariant, as tests.
- skillforge `INTEGRATION.md` §3 — the framework side of the same contract.
