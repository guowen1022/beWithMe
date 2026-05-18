"""
Benchmark runner — executes scenarios against the live beWithMe API.

DEPRECATED. Prefer `python -m benchmark.model_behavior --region <name>` or
`python -m benchmark.goal_planning --topic <slug>`. The YAML-driven runners
co-locate each round's results.json, metadata.json (full prompts), and
comments.md next to the question set. This module is retained so the legacy
`python -m benchmark --scenario N` invocation keeps working until all callers
migrate.

Usage:
    python -m benchmark.runner [--scenario 1] [--reset] [--base-url http://localhost:8000]

Questions run sequentially within a session to model a real user reading an
answer and then asking the next follow-up. Sessions also run sequentially.
"""

import argparse
import asyncio
import json
import os
import time
import uuid
import httpx
import asyncpg
from datetime import datetime
from benchmark.scenarios import ALL_SCENARIOS, ALL_GOAL_SCENARIOS


async def reset_db():
    conn = await asyncpg.connect("postgresql://weng@localhost/bewithme")
    await conn.execute("DELETE FROM concept_edges")
    await conn.execute("DELETE FROM concept_nodes")
    await conn.execute("DELETE FROM interactions")
    await conn.execute("DELETE FROM document_chunks")
    await conn.execute("DELETE FROM documents")
    await conn.execute("DELETE FROM learning_preferences")
    await conn.execute("DELETE FROM profile")
    await conn.execute("DELETE FROM users")
    await conn.close()
    print("[reset] Database cleared")


def auth_headers(user_id: str) -> dict:
    return {"Content-Type": "application/json", "X-User-Id": user_id}


async def ask_question(
    client: httpx.AsyncClient,
    headers: dict,
    passage: str,
    selected_text: str,
    question: str,
    session_id: str,
    q_num: int,
    scenario_start: float,
) -> dict:
    """Ask a single question and return timing + extracted concepts line."""
    payload = {
        "passage_text": passage,
        "selected_text": selected_text,
        "question": question,
        "session_id": session_id,
    }
    start = time.time()
    try:
        resp = await client.post("/api/ask/stream", headers=headers, json=payload)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"  Q{q_num}: ERROR {e.response.status_code}", flush=True)
        return {"question": question, "answer_length": 0, "concepts_line": "", "elapsed": 0, "error": str(e)}
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError) as e:
        # SSE stream can be cut short by a server reload or flaky network.
        # One bad question shouldn't torch the whole scenario.
        print(f"  Q{q_num}: TRANSPORT ERROR {type(e).__name__}: {e}", flush=True)
        return {"question": question, "answer_length": 0, "concepts_line": "", "elapsed": 0, "error": f"{type(e).__name__}: {e}"}

    answer_text = ""
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            try:
                event = json.loads(line[6:])
                if event.get("type") == "answer":
                    answer_text = event["answer"]
            except json.JSONDecodeError:
                pass

    elapsed = time.time() - start
    concepts_line = ""
    for l in answer_text.split("\n"):
        if l.strip().upper().startswith("CONCEPTS:"):
            concepts_line = l.strip()
            break

    cum = time.time() - scenario_start
    print(
        f"  Q{q_num}: {len(answer_text)} chars in {elapsed:.1f}s "
        f"(cum {cum:.0f}s) {'✓' if concepts_line else '✗'}",
        flush=True,
    )
    return {
        "question": question,
        "answer_length": len(answer_text),
        "concepts_line": concepts_line,
        "elapsed": round(elapsed, 1),
    }


