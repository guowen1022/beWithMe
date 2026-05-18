"""Reading-Q&A behavior benchmark runner.

Loads `benchmark/model_behavior/<region>/questions.yaml`, runs each
session sequentially against a live backend, and writes three artifacts
under `<region>/runs/<round-id>/`:
  - results.json   answers + timings (full answer_text)
  - metadata.json  git sha, env, prompts per interaction, profile snapshot
  - comments.md    empty template for human/LLM-judge annotations
"""

from __future__ import annotations

import asyncio
import pathlib
import time
import uuid
from datetime import datetime, timezone

import httpx

from benchmark._common import (
    ask_one,
    auth_headers,
    comments_template,
    create_or_get_user,
    end_session,
    env_snapshot,
    git_sha,
    list_runnable,
    load_yaml,
    render_answers_md,
    reset_db,
    run_id,
    safe_json,
    seed_preferences,
    set_profile,
    set_talk_preference,
    upload_file_decl,
    write_run_artifacts,
)


_VALID_DEVICE_CLASSES = {"desktop", "tablet", "phone"}


def _validate_device_class(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v == "":
        return None
    if v not in _VALID_DEVICE_CLASSES:
        raise SystemExit(
            f"device_class must be one of desktop/tablet/phone (got {value!r})."
        )
    return v


PKG_ROOT = pathlib.Path(__file__).resolve().parent


def list_regions() -> list[str]:
    return list_runnable(PKG_ROOT, require_key="sessions")


async def run_region(region: str, base_url: str, *, did_reset: bool) -> pathlib.Path:
    region_dir = PKG_ROOT / region
    yaml_path = region_dir / "questions.yaml"
    if not yaml_path.exists():
        raise SystemExit(f"No questions.yaml under {region_dir}")
    data = load_yaml(yaml_path)
    sessions = data.get("sessions") or []
    if not sessions:
        raise SystemExit(
            f"{yaml_path} has no sessions. Stub slot — add `sessions:` to make it runnable."
        )

    print(f"\n{'=' * 60}\nREGION: {region} — {data.get('name', '')}\n{'=' * 60}")

    rid = run_id()
    run_dir = region_dir / "runs" / rid
    started_at = datetime.now(timezone.utc).isoformat()
    wall_start = time.time()

    device_class = _validate_device_class(data.get("device_class"))
    talk_pref_yaml = data.get("talk_preference") or None

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        username = f"bench_{region}_{uuid.uuid4().hex[:6]}"
        user_id = await create_or_get_user(client, username)
        headers = auth_headers(user_id, device_class=device_class)
        print(f"[user] {username} (id={user_id[:8]}...)")
        if device_class:
            print(f"[device] X-Device-Class={device_class}")

        await set_profile(client, headers, data.get("profile", ""))
        seeded_prefs = await seed_preferences(client, headers, data.get("preferences"))
        seeded_talk_pref = await set_talk_preference(client, headers, talk_pref_yaml)
        if seeded_talk_pref:
            print(f"[talk_preference] seeded: {seeded_talk_pref}")

        all_answers: list[dict] = []
        prompts_per_interaction: list[dict] = []
        session_uploads: list[dict] = []
        q_num = 0

        for s_idx, session in enumerate(sessions):
            title = session.get("title", "")
            interactions = session.get("interactions") or []
            encourage_visual = bool(session.get("encourage_visual"))

            # A session may declare a file (PDF/video/audio/image) instead
            # of an inline passage. When `file:` is set, upload it now and
            # use the extracted/hint text as the passage. PDFs also pass
            # document_id on each ask so the Interaction is linked.
            file_decl = session.get("file")
            session_doc_id: str | None = None
            if file_decl:
                upload = await upload_file_decl(
                    client,
                    headers,
                    file_decl,
                    slug_dir=region_dir,
                    default_filename=f"{region}-s{s_idx + 1}.pdf",
                )
                passage = upload["passage_text"]
                session_doc_id = upload["document_id"]
                session_uploads.append({"session_index": s_idx, **upload["file_ref"]})
                print(
                    f"[upload {upload['kind']}] session {s_idx + 1} -> "
                    f"{upload['file_ref']}",
                    flush=True,
                )
            else:
                passage = session.get("passage", "")

            print(
                f"\n--- {title} ({len(interactions)} questions, sequential) ---",
                flush=True,
            )
            sid = str(uuid.uuid4())

            for ix in interactions:
                q_num += 1
                selected = ix.get("selected_text")
                question = ix.get("question", "")
                result = await ask_one(
                    client,
                    headers,
                    passage=passage,
                    selected_text=selected,
                    question=question,
                    session_id=sid,
                    document_id=session_doc_id,
                    encourage_visual=encourage_visual,
                )
                result["session_title"] = title
                result["interaction_index"] = q_num - 1

                if "error" in result:
                    print(f"  Q{q_num}: ERROR {result['error']}", flush=True)
                else:
                    cum = time.time() - wall_start
                    mark = "✓" if result["concepts_line"] else "✗"
                    print(
                        f"  Q{q_num}: {result['answer_length']} chars in "
                        f"{result['elapsed_seconds']:.1f}s (cum {cum:.0f}s) {mark}",
                        flush=True,
                    )

                parts = result.pop("prompt_parts", None)
                if parts is not None:
                    prompts_per_interaction.append(
                        {"interaction_index": q_num - 1, **parts}
                    )
                all_answers.append(result)

            await end_session(client, headers, sid)

        wait_time = max(15, len(all_answers)) + 30 * len(sessions)
        print(f"\n[waiting] {wait_time}s for background tasks...", flush=True)
        await asyncio.sleep(wait_time)

        concepts = await safe_json(client, "/api/concepts", headers, [])
        graph = await safe_json(
            client, "/api/graph", headers, {"nodes": [], "edges": []}
        )
        final_prefs = await safe_json(client, "/api/preferences", headers, {})
        talk_pref = await safe_json(client, "/api/talk-preference", headers, None)

        finished_at = datetime.now(timezone.utc).isoformat()
        wall_total = time.time() - wall_start

        valid = [a for a in all_answers if "error" not in a]
        avg_time = sum(a.get("elapsed_seconds", 0.0) for a in valid) / max(len(valid), 1)
        concepts_with_line = sum(1 for a in valid if a.get("concepts_line"))

        # Aggregate multi-modal totals so reviewers see at a glance
        # whether the round actually exercised diagrams / voice / canvas
        # tools. Counts come from the per-result `multimodal` block
        # built in `ask_one`.
        mm_totals = {
            "spoken": 0,
            "diagrams": 0,
            "annotations": 0,
            "mounted_templates": 0,
            "delegations": 0,
            "tool_call_count": 0,
        }
        for a in valid:
            mm = a.get("multimodal") or {}
            for k in mm_totals:
                if k == "tool_call_count":
                    mm_totals[k] += mm.get("tool_call_count", 0)
                else:
                    mm_totals[k] += len(mm.get(k, []))

        results = {
            "round_id": rid,
            "region": region,
            "name": data.get("name", ""),
            "user_id": user_id,
            "username": username,
            "answers": all_answers,
            "concept_list": [c.get("name") for c in concepts],
            "graph": graph,
            "totals": {
                "questions": len(all_answers),
                "successful": len(valid),
                "avg_llm_seconds": round(avg_time, 2),
                "concept_count": len(concepts),
                "graph_edges": len(graph.get("edges", [])),
                "concepts_line_rate": f"{concepts_with_line}/{len(valid)}",
                "total_wall_seconds": round(wall_total, 1),
                "multimodal": mm_totals,
            },
        }

        metadata = {
            "round_id": rid,
            "started_at": started_at,
            "finished_at": finished_at,
            **git_sha(),
            "env": env_snapshot(),
            "base_url": base_url,
            "config": {
                "reset_db_before": did_reset,
                "device_class": device_class,
                "talk_preference_yaml": talk_pref_yaml,
                "talk_preference_after_put": seeded_talk_pref,
            },
            "user_profile_snapshot": {
                "self_description": data.get("profile", ""),
                "preferences_yaml": data.get("preferences") or None,
                "preferences_after_put": seeded_prefs,
                "final_preferences": final_prefs,
                "talk_preference": talk_pref,
            },
            "session_uploads": session_uploads,
            "prompts_per_interaction": prompts_per_interaction,
        }

        answers_md = render_answers_md(
            title=f"Round {rid} — {region}",
            profile=data.get("profile", ""),
            device_class=device_class,
            talk_preference=talk_pref if isinstance(talk_pref, dict) else None,
            answers=all_answers,
        )

        write_run_artifacts(
            run_dir,
            results=results,
            metadata=metadata,
            comments_header=comments_template(rid, region),
            answers_md=answers_md,
        )

        print(
            f"\n{'=' * 60}\nDONE — {len(valid)}/{len(all_answers)} successful "
            f"(avg {avg_time:.1f}s/q, {wall_total:.0f}s wall)\n{'=' * 60}"
        )
        print(f"  results:  {run_dir / 'results.json'}")
        print(f"  metadata: {run_dir / 'metadata.json'}")
        print(f"  comments: {run_dir / 'comments.md'}")
        print(f"  answers:  {run_dir / 'answers.md'}")
        return run_dir


async def amain(args) -> None:
    if args.reset:
        await reset_db()
    await run_region(args.region, args.base_url, did_reset=args.reset)
