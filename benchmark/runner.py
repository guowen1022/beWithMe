"""
Benchmark runner — executes scenarios against the live beWithMe API.

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
from benchmark.scenarios import ALL_SCENARIOS


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

        # Wait for trailing background tasks. Sequential execution lets most
        # background work overlap with later LLM calls, so a shorter wait is fine.
        wait_time = max(10, len(all_answers))
        print(f"\n[waiting] {wait_time}s for background tasks...", flush=True)
        await asyncio.sleep(wait_time)

        # Collect results
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")

        resp = await client.get("/api/concepts", headers=headers)
        concepts = resp.json()
        print(f"\nConcepts: {len(concepts)}")
        for c in concepts[:15]:
            print(f"  - {c['name']} ({c['state']}, x{c['encounter_count']})")
        if len(concepts) > 15:
            print(f"  ... and {len(concepts) - 15} more")

        resp = await client.get("/api/graph", headers=headers)
        graph = resp.json()
        print(f"\nGraph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")

        resp = await client.get("/api/preferences", headers=headers)
        prefs = resp.json()
        print(f"\nPreferences: style={prefs['explanation_style']}, depth={prefs['depth_preference']}, analogy={prefs['analogy_affinity']}")

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


async def main():
    parser = argparse.ArgumentParser(description="beWithMe benchmark runner")
    parser.add_argument("--scenario", type=int, default=1, help="Scenario number (1-based)")
    parser.add_argument("--reset", action="store_true", help="Reset database before running")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend URL")
    args = parser.parse_args()

    if args.reset:
        await reset_db()

    idx = args.scenario - 1
    if idx < 0 or idx >= len(ALL_SCENARIOS):
        print(f"Invalid scenario {args.scenario}. Available: 1-{len(ALL_SCENARIOS)}")
        return

    result = await run_scenario(ALL_SCENARIOS[idx], args.base_url)

    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(results_dir, f"scenario{args.scenario}_{ts}.json")
    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to: {filepath}")


if __name__ == "__main__":
    asyncio.run(main())
