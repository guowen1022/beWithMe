"""
Benchmark runner — executes scenarios against the live beWithMe API.

Usage:
    python -m benchmark.runner [--scenario 1] [--reset] [--base-url http://localhost:8000]

Workflow:
    1. Optionally reset the database (concepts, edges, interactions, preferences)
    2. Set up user profile from scenario
    3. For each session: set the passage, ask each question
    4. Wait for background tasks (embedding + concept extraction)
    5. Collect and report results: concepts extracted, edges created, preferences distilled
"""

import asyncio
import argparse
import json
import time
import httpx
import asyncpg
from benchmark.scenarios import ALL_SCENARIOS


async def reset_db():
    """Clear all user data for a fresh benchmark run."""
    conn = await asyncpg.connect("postgresql://weng@localhost/bewithme")
    await conn.execute("DELETE FROM concept_edges")
    await conn.execute("DELETE FROM concept_nodes")
    await conn.execute("DELETE FROM interactions")
    await conn.execute("DELETE FROM learning_preferences")
    await conn.execute("DELETE FROM profile")
    # Re-create singleton rows
    await conn.execute("INSERT INTO profile (self_description) VALUES ('') ON CONFLICT DO NOTHING")
    await conn.execute("INSERT INTO learning_preferences (explanation_style) VALUES ('balanced') ON CONFLICT DO NOTHING")
    await conn.close()
    print("[reset] Database cleared")


async def run_scenario(scenario: dict, base_url: str):
    """Execute a full scenario against the API."""
    print(f"\n{'='*60}")
    print(f"SCENARIO: {scenario['name']}")
    print(f"{'='*60}")

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        # Set up profile
        resp = await client.put("/api/profile", json={"self_description": scenario["profile"]})
        resp.raise_for_status()
        print(f"[profile] Set: {scenario['profile'][:60]}...")

        session_id = None
        total_questions = 0
        answers = []

        for session in scenario["sessions"]:
            print(f"\n--- Session: {session['title']} ---")
            # New session ID per reading session
            import uuid
            session_id = str(uuid.uuid4())

            for selected_text, question in session["interactions"]:
                total_questions += 1
                payload = {
                    "passage_text": session["passage"],
                    "selected_text": selected_text,
                    "question": question,
                    "session_id": session_id,
                }

                print(f"  Q{total_questions}: {question[:60]}...")
                start = time.time()

                # Use the streaming endpoint
                try:
                    resp = await client.post("/api/ask/stream", json=payload)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    print(f"    ERROR: {e.response.status_code} - retrying in 3s...")
                    await asyncio.sleep(3)
                    resp = await client.post("/api/ask/stream", json=payload)
                    resp.raise_for_status()

                # Parse SSE response
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
                # Check for CONCEPTS: line
                concepts_line = ""
                for l in answer_text.split("\n"):
                    if l.strip().upper().startswith("CONCEPTS:"):
                        concepts_line = l.strip()
                        break

                answers.append({
                    "question": question,
                    "answer_length": len(answer_text),
                    "concepts_line": concepts_line,
                    "elapsed": round(elapsed, 1),
                })
                print(f"    Answer: {len(answer_text)} chars in {elapsed:.1f}s")
                if concepts_line:
                    print(f"    {concepts_line}")

                # Small delay between questions
                await asyncio.sleep(1)

        # Wait for background tasks to complete
        print(f"\n[waiting] 60s for background tasks (embedding + concept extraction)...")
        await asyncio.sleep(60)

        # Collect results
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")

        # Concepts
        resp = await client.get("/api/concepts")
        concepts = resp.json()
        print(f"\nConcepts extracted: {len(concepts)}")
        for c in concepts:
            print(f"  - {c['name']} (state={c['state']}, encounters={c['encounter_count']})")

        # Graph
        resp = await client.get("/api/graph")
        graph = resp.json()
        print(f"\nGraph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
        for e in graph["edges"][:10]:
            print(f"  - {e['source']} -- {e['target']} (w={e['weight']}, type={e['type']})")

        # Preferences
        resp = await client.get("/api/preferences")
        prefs = resp.json()
        print(f"\nPreferences:")
        for k in ["explanation_style", "depth_preference", "analogy_affinity", "math_comfort", "pacing"]:
            print(f"  {k}: {prefs[k]}")
        if prefs["meta_notes"]:
            print(f"  meta: {prefs['meta_notes'][:100]}")

        # Summary
        print(f"\n--- Summary ---")
        print(f"Total questions: {total_questions}")
        print(f"Total concepts: {len(concepts)}")
        print(f"Total edges: {len(graph['edges'])}")
        avg_time = sum(a["elapsed"] for a in answers) / len(answers)
        print(f"Avg response time: {avg_time:.1f}s")
        concepts_found = sum(1 for a in answers if a["concepts_line"])
        print(f"Answers with CONCEPTS line: {concepts_found}/{total_questions}")

        return {
            "scenario": scenario["name"],
            "questions": total_questions,
            "concepts": len(concepts),
            "edges": len(graph["edges"]),
            "avg_time": round(avg_time, 1),
            "concepts_extraction_rate": f"{concepts_found}/{total_questions}",
            # Detailed data for analysis
            "answers": answers,
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
    print(f"\n{json.dumps(result, indent=2)}")

    # Save results to file
    import os
    from datetime import datetime
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"scenario{args.scenario}_{ts}.json"
    filepath = os.path.join(results_dir, filename)
    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to: {filepath}")


if __name__ == "__main__":
    asyncio.run(main())
