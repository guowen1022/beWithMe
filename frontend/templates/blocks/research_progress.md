---
keywords: research progress investigation plan steps
purpose: "Visible step-by-step progress ribbon for the teacher's research mode. Shows the goal, the planned steps, which step is currently in flight, and per-step findings as they accumulate. Updates via push_block_content on the block's content topic."
subscribes: [__CONTENT_TOPIC__]
publishes: []
grid:
  x: 0
  y: 0
  w: 12
  h: 3
---

The research_progress block is the user-visible face of the teacher's
research mode (Lane R). When the teacher starts a multi-step
investigation, this ribbon mounts at the top of the canvas and updates
live as the agent plans, executes, and notes findings.

State payload shape (sent via push_block_content on the block's content
topic, `text.research-progress.content`):

```js
{
  goal: string,           // the user's question / what we're investigating
  steps: [
    {
      text: string,                                  // step description
      status: 'pending' | 'doing' | 'done' | 'error',
      note: string | null                            // ≤ 280 chars finding
    }
  ],
  finished: boolean,      // true when the synthesis has been delivered
}
```

Layout (compact, designed for a 12×3 desktop slot at top of canvas):

  - Header strip with a `RESEARCHING` chip + the goal (italic, muted).
  - One row per step. Left: state dot (◯ pending, ◐ doing animated,
    ● done, ✕ error). Middle: step text in 12 px sans. Right: ellipsis
    or status caption.
  - Below each `done` step: the finding rendered in a smaller secondary
    line (~11 px, faint).
  - On `finished=true`, the ribbon collapses to a single one-line chip
    showing the goal + step count; clicking re-expands to audit the
    investigation.

State reporting: emits `kind: 'research_progress'` with `content: '<n>/<m> · <current>'`
and `extra: {goal, steps, finished}` so read_media surfaces the same
information the persona/user sees.