async def run_scenario(scenario: dict, base_url: str):
    print(f"\n{'='*60}")
    print(f"SCENARIO: {scenario['name']}")
    print(f"{'='*60}")

    scenario_start = time.time()

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        # Create user
        username = f"bench_{scenario['name'][:20].lower().replace(' ', '_')}"
        resp = await client.post("/api/users", json={"username": username})
        if resp.status_code == 409:
            resp = await client.get("/api/users")
            users = resp.json()
            user_id = next(u["id"] for u in users if u["username"] == username)
        else:
            resp.raise_for_status()
            user_id = resp.json()["id"]
        headers = auth_headers(user_id)
        print(f"[user] {username} (id={user_id[:8]}...)")

        # Set profile
        await client.put("/api/profile", headers=headers,
                         json={"self_description": scenario["profile"]})
        print(f"[profile] Set")

        all_answers = []
        q_num = 0

        for session in scenario["sessions"]:
            print(f"\n--- {session['title']} ({len(session['interactions'])} questions, sequential) ---", flush=True)
            session_id = str(uuid.uuid4())

            # Ask questions one at a time — models a real reader working through
            # an article: read passage, ask, read answer, ask follow-up.
            for selected_text, question in session["interactions"]:
                q_num += 1
                result = await ask_question(
                    client, headers, session["passage"], selected_text,
                    question, session_id, q_num, scenario_start,
                )
                all_answers.append(result)

            # End the session — saves transcript and triggers async summarization
            try:
                resp = await client.post(
                    f"/api/sessions/{session_id}/end", headers=headers,
                )
                resp.raise_for_status()
                print(f"  [end session] {session_id[:8]}... OK", flush=True)
            except Exception as e:
                print(f"  [end session] ERROR: {e}", flush=True)

        # Wait for trailing background tasks — includes session summarization
        # (one LLM call per session) which can take 15-30s each.
        n_sessions = len(scenario["sessions"])
        wait_time = max(15, len(all_answers)) + 30 * n_sessions
        print(f"\n[waiting] {wait_time}s for background tasks...", flush=True)
        await asyncio.sleep(wait_time)

        # Collect results
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")

        # Tolerate post-test endpoint flakes — a 504 here shouldn't lose 12
        # successful Q&A answers worth of data.
        async def _safe_json(path, fallback):
            try:
                r = await client.get(path, headers=headers)
                if r.status_code != 200:
                    print(f"\n[warn] {path} -> {r.status_code}; using fallback")
                    return fallback
                return r.json()
            except Exception as e:
                print(f"\n[warn] {path} failed: {type(e).__name__}: {e}; using fallback")
                return fallback

        concepts = await _safe_json("/api/concepts", [])
        print(f"\nConcepts: {len(concepts)}")
        for c in concepts[:15]:
            print(f"  - {c['name']} ({c['state']}, x{c['encounter_count']})")
        if len(concepts) > 15:
            print(f"  ... and {len(concepts) - 15} more")

        graph = await _safe_json("/api/graph", {"nodes": [], "edges": []})
        print(f"\nGraph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")

        prefs = await _safe_json("/api/preferences", {
            "explanation_style": "?", "depth_preference": "?", "analogy_affinity": "?"
        })
        print(f"\nPreferences: style={prefs['explanation_style']}, depth={prefs['depth_preference']}, analogy={prefs['analogy_affinity']}")

        # Show session transcripts and summaries
        import pathlib
        sessions_dir = pathlib.Path(__file__).resolve().parents[1] / "data" / "sessions" / user_id
        if sessions_dir.exists():
            print(f"\n{'='*60}")
            print("SESSION TRANSCRIPTS & SUMMARIES")
            print(f"{'='*60}")
            for session_dir in sorted(sessions_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                sid = session_dir.name[:8]
                transcript_file = session_dir / "transcript.md"
                summary_file = session_dir / "summary.md"
                if transcript_file.exists():
                    transcript = transcript_file.read_text()
                    print(f"\n--- Transcript (session {sid}...) ---")
                    # Show first 1500 chars to keep output manageable
                    if len(transcript) > 1500:
                        print(transcript[:1500])
                        print(f"... ({len(transcript)} chars total, truncated)")
                    else:
                        print(transcript)
                if summary_file.exists():
                    summary = summary_file.read_text()
                    print(f"\n--- Summary (session {sid}...) ---")
                    print(summary)
                else:
                    print(f"\n--- Summary (session {sid}...) ---")
                    print("(not yet generated — summarizer may still be running)")

        valid = [a for a in all_answers if "error" not in a]
        avg_time = sum(a["elapsed"] for a in valid) / max(len(valid), 1)
        concepts_found = sum(1 for a in valid if a["concepts_line"])
        total_time = time.time() - scenario_start

        print(f"\n--- Summary ---")
        print(f"Total questions: {len(all_answers)}")
        print(f"Successful: {len(valid)}/{len(all_answers)}")
        print(f"Concepts: {len(concepts)}")
        print(f"Edges: {len(graph['edges'])}")
        print(f"Avg LLM time: {avg_time:.1f}s")
        print(f"CONCEPTS rate: {concepts_found}/{len(valid)}")
        print(f"Total wall time: {total_time:.0f}s")

        return {
            "scenario": scenario["name"],
            "user_id": user_id,
            "questions": len(all_answers),
            "successful": len(valid),
            "concepts": len(concepts),
            "edges": len(graph["edges"]),
            "avg_llm_time": round(avg_time, 1),
            "total_wall_time": round(total_time, 0),
            "concepts_extraction_rate": f"{concepts_found}/{len(valid)}",
            "answers": all_answers,
            "concept_list": [c["name"] for c in concepts],
            "graph": graph,
            "preferences": prefs,
        }


async def run_goal_scenario(scenario: dict, base_url: str):
    """Run a goal planning scenario: create goal, then execute expand/know/unknown actions."""
    print(f"\n{'='*60}")
    print(f"GOAL SCENARIO: {scenario['name']}")
    print(f"{'='*60}")

    scenario_start = time.time()

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        # Create user
        username = f"bench_goal_{scenario['name'][:15].lower().replace(' ', '_')}"
        resp = await client.post("/api/users", json={"username": username})
        if resp.status_code == 409:
            resp = await client.get("/api/users")
            users = resp.json()
            user_id = next(u["id"] for u in users if u["username"] == username)
        else:
            resp.raise_for_status()
            user_id = resp.json()["id"]
        headers = auth_headers(user_id)
        print(f"[user] {username} (id={user_id[:8]}...)")

        await client.put("/api/profile", headers=headers,
                         json={"self_description": scenario["profile"]})

        # Step 1: Create goal
        print(f"\n--- Creating goal: {scenario['goal']} ---")
        start = time.time()
        resp = await client.post("/api/goals", headers=headers,
                                 json={"title": scenario["goal"]})
        resp.raise_for_status()
        goal_data = resp.json()
        goal_id = goal_data["id"]
        dag = goal_data["dag"]
        elapsed = time.time() - start
        print(f"  Created in {elapsed:.1f}s — {len(dag['nodes'])} nodes")

        # Print initial DAG
        prereqs = [n for n in dag["nodes"] if n["type"] != "goal"]
        for i, n in enumerate(prereqs):
            print(f"  [{i}] {n['id']}: {n['label']} ({n['status']})")

        transcript_text = goal_data.get("transcript", [{}])[0].get("text", "")
        print(f"  Planner: {transcript_text[:120]}...")

        actions_log = []

        # Step 2: Execute actions
        for action in scenario["actions"]:
            act_type = action["type"]
            node_idx = action["node_index"]

            # Find the target node — index into non-goal nodes by current order
            non_goal = [n for n in dag["nodes"] if n["type"] != "goal"]
            if node_idx >= len(non_goal):
                print(f"  [skip] node_index {node_idx} out of range ({len(non_goal)} nodes)")
                continue

            target = non_goal[node_idx]
            print(f"\n--- Action: {act_type} on [{node_idx}] \"{target['label'][:50]}\" ---")

            start = time.time()
            if act_type == "expand":
                resp = await client.post(f"/api/goals/{goal_id}/expand", headers=headers,
                                         json={"node_id": target["id"]})
            elif act_type in ("know", "unknown"):
                resp = await client.post(f"/api/goals/{goal_id}/feedback", headers=headers,
                                         json={"node_id": target["id"], "action": act_type})
            else:
                print(f"  [skip] unknown action type: {act_type}")
                continue

            resp.raise_for_status()
            result = resp.json()
            dag = result["dag"]
            elapsed = time.time() - start

            new_text = result.get("text", "")
            print(f"  {elapsed:.1f}s — now {len(dag['nodes'])} nodes")
            print(f"  Planner: {new_text[:120]}...")

            # Show any new nodes
            new_nodes = [n for n in dag["nodes"] if n["id"] not in {nn["id"] for nn in (actions_log[-1]["dag"]["nodes"] if actions_log else goal_data["dag"]["nodes"])}]
            for nn in new_nodes:
                status_mark = "◆" if nn["status"] == "atomic" else "○"
                print(f"  {status_mark} NEW: {nn['id']}: {nn['label']} ({nn['status']})")

            actions_log.append({"action": act_type, "target": target["label"], "elapsed": elapsed, "dag": dag, "text": new_text})

        # Final summary
        total_time = time.time() - scenario_start
        print(f"\n{'='*60}")
        print("GOAL PLANNING RESULTS")
        print(f"{'='*60}")

        all_nodes = dag["nodes"]
        goal_node = next(n for n in all_nodes if n["type"] == "goal")
        prereqs = [n for n in all_nodes if n["type"] != "goal"]
        by_status = {}
        for n in prereqs:
            by_status.setdefault(n["status"], []).append(n)

        print(f"\nGoal: {goal_node['label']}")
        print(f"Total nodes: {len(all_nodes)} ({len(prereqs)} prerequisites + 1 goal)")
        print(f"Edges: {len(dag['edges'])}")
        for status in ["pending", "atomic", "known", "unknown", "expanded"]:
            nodes = by_status.get(status, [])
            if nodes:
                print(f"\n  {status.upper()} ({len(nodes)}):")
                for n in nodes:
                    print(f"    - {n['label']}")

        # Quality checks
        print(f"\n--- Quality Check ---")
        atomic_nodes = by_status.get("atomic", [])
        pending_nodes = by_status.get("pending", [])
        print(f"Atomic (actionable leaves): {len(atomic_nodes)}")
        print(f"Still pending (expandable): {len(pending_nodes)}")
        if atomic_nodes:
            print(f"Sample atomic nodes:")
            for n in atomic_nodes[:5]:
                # Check if it looks like a real course/practice/concept
                label = n["label"].lower()
                is_course = any(w in label for w in ["course", "tutorial", "book", "read", "take", "complete", "watch"])
                is_practice = any(w in label for w in ["practice", "exercise", "build", "solve", "write", "create", "do", "min/day", "hours", "daily", "weekly"])
                is_concept = any(w in label for w in ["understand", "learn", "know", "grasp", "study"])
                actionable = is_course or is_practice or is_concept
                mark = "✓" if actionable else "?"
                print(f"  {mark} {n['label']}")

        print(f"\nTotal wall time: {total_time:.0f}s")

        return {
            "scenario": scenario["name"],
            "goal": scenario["goal"],
            "total_nodes": len(all_nodes),
            "total_edges": len(dag["edges"]),
            "by_status": {s: len(ns) for s, ns in by_status.items()},
            "dag": dag,
            "actions_log": [{"action": a["action"], "target": a["target"], "elapsed": a["elapsed"]} for a in actions_log],
            "total_wall_time": round(total_time, 0),
        }


async def main():
    parser = argparse.ArgumentParser(description="beWithMe benchmark runner")
    parser.add_argument("--scenario", type=int, default=1, help="Scenario number (1-based)")
    parser.add_argument("--goal", type=int, default=0, help="Goal scenario number (1-based, 0=skip)")
    parser.add_argument("--reset", action="store_true", help="Reset database before running")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend URL")
    args = parser.parse_args()

    if args.reset:
        await reset_db()

    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if args.goal > 0:
        idx = args.goal - 1
        if idx < 0 or idx >= len(ALL_GOAL_SCENARIOS):
            print(f"Invalid goal scenario {args.goal}. Available: 1-{len(ALL_GOAL_SCENARIOS)}")
            return
        result = await run_goal_scenario(ALL_GOAL_SCENARIOS[idx], args.base_url)
        filepath = os.path.join(results_dir, f"goal{args.goal}_{ts}.json")
        with open(filepath, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to: {filepath}")
        return

    idx = args.scenario - 1
    if idx < 0 or idx >= len(ALL_SCENARIOS):
        print(f"Invalid scenario {args.scenario}. Available: 1-{len(ALL_SCENARIOS)}")
        return

    result = await run_scenario(ALL_SCENARIOS[idx], args.base_url)

    filepath = os.path.join(results_dir, f"scenario{args.scenario}_{ts}.json")
    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to: {filepath}")


if __name__ == "__main__":
    asyncio.run(main())
