# IDEA — Brain / Personalities / Grounding

A three-layer architecture for an AI app that learns about the user and acts on their behalf across multiple modalities and real-world contexts.

## The three layers

```
              ┌─────────┐
              │  BRAIN  │   user model (concepts, mastery, preferences,
              └────┬────┘   history). Pure state. No I/O. No LLM calls.
                   │        Receives structured Learnings only.
       ┌───────────┴───────────┐
       │     PERSONALITIES     │   teacher · helper · worker · coach · …
       │   (agents/characters) │   Each: reads brain → forms intent →
       └───────────┬───────────┘   acts via grounding → deposits Learnings.
                   │
              ┌────┴────┐
              │GROUNDING│   the entire I/O boundary to reality.
              └────┬────┘   symmetric: output AND input.
                   │
   ┌───────────────┼──────────────────────────┐
   │ UI grounding  │  sensor grounding        │ action grounding
   │ text / image  │  screen capture / mic    │ keyboard / clipboard
   │ video / chart │  camera / game window    │ file write / browser
   │ ← user input  │  (observe world)         │ (touch world back)
   └───────────────┴──────────────────────────┘
```

## Why this split

1. **Brain stays pure.** It never talks to I/O. That makes it testable, swappable, and safe to share across personalities. Today's codebases mix the user model with the agent that reasons over it; this separation forces a clean interface.
2. **Personalities are plural and composable.** A teacher answers questions about a passage. A helper fixes a bug. A coach watches gameplay and gives drills. Same brain, different personalities — each with its own prompts, tools, and grounding compositions.
3. **Grounding is symmetric.** Every way the app touches reality belongs here: displaying text to the user, streaming video, listening on a mic, capturing a game window, writing to the clipboard. The Electron shell, a screen recorder, and a chart widget are all the same kind of thing — different modalities behind one boundary.

## Key insight: the existing UI is teacher-specific grounding

A common mistake would be treating the current frontend as a neutral UI layer. It's not. The reader, the passage input, the goal planner — those are the **teacher's** grounding surface. Other personalities will bring their own composed surfaces.

```
personalities/teacher/ui/    Reader, ContentInput, GoalPlanner, …
personalities/helper/ui/     (composes primitives its own way)
personalities/coach/ui/      (video player + drill card feed)

grounding/ui/primitives/     TextStream · ImageView · VideoView
                             SelectionListener · ClipRecorder
                             ChartView · FileDrop
grounding/sensors/           screen capture · mic · camera · game window
grounding/actions/           clipboard · file write · browser control
```

Personalities share **primitives**, not **layouts**. The Electron shell becomes a dumb host that routes screen real estate to whichever personality is active and owns only window-level chrome.

## Real-world grounding: the gaming example

To learn a game like Honor of Kings or Counter-Strike, the app needs to observe the user playing — not just hear them talk about it. No public API for most games, so the grounding does the work:

- **User sessions**: in-app screen-recording of the game window (Electron `desktopCapturer`) → clip stored locally → multimodal LLM extracts time-stamped `SkillObservation`s (concept name, quality score, evidence).
- **Reference sessions ("gurus")**: pro/tournament VODs run through the same analyzer, tagged as reference. Never touch the user's mastery state; provide targets for weakness comparison.
- **Weakness detection**: `reference_mean_quality − user_mean_quality`, weighted by HLR mastery and encounter count, ranks concepts the user is behind on.
- **Drills**: a coach personality reads weaknesses and the brain's preferences, emits concrete drill recommendations.

Critically, none of this is HoK-specific. A `DomainConfig` YAML bundles the concept-extraction prompt, quality rubric, taxonomy seeds, goal/drill prompts. New game / sport / instrument = new YAML, same code.

## Personality contract (sketch)

```python
class Personality(Protocol):
    id: str                                # "teacher", "coach", ...
    def surface() -> UISurface: ...        # composed from grounding primitives
    def on_event(event) -> Intent: ...     # user input, sensor tick, schedule
    def act(intent, brain, grounding) -> Optional[Learning]: ...
```

- Reads brain via a read-only snapshot API.
- Acts by calling grounding primitives — never imports Electron, fetch, or a raw LLM client directly.
- Returns `Learning` events the brain layer persists (concepts, preference updates, edges).

## Grounding contract (sketch)

```python
class Grounding(Protocol):
    ui: UIGrounding          # stream_text, show_image, show_video,
                             # show_chart, listen_selection, clip_recorder, …
    sensors: SensorGrounding # screen_capture, mic, camera, game_window, …
    actions: ActionGrounding # clipboard, file_write, browser, …
```

Each primitive has a single well-typed shape. Mockable in tests. Backed by Electron on desktop, browser APIs on the web, no-op/text-only in headless.

## What this changes vs. a typical "AI app"

| Typical                       | This model                              |
|-------------------------------|-----------------------------------------|
| One agent, hardcoded to one UI| Many personalities sharing a brain      |
| Brain = RAG + embeddings      | Brain = user model (concepts, mastery)  |
| UI = "the frontend"           | UI = one modality of grounding          |
| Sensors = optional plugin     | Sensors are first-class grounding       |
| Domain = baked in             | Domain = YAML config + prompt templates |

## POC scope (for the next project)

Smallest thing that demonstrates the whole loop:

1. **Brain**: concept nodes with HLR mastery; preference embedding; `record_learning()` entry point.
2. **One personality** (coach) with a minimal surface: a video view, a drill card feed.
3. **Grounding primitives**: `ClipRecorder` (Electron screen capture), `VideoView`, `DrillCardFeed`, `TextStream`.
4. **One domain YAML** (e.g. `hok.yaml`) with extraction + drill prompts.
5. **Analyzer**: clip → Whisper transcript + sampled frames → multimodal LLM → `SkillObservation[]` → Learning events into brain.
6. **Reference clip** flow: same analyzer, tagged as reference, skips brain updates.
7. **Weakness view**: bar chart user vs reference per concept; top-K drives drill generation.

If the POC holds up, the current app's reader/goals UI moves under `personalities/teacher/` unchanged, and the `silicon_brain/` module becomes the new `brain/`.

## Open questions to resolve before building

- **Brain API shape**: read-only snapshot vs. live handle? Snapshot is cleaner but can go stale mid-turn.
- **Personality communication**: do personalities talk to each other, or only via shared brain state? Start with brain-only — simpler, forces the right contract.
- **Multimodal LLM provider**: real Anthropic for vision (explicit key) vs. routing via a gateway. Explicit wins for a POC.
- **Clip storage**: local-only vs. object storage. Local-only until multi-device sync matters.
- **Privacy surface**: recording indicator, upload confirmation, clip TTL — non-negotiable before any screen capture ships.
- **Concept canonicalization**: LLMs coin variants. Seed taxonomy + post-hoc embedding dedup gets ~80%; full solution is a separate project.
