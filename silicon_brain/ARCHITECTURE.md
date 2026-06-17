# silicon_brain — module architecture (owner reference)

> Scope reference for the **silicon_brain** workstream (the user's data + its HTTP face). Root
> map: [`../ARCHITECTURE.md`](../ARCHITECTURE.md); North Star:
> [`../architecture-review/PRINCIPLES.md`](../architecture-review/PRINCIPLES.md); this records the
> **owner simulation** (Step 4 of [`../architecture-review/PROCESS.md`](../architecture-review/PROCESS.md)).

silicon_brain is **the user's brain** — neutral, user-stated data only (every table has
`user_id`). Personas reach it **over HTTP**, never by import.

## Territory
- `silicon_brain/models/*` — `User`, `Profile`, `UserPreferences` (stated prefs: voice + talk),
  `Document`/`DocumentChunk`, `Event`, `InboxProposal`, `FeedCandidate`, `NoteChunk`. All
  `user_id`-keyed, `CASCADE` on user delete.
- `silicon_brain/` operations (retrieval, concept/knowledge ops, schemas).
- **The HTTP face: `services/knowledge/routers/*`** — auth, users, profile, documents,
  retrieval, notes, event_stream, inbox, feed_candidates, talk_preference, voice-preference, media.

## Protocol I provide
- The knowledge sidecar endpoints + the `SiliconBrainClient` methods that wrap them (~24:
  `get_profile`, `search_document_chunks`, `emit_event`, `query_stream`, `*_inbox_proposal`,
  `*_feed_candidate`, `get_talk_preference`, `get_voice_preferences`, …).
- The **content** of the inbox/feed DTOs in `infra/contracts/{inbox,feed}.py` (infra owns the dir).
- Consumers: tools, personas, maestro — all via `SiliconBrainClient` over HTTP.

## Protocol I consume
`infra.contracts.*` DTOs, `infra.db` (Base/get_db), `infra.auth`, `infra.topology`. No upward imports.

## Can I work alone?
Yes — tables, routers, and schemas are mine; adding a table + endpoint + DTO + client method is
**add-only safe**. The one ripple: **changing a `SiliconBrainClient` signature** hits 20+
consumers — add a new method instead, or coordinate.

## Collisions (coordinate, don't parallelize)
- `infra/silicon_brain_client.py` — I add methods; infra owns the file.
- `infra/contracts/*` — I own the inbox/feed DTO content; infra owns the directory; add-only.

## Boundary rules I keep
1. Import only infra: `grep -rnE "^(from|import) (persona|services)\." silicon_brain/` → **0**.
2. Nobody imports `silicon_brain.models` except my own knowledge routers + infra's registration
   manifest (`infra.user_data.load_domains`). A tool/persona that imports my ORM is a protocol bypass.
3. **The user/teacher split (F7, fixed 2026-06-17):** `UserPreferences` (what the user *states* —
   voice, talk channel) is mine, served on the knowledge sidecar. The teacher's *distilled*
   interpretation (`TeacherPreferenceModel`, `/api/preferences`) is the **teacher's**, served on
   the **persona** sidecar — the knowledge sidecar no longer imports any persona, so a 2nd persona
   can be added without touching me.
