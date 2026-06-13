"""app_operator — the persona that performs app-level actions on the user's
behalf (switch user, go home, show the mirror).

Sibling to `persona/teacher/`; see ARCHITECTURE.md §4. Minimal by design:
the "app actions" are deterministic shell operations, so this persona has no
private models and reads no silicon-brain state — it just picks the matching
tool and fires it through the shared agent loop.

Public surface: `respond(question, user_id)` drives one turn and yields the
loop's `delta` / `tool_call` / `done` events.
"""
from persona.app_operator.agent import respond

__all__ = ["respond"]
