# Phase 0 — End-to-End Scenarios

Catalogue of user-facing scenarios this branch must support, with a row
per scenario marking the e2e file that exercises it. Scenarios prefixed
[NEW] do not yet have direct coverage and are added by this PR.

Every scenario is phrased as a real user-visible behaviour, not an
internal contract. Where a real browser cannot reproduce it (timing
gaps, server-side throttles, internal HTTP boundaries), the scenario is
covered by an e2e test against the live sidecar stack — same network
surface the browser would hit.

Forbidden-metric sweep (SPEC §17.5) and per-user isolation are
considered cross-cutting; they appear in their own scenarios but are
also implicitly asserted by the suite as a whole.

---

## A. Inbox lifecycle (the surface a user actually sees)

1. **First proposal lands and is visible in the inbox.**
   POST /api/inbox → GET /api/inbox returns it, status=pending.
   Covered: `test_inbox_create_list_tap_consume`.

2. **Tapping a pending proposal moves it to tapped + emits user.proposal_tapped.**
   POST /api/inbox/{id}/tap returns status=tapped and the event view
   shows the transition.
   Covered: `test_tap_emits_proposal_tapped_event`,
   `test_inbox_create_list_tap_consume`.

3. **Dismissing a pending proposal moves it to dismissed + emits user.proposal_dismissed.**
   And no consume is possible afterwards.
   Covered: `test_dismiss_emits_proposal_dismissed_event`,
   `test_inbox_dismiss_blocks_consume`.

4. **Consuming a tapped proposal marks it consumed + emits user.proposal_consumed.**
   Covered: `test_consume_emits_proposal_consumed_event`,
   `test_inbox_create_list_tap_consume`.

5. **Tap is idempotent: re-tapping does not emit duplicate events.**
   Three taps → one user.proposal_tapped event.
   Covered: `test_idempotent_tap_does_not_emit_duplicate_events`.

