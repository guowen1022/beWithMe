"""File-Q&A behavior benchmark runner.

This bucket is the focused module test for the file-upload pipeline.
For real per-region exercises ("attach this PDF in the computer_science
session"), put a `file:` block on the session inside `model_behavior` —
the same upload helpers power both paths.

Each `<slug>/questions.yaml` declares one file (PDF via inline text, or
a path on disk for video/audio/image), a user profile, and a flat list
of questions. The runner uploads the file once, then asks each question
sequentially against `/api/ask/stream`. Same three artifacts as the
other benchmark sub-packages: `results.json`, `metadata.json`,
`comments.md` under `<slug>/runs/<round-id>/`.

YAML shape — PDF case (text is rendered into a PDF at runtime):

    name: "Gettysburg Address"
    profile: "..."
    file:
      kind: pdf
      text_source: |
        The actual passage text, as many paragraphs as needed.
    questions:
      - "What year is referenced by 'four score and seven'?"
      - "Why does Lincoln say the world will little note..."

YAML shape — media case (use an existing file on disk):

    name: "..."
    profile: "..."
    file:
      kind: video       # or audio, image
      path: "data/uploads/.../some.mp4"
    questions:
      - "What is happening at the start?"
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
    reset_db,
    run_id,
    safe_json,
    seed_preferences,
    set_profile,
    upload_file_decl,
    write_run_artifacts,
)


PKG_ROOT = pathlib.Path(__file__).resolve().parent


def list_slugs() -> list[str]:
    return list_runnable(PKG_ROOT, require_key="questions")


async def run_slug(slug: str, base_url: str, *, did_reset: bool) -> pathlib.Path:
    slug_dir = PKG_ROOT / slug
    yaml_path = slug_dir / "questions.yaml"
    if not yaml_path.exists():
        raise SystemExit(f"No questions.yaml under {slug_dir}")
    data = load_yaml(yaml_path)
    file_decl = data.get("file") or {}
    questions = data.get("questions") or []
    if not file_decl:
        raise SystemExit(f"{yaml_path} has no `file:` block.")
    if not questions:
        raise SystemExit(f"{yaml_path} has no `questions:`. Stub slot.")

    print(f"\n{'=' * 60}\nFILE SLUG: {slug} — {data.get('name', '')}\n{'=' * 60}")

    rid = run_id()
    run_dir = slug_dir / "runs" / rid
    started_at = datetime.now(timezone.utc).isoformat()
    wall_start = time.time()

    async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
        username = f"bench_file_{slug}_{uuid.uuid4().hex[:6]}"
        user_id = await create_or_get_user(client, username)
        headers = auth_headers(user_id)
        print(f"[user] {username} (id={user_id[:8]}...)")

        await set_profile(client, headers, data.get("profile", ""))
        seeded_prefs = await seed_preferences(client, headers, data.get("preferences"))

        upload = await upload_file_decl(
            client,
            headers,
            file_decl,
            slug_dir=slug_dir,
            default_filename=f"{slug}.pdf",
        )
        print(f"[upload {upload['kind']}] {upload['file_ref']}")

        all_answers: list[dict] = []
        prompts_per_interaction: list[dict] = []
        sid = str(uuid.uuid4())

        for q_idx, qraw in enumerate(questions):
            if isinstance(qraw, dict):
                question = qraw.get("question", "")
                selected_text = qraw.get("selected_text")
            else:
                question = str(qraw)
                selected_text = None

            # For media, append the path reminder to the question so the
            # tool call is unambiguous. For PDFs, document_id handles
            # the link.
            if upload["media_path"]:
                question = f"{question}\n\n(file: {upload['media_path']})"

            result = await ask_one(
                client,
                headers,
                passage=upload["passage_text"],
                selected_text=selected_text,
                question=question,
                session_id=sid,
                document_id=upload["document_id"],
            )
            result["interaction_index"] = q_idx

            if "error" in result:
                print(f"  Q{q_idx + 1}: ERROR {result['error']}", flush=True)
            else:
                cum = time.time() - wall_start
                mark = "✓" if result["concepts_line"] else "✗"
                print(
                    f"  Q{q_idx + 1}: {result['answer_length']} chars in "
                    f"{result['elapsed_seconds']:.1f}s (cum {cum:.0f}s) {mark}",
                    flush=True,
                )

            parts = result.pop("prompt_parts", None)
            if parts is not None:
                prompts_per_interaction.append(
                    {"interaction_index": q_idx, **parts}
                )
            all_answers.append(result)

        await end_session(client, headers, sid)

        wait_time = max(15, len(all_answers)) + 30
        print(f"\n[waiting] {wait_time}s for background tasks...", flush=True)
        await asyncio.sleep(wait_time)

        concepts = await safe_json(client, "/api/concepts", headers, [])
        final_prefs = await safe_json(client, "/api/preferences", headers, {})

        finished_at = datetime.now(timezone.utc).isoformat()
        wall_total = time.time() - wall_start

        valid = [a for a in all_answers if "error" not in a]
        avg_time = sum(a.get("elapsed_seconds", 0.0) for a in valid) / max(len(valid), 1)

        results = {
            "round_id": rid,
            "slug": slug,
            "name": data.get("name", ""),
            "file": upload["file_ref"],
            "user_id": user_id,
            "username": username,
            "answers": all_answers,
            "concept_list": [c.get("name") for c in concepts],
            "totals": {
                "questions": len(all_answers),
                "successful": len(valid),
                "avg_llm_seconds": round(avg_time, 2),
                "concept_count": len(concepts),
                "total_wall_seconds": round(wall_total, 1),
            },
        }

        metadata = {
            "round_id": rid,
            "started_at": started_at,
            "finished_at": finished_at,
            **git_sha(),
            "env": env_snapshot(),
            "base_url": base_url,
            "config": {"reset_db_before": did_reset},
            "file_kind": upload["kind"],
            "file_ref": upload["file_ref"],
            "document_id": upload["document_id"],
            "user_profile_snapshot": {
                "self_description": data.get("profile", ""),
                "preferences_yaml": data.get("preferences") or None,
                "preferences_after_put": seeded_prefs,
                "final_preferences": final_prefs,
            },
            "prompts_per_interaction": prompts_per_interaction,
        }

        write_run_artifacts(
            run_dir,
            results=results,
            metadata=metadata,
            comments_header=comments_template(rid, slug),
        )

        print(
            f"\n{'=' * 60}\nDONE — {len(valid)}/{len(all_answers)} successful "
            f"(avg {avg_time:.1f}s/q, {wall_total:.0f}s wall)\n{'=' * 60}"
        )
        print(f"  results:  {run_dir / 'results.json'}")
        print(f"  metadata: {run_dir / 'metadata.json'}")
        print(f"  comments: {run_dir / 'comments.md'}")
        return run_dir


async def amain(args) -> None:
    if args.reset:
        await reset_db()
    await run_slug(args.slug, args.base_url, did_reset=args.reset)
