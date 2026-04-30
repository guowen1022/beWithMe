"""Goal planner — LLM-driven DAG decomposition of learning goals."""

import json
import re
from pathlib import Path
from app.infra.model.llm import generate

_SKILL_PROMPT_PATH = Path(__file__).resolve().parent.parent / "skills" / "goal_planning.md"


def _load_skill_prompt() -> str:
    return _SKILL_PROMPT_PATH.read_text(encoding="utf-8")


def _next_node_id(dag: dict) -> str:
    """Generate the next sequential node ID (n1, n2, ...)."""
    existing = [n["id"] for n in dag.get("nodes", []) if n["id"].startswith("n")]
    nums = []
    for nid in existing:
        try:
            nums.append(int(nid[1:]))
        except ValueError:
            pass
    return f"n{max(nums, default=0) + 1}"


def _parse_llm_response(raw: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    # Strip markdown fences if present
    cleaned = raw.strip()
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
    # Try to find JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        cleaned = cleaned[start:end]
    return json.loads(cleaned)


def _apply_update(dag: dict, update: dict, start_id: int) -> dict:
    """Apply LLM's DAG update to the current DAG state."""
    nodes = list(dag.get("nodes", []))
    edges = list(dag.get("edges", []))
    existing_ids = {n["id"] for n in nodes}

    # Remap node IDs from LLM response to avoid collisions
    id_map = {}
    for node in update.get("nodes_to_add", []):
        old_id = node["id"]
        if old_id in existing_ids and old_id != "goal":
            new_id = f"n{start_id}"
            id_map[old_id] = new_id
            start_id += 1
        else:
            id_map[old_id] = old_id

    # Add new nodes
    for node in update.get("nodes_to_add", []):
        mapped_id = id_map.get(node["id"], node["id"])
        if mapped_id not in existing_ids:
            nodes.append({
                "id": mapped_id,
                "label": node["label"],
                "type": node.get("type", "prerequisite"),
                "status": node.get("status", "pending"),
            })
            existing_ids.add(mapped_id)

    # Add new edges
    for edge in update.get("edges_to_add", []):
        source = id_map.get(edge["source"], edge["source"])
        target = id_map.get(edge["target"], edge["target"])
        if source in existing_ids and target in existing_ids:
            # Avoid duplicate edges
            if not any(e["source"] == source and e["target"] == target for e in edges):
                edges.append({"source": source, "target": target})

    # Update existing nodes
    for upd in update.get("nodes_to_update", []):
        for node in nodes:
            if node["id"] == upd["id"]:
                if "status" in upd:
                    node["status"] = upd["status"]
                if "label" in upd:
                    node["label"] = upd["label"]

    return {"nodes": nodes, "edges": edges}


def _build_context(dag: dict, transcript: list) -> str:
    """Build the context message for the LLM showing current DAG state."""
    parts = []
    if dag["nodes"]:
        parts.append("CURRENT DAG STATE:")
        parts.append(json.dumps(dag, indent=2))
    if transcript:
        parts.append("\nCONVERSATION HISTORY:")
        for entry in transcript[-6:]:  # last 6 turns
            parts.append(f"[{entry['role']}]: {entry['text']}")
    return "\n".join(parts)


async def plan_initial(goal_text: str) -> dict:
    """First call: decompose a goal into initial prerequisites.

    Returns {text, dag} where dag has the goal node + prerequisites.
    """
    skill_prompt = _load_skill_prompt()
    user_msg = (
        f"The user's learning goal is: \"{goal_text}\"\n\n"
        f"Break this down into 4-6 high-level prerequisites. "
        f"The goal node must have id \"goal\" and label \"{goal_text}\".\n"
        f"All prerequisites at this level should have status \"pending\" — they are high-level and will be expanded later.\n\n"
        f"Respond with JSON only."
    )

    raw = await generate(user_msg, system=skill_prompt, max_tokens=2048)
    update = _parse_llm_response(raw)

    # Build initial DAG
    dag = {
        "nodes": [{"id": "goal", "label": goal_text, "type": "goal", "status": "pending"}],
        "edges": [],
    }
    dag = _apply_update(dag, update, start_id=1)

    return {
        "text": update.get("text", ""),
        "dag": dag,
    }


async def plan_expand(node_id: str, dag: dict, transcript: list) -> dict:
    """Expand a node into sub-prerequisites.

    Returns {text, dag} with updated DAG.
    """
    node = next((n for n in dag["nodes"] if n["id"] == node_id), None)
    if not node:
        return {"text": f"Node {node_id} not found.", "dag": dag}

    skill_prompt = _load_skill_prompt()
    context = _build_context(dag, transcript)
    start_id = int(_next_node_id(dag)[1:])

    user_msg = (
        f"{context}\n\n"
        f"The user wants to EXPAND node \"{node_id}\" (\"{node['label']}\").\n\n"
        f"FIRST: Judge whether this node is already actionable. Is it a real course/book people can find, "
        f"a time-bounded practice, or a single-session concept? If YES → set its status to \"atomic\" "
        f"and do NOT add sub-nodes. Explain in \"text\" what the user should actually go do.\n\n"
        f"ONLY if the node is still too vague to act on → break it into 3-5 sub-prerequisites. "
        f"Set node \"{node_id}\" status to \"expanded\". Use node IDs starting from n{start_id}. "
        f"Mark any sub-nodes that are themselves actionable as \"atomic\".\n\n"
        f"Respond with JSON only."
    )

    raw = await generate(user_msg, system=skill_prompt, max_tokens=2048)
    update = _parse_llm_response(raw)
    new_dag = _apply_update(dag, update, start_id=start_id)

    return {
        "text": update.get("text", ""),
        "dag": new_dag,
    }


async def plan_feedback(node_id: str, action: str, dag: dict, transcript: list) -> dict:
    """Process user feedback on a node (know/unknown).

    Returns {text, dag} with updated DAG.
    """
    node = next((n for n in dag["nodes"] if n["id"] == node_id), None)
    if not node:
        return {"text": f"Node {node_id} not found.", "dag": dag}

    # Update node status directly — no LLM call needed for simple status changes
    new_dag = {"nodes": [dict(n) for n in dag["nodes"]], "edges": list(dag["edges"])}
    for n in new_dag["nodes"]:
        if n["id"] == node_id:
            n["status"] = "known" if action == "know" else "unknown"

    status_label = "already known" if action == "know" else "needs to be learned"
    text = f"Noted: \"{node['label']}\" is {status_label}."

    return {
        "text": text,
        "dag": new_dag,
    }