6. **Per-user isolation: user B never sees user A's proposals and
   cannot tap them (404, not 403, so existence isn't leaked).**
   Covered: `test_inbox_isolation`.

7. **[NEW] Status filter on GET /api/inbox returns only the requested
   status.** GET /api/inbox?status=pending omits tapped/dismissed/expired.
   Not previously asserted as a positive filter test.

8. **[NEW] Dismissing a tapped proposal is rejected (409).**
   After tap, the only forward move is consume. dismiss must 409.
   Today's code: dismiss-from-tapped raises 409. Test currently absent.

9. **[NEW] Tapping a dismissed proposal is rejected (409).**
   Re-tap from a dismissed terminal state must fail rather than
   silently no-op.

10. **[NEW] Idempotent dismiss is silent (no duplicate event).**
    Same property as #5 but for dismissed.

## B. Inbox stock cap & TTL

11. **Posting more than M proposals expires the oldest with reason='stock_cap'.**
    Covered: `test_stock_cap_expires_oldest_when_over_M`.

12. **TTL sweep on GET /api/inbox expires pending proposals older than
    TTL_HOURS with reason='ttl'.**
    Covered: `test_ttl_sweep_expires_old_pending_on_list`.

13. **[NEW] TTL sweep does NOT touch already-tapped/dismissed/consumed rows
    even if they are older than TTL_HOURS.** Only `pending` rows can
    expire; terminal states are immutable history.

14. **[NEW] Stock cap counts only pending rows.** Five tapped + one new
    pending must NOT trigger expiry of the new pending (cap is per
    `status='pending'`, not total).

## C. Kickoff realization (long → persona → inbox)

15. **K candidates from one kickoff land as K inbox rows grouped by
    kickoff_event_id; re-firing the same kickoff is idempotent.**
    Covered: `test_realize_kickoff_writes_K_proposals`.

16. **[NEW] Realization rejects when X-User-Id header disagrees with
    body.user_id (belt + suspenders auth).** Confirms the kickoff
    endpoint refuses to write for a user the caller didn't authenticate.

17. **[NEW] Realization with zero candidates is a no-op (writes 0 rows,
    no events).** Defensive against the long instance handing an
    empty candidate list.

## D. Engagement-seeding the maestro cache

18. **Tapping a proposal then driving a turn seeds the maestro cache
    with the candidate's posture + opening, and marks the proposal
    consumed.**
    Covered: `test_engagement_seeds_cache_from_tapped_proposal`.

19. **[NEW] If TWO proposals are tapped before a turn, only ONE seeds
    the cache (first by tap time); the other stays tapped.**
    SPEC: one active frame at a time per persona_purpose.

20. **[NEW] Driving a turn while no proposal is tapped leaves the cache
    empty.** No phantom seeding from `pending` or `consumed` rows.

## E. Maestro short-instance signals

21. **A signal with no cache present returns skip_refresh with rationale
    'no cache entry'.**
    Covered: `test_skip_refresh_when_no_cache_entry`.

22. **`signal.turn_arrived` after seeding does not refresh (not a strong
    signal).**
    Covered: `test_skip_refresh_on_turn_arrived_within_active_ttl`.

23. **Strong signals (environment_shift, distress_marker, flow_marker)
    refresh the cache the first time, then back-to-back fire throttles
    the second.**
    Covered: `test_skip_refresh_throttle_on_second_call`.

24. **Posture monotonicity at the wire: a wind_down cache holds against
    a steady signal-hint.**
    Covered: `test_wind_down_holds_against_steady_signal_hint`.

25. **[NEW] Terminal postures (`escalate`, `interrupt_now`) reject ALL
    candidate transitions until engagement ends.**
    Posture unit tests assert this in-process; add HTTP-layer assertion
    that a signal carrying a different posture cannot pull the cache
    back to steady.

26. **[NEW] An unknown posture in a signal body is rejected at the
    posture transition layer — cache stays on the previous posture
    with note 'unknown posture'.**

## F. Maestro long-instance gate

27. **`signal.followup_due` (synthesized) → ACT only when
    due_followups_count > 0; otherwise SILENCE.**
    Existing tests cover the kickoff_log + ACT-with-LLM-empty path;
    add explicit followup_due ACT vs SILENCE assertion.
    [NEW]

28. **Inbox-at-cap forces SILENCE even for capture.* events.**
    [NEW] — covered only at unit-level; add an e2e that posts STOCK_CAP
    proposals then fires a capture event and expects SILENCE.

29. **Cool-down: engagement_ended within MIN_QUIET_AFTER_ENGAGEMENT
    forces SILENCE.**
    [NEW] — assert at HTTP layer through the maestro webhook.

## G. Views & forbidden metrics

30. **`engagement_log` view pairs started/ended; live engagement has
    ended_at = None.**
    Covered: `test_engagement_log_view_pairs_started_and_ended`.

31. **`engagement_log` view is empty for a fresh user.**
    Covered: `test_engagement_log_empty_for_fresh_user`.

32. **Unknown view name returns 404 with the list of valid views in
    the detail.**
    Covered: `test_engagement_log_view_404s_for_unknown`.

33. **Forbidden body keys (session_length, turn_count, …) appear in NO
    event ever written by Phase 0.**
    Covered: `test_no_forbidden_metrics_in_any_emitted_event` — keep,
    but extend to cover inbox interaction bodies too.
    [NEW extension]

## H. Cross-cutting browser-visible behaviour

34. **[BROWSER] Inbox renders K=2 proposals from one kickoff under one
    "A few directions:" grouping; K=1 renders inline without grouping.**

35. **[BROWSER] Tapping a card in the browser updates the card's
    status badge to "tapped" without a full page reload.**

36. **[BROWSER] Mirror page groups events by source family and shows
    every event recorded so far.**

37. **[BROWSER] Expired proposals show with the "expired" badge and no
    tap/dismiss buttons.**

38. **[BROWSER] A dismissed card shows the dismissed badge but is still
    in the listing (history is not hidden).**

---

## Coverage map

| # | Scenario | Covered by | Status |
|---|---|---|---|
| 1 | First proposal visible | test_inbox_create_list_tap_consume | ✓ |
| 2 | Tap emits event | test_tap_emits_proposal_tapped_event | ✓ |
| 3 | Dismiss emits event | test_dismiss_emits_proposal_dismissed_event | ✓ |
| 4 | Consume emits event | test_consume_emits_proposal_consumed_event | ✓ |
| 5 | Tap idempotent | test_idempotent_tap_does_not_emit_duplicate_events | ✓ |
| 6 | User isolation | test_inbox_isolation | ✓ |
| 7 | Status filter | NEW: test_status_filter_returns_only_requested | this PR |
| 8 | Dismiss-from-tapped 409 | NEW: test_dismiss_from_tapped_is_409 | this PR |
| 9 | Tap-from-dismissed 409 | NEW: test_tap_from_dismissed_is_409 | this PR |
| 10 | Idempotent dismiss | NEW: test_idempotent_dismiss_no_duplicate_event | this PR |
| 11 | Stock cap | test_stock_cap_expires_oldest_when_over_M | ✓ |
| 12 | TTL sweep | test_ttl_sweep_expires_old_pending_on_list | ✓ |
| 13 | TTL respects terminal | NEW: test_ttl_sweep_skips_non_pending_rows | this PR |
| 14 | Cap is per-pending | NEW: test_stock_cap_counts_only_pending | this PR |
| 15 | K-candidate realize | test_realize_kickoff_writes_K_proposals | ✓ |
| 16 | Kickoff X-User-Id mismatch | NEW: test_kickoff_rejects_user_id_mismatch | this PR |
| 17 | Empty candidates no-op | NEW: test_kickoff_with_empty_candidates_is_noop | this PR |
| 18 | Cache seed on engagement | test_engagement_seeds_cache_from_tapped_proposal | ✓ |
| 19 | Two-tap single-seed | NEW: test_two_tapped_only_first_seeds_cache | this PR |
| 20 | No-tap no-seed | NEW: test_turn_without_tapped_leaves_cache_empty | this PR |
| 21 | Skip-refresh no cache | test_skip_refresh_when_no_cache_entry | ✓ |
| 22 | turn_arrived not strong | test_skip_refresh_on_turn_arrived_within_active_ttl | ✓ |
| 23 | Strong-signal throttle | test_skip_refresh_throttle_on_second_call | ✓ |
| 24 | wind_down sticky | test_wind_down_holds_against_steady_signal_hint | ✓ |
| 25 | Terminal postures sticky | NEW: test_terminal_posture_blocks_all_transitions_via_wire | this PR |
| 26 | Unknown posture rejected | NEW: test_unknown_posture_in_signal_is_rejected | this PR |
| 27 | followup_due ACT vs SILENCE | NEW: test_followup_due_act_path | this PR |
| 28 | Capture suppressed by cap | NEW: test_capture_silenced_when_inbox_at_cap | this PR |
| 29 | Cool-down silence | NEW: test_cooldown_after_engagement_ended_is_silence | this PR |
| 30 | engagement_log pair | test_engagement_log_view_pairs_started_and_ended | ✓ |
| 31 | empty engagement_log | test_engagement_log_empty_for_fresh_user | ✓ |
| 32 | unknown view 404 | test_engagement_log_view_404s_for_unknown | ✓ |
| 33 | forbidden metrics sweep | test_no_forbidden_metrics_in_any_emitted_event (extended) | this PR extends |
| 34 | Browser: K-group rendering | manual /browse walkthrough | this PR |
| 35 | Browser: live card update | manual /browse walkthrough | this PR |
| 36 | Browser: mirror grouping | manual /browse walkthrough | this PR |
| 37 | Browser: expired badge | manual /browse walkthrough | this PR |
| 38 | Browser: dismissed visible in history | manual /browse walkthrough | this PR |

Scenario count: 38. New e2e cases added by this PR: 14. Browser
walkthroughs: 5.
