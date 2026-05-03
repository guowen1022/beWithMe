"""Placeholder for the engineer's system prompt.

The prompt is currently assembled inline in `llm_engineer.py`. Two parts:

  - `_system_prompt()` (cacheable, turn-independent): `_BASE_PROMPT` + the
    skill index + bodies of skills marked `always: true` in frontmatter.
  - `_routed_passage(command)` (per-turn, rides the user-passage slot):
    bodies of skills whose `keywords` overlap the command, plus the
    template reference if any chosen skill sets `needs_templates: true`.

Skills are auto-discovered from `skills/*.md` via frontmatter — adding a
new skill is a file drop, not a code change. See `ARCHITECTURE.md` § 4.1
and `tools.md` for the registry shape and routing rules. When the prompt
builder grows beyond inline assembly (e.g. when partial-edit verbs or
re-prompt-on-miss land), move it here.
"""
