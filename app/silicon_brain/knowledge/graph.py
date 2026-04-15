"""NetworkX graph operations: load, walk, format for prompts."""
import uuid
from typing import List, Dict, Tuple
from datetime import datetime
import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.silicon_brain.knowledge.models import ConceptNode, ConceptEdge


async def load_graph(db: AsyncSession, user_id: uuid.UUID) -> nx.Graph:
    """Load the user's concept graph into NetworkX."""
    g = nx.Graph()

    node_result = await db.execute(select(ConceptNode).where(ConceptNode.user_id == user_id))
    for node in node_result.scalars().all():
        g.add_node(node.name, state=node.state, count=node.encounter_count,
                    half_life=node.half_life_hours)

    id_result = await db.execute(
        select(ConceptNode.id, ConceptNode.name).where(ConceptNode.user_id == user_id)
    )
    id_to_name = {row[0]: row[1] for row in id_result.all()}

    edge_result = await db.execute(select(ConceptEdge).where(ConceptEdge.user_id == user_id))
    for edge in edge_result.scalars().all():
        src = id_to_name.get(edge.source_id)
        tgt = id_to_name.get(edge.target_id)
        if src and tgt:
            if g.has_edge(src, tgt):
                g[src][tgt]["weight"] += edge.weight
            else:
                g.add_edge(src, tgt, weight=edge.weight, edge_type=edge.edge_type)

    return g


def walk_neighborhood(
    graph: nx.Graph,
    concept_names: List[str],
    max_depth: int = 2,
    min_weight: float = 0.3,
) -> Dict[str, List[Tuple[str, float]]]:
    """Walk from given concepts, return related concepts with weights."""
    results: Dict[str, List[Tuple[str, float]]] = {}

    for start in concept_names:
        if start not in graph:
            continue
        neighbors = []
        for target in graph.nodes():
            if target == start or target in concept_names:
                continue
            try:
                path = nx.shortest_path(graph, start, target)
                if len(path) - 1 > max_depth:
                    continue
                weight = 1.0
                for i in range(len(path) - 1):
                    weight *= graph[path[i]][path[i + 1]].get("weight", 1.0)
                if weight >= min_weight:
                    neighbors.append((target, round(weight, 2)))
            except nx.NetworkXNoPath:
                continue

        neighbors.sort(key=lambda x: -x[1])
        if neighbors:
            results[start] = neighbors[:5]

    return results


async def get_graph_context(db: AsyncSession, user_id: uuid.UUID, concept_names: List[str]) -> str:
    """Load graph, walk neighborhoods, return prompt-ready text."""
    if not concept_names:
        return ""

    graph = await load_graph(db, user_id)
    neighborhood = walk_neighborhood(graph, concept_names)

    if not neighborhood:
        return ""

    lines = []
    for concept, neighbors in neighborhood.items():
        related = [f"{name} (strength {w})" for name, w in neighbors]
        if related:
            lines.append(f"- {concept} connects to: {', '.join(related)}")

    if not lines:
        return ""

    return "CONCEPT CONNECTIONS (from the user's personal knowledge graph):\n" + "\n".join(lines)
