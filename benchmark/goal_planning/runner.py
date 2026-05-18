"""Goal-planning behavior benchmark runner.

Loads `benchmark/goal_planning/<topic>/questions.yaml` and exercises the
goal planner: create the goal, then walk a sequence of expand/know/
unknown actions against the resulting DAG. Writes the same three
artifacts (`results.json`, `metadata.json`, `comments.md`) under
`<topic>/runs/<round-id>/` as the model_behavior runner.
"""

from __future__ import annotations

import pathlib
import time
import uuid
from datetime import datetime, timezone

import httpx

from benchmark._common import (
    auth_headers,
    comments_template,
    create_or_get_user,
    env_snapshot,
    git_sha,
    list_runnable,
    load_yaml,
    reset_db,
    run_id,
    seed_preferences,
    set_profile,
    write_run_artifacts,
)


PKG_ROOT = pathlib.Path(__file__).resolve().parent


def list_topics() -> list[str]:
    return list_runnable(PKG_ROOT, require_key="goal")


async def run_topic(topic: str, base_url: str, *, did_reset: bool) -> pathlib.Path:
    topic_dir = PKG_ROOT / topic
    yaml_path = topic_dir / "questions.yaml"
    if not yaml_path.exists():
        raise SystemExit(f"No questions.yaml under {topic_dir}")
    data = load_yaml(yaml_path)
    goal_text = data.get("goal")
    if not goal_text:
        raise SystemExit(
            f"{yaml_path} has no `goal:`. Stub slot — add a goal to make it runnable."
        )
    actions = data.get("actions") or []

    print(f"\n{'=' * 60}\nTOPIC: {topic} — {data.get('name', '')}\n{'=' * 60}")

    rid = run_id()
    run_dir = topic_dir / "runs" / rid
    started_at = datetime.now(timezone.utc).isoformat()
    wall_start = time.time()

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        username = f"bench_goal_{topic}_{uuid.uuid4().hex[:6]}"
        user_id = await create_or_get_user(client, username)
        headers = auth_headers(user_id)
        print(f"[user] {username} (id={user_id[:8]}...)")

        await set_profile(client, headers, data.get("profile", ""))
        seeded_prefs = await seed_preferences(client, headers, data.get("preferences"))

        # Create the goal
        print(f"\n--- Creating goal: {goal_text} ---")
        t0 = time.time()
        resp = await client.post(
            "/api/goals", headers=headers, json={"title": goal_text}
        )
        resp.raise_for_status()
        goal_data = resp.json()
        goal_id = goal_data["id"]
        dag = goal_data["dag"]
        planner_transcripts: list[dict] = []
        if goal_data.get("transcript"):
            planner_transcripts.append(
                {"step": "create", "transcript": goal_data["transcript"]}
            )
        print(
            f"  Created in {time.time() - t0:.1f}s — {len(dag['nodes'])} nodes",
            flush=True,
        )

        actions_log: list[dict] = []
        for action in actions:
            act_type = action.get("type")
            node_idx = action.get("node_index", 0)
            non_goal = [n for n in dag["nodes"] if n.get("type") != "goal"]
            if node_idx >= len(non_goal):
                print(
                    f"  [skip] node_index {node_idx} out of range ({len(non_goal)} nodes)",
                    flush=True,
                )
                continue
            target = non_goal[node_idx]
            print(
                f"\n--- Action: {act_type} on [{node_idx}] "
                f"\"{target['label'][:50]}\" ---",
                flush=True,
            )
            t0 = time.time()
            if act_type == "expand":
                resp = await client.post(
                    f"/api/goals/{goal_id}/expand",
                    headers=headers,
                    json={"node_id": target["id"]},
                )
            elif act_type in ("know", "unknown"):
                resp = await client.post(
                    f"/api/goals/{goal_id}/feedback",
                    headers=headers,
                    json={"node_id": target["id"], "action": act_type},
                )
            else:
                print(f"  [skip] unknown action type: {act_type}", flush=True)
                continue
            resp.raise_for_status()
            result = resp.json()
            dag = result["dag"]
            elapsed = time.time() - t0
            print(
                f"  {elapsed:.1f}s — now {len(dag['nodes'])} nodes", flush=True
            )
            if result.get("transcript"):
                planner_transcripts.append(
                    {
                        "step": f"{act_type}:{target['id']}",
                        "transcript": result["transcript"],
                    }
                )
            actions_log.append(
                {
                    "action": act_type,
                    "node_index": node_idx,
                    "target_id": target["id"],
                    "target_label": target["label"],
                    "elapsed_seconds": round(elapsed, 2),
                    "node_count_after": len(dag["nodes"]),
                    "text": result.get("text", ""),
                }
            )

        finished_at = datetime.now(timezone.utc).isoformat()
        wall_total = time.time() - wall_start

        # Summarize by status
        by_status: dict[str, int] = {}
        for n in dag["nodes"]:
            if n.get("type") == "goal":
                continue
            by_status[n.get("status", "?")] = by_status.get(n.get("status", "?"), 0) + 1

        results = {
            "round_id": rid,
            "topic": topic,
            "name": data.get("name", ""),
            "goal": goal_text,
            "user_id": user_id,
            "username": username,
            "final_dag": dag,
            "actions_log": actions_log,
            "totals": {
                "actions": len(actions_log),
                "final_node_count": len(dag["nodes"]),
                "final_edge_count": len(dag.get("edges", [])),
                "by_status": by_status,
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
            "user_profile_snapshot": {
                "self_description": data.get("profile", ""),
                "preferences_yaml": data.get("preferences") or None,
                "preferences_after_put": seeded_prefs,
            },
            "planner_transcripts": planner_transcripts,
        }

        write_run_artifacts(
            run_dir,
            results=results,
            metadata=metadata,
            comments_header=comments_template(rid, topic),
        )

        print(
            f"\n{'=' * 60}\nDONE — {len(actions_log)} actions, "
            f"{len(dag['nodes'])} final nodes ({wall_total:.0f}s wall)\n{'=' * 60}"
        )
        print(f"  results:  {run_dir / 'results.json'}")
        print(f"  metadata: {run_dir / 'metadata.json'}")
        print(f"  comments: {run_dir / 'comments.md'}")
        return run_dir


async def amain(args) -> None:
    if args.reset:
        await reset_db()
    await run_topic(args.topic, args.base_url, did_reset=args.reset)
