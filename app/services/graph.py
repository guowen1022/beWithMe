"""Graph operations: build NetworkX graph from DB, walk neighborhoods, create edges."""
from typing import List, Dict, Tuple, Optional
from datetime import datetime
from itertools import combinations
import networkx as nx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.knowledge.models import ConceptNode, ConceptEdge


async def create_temporal_edges(
    db: AsyncSession,
    concept_names: List[str],
    context: Optional[str] = None,
) -> List[ConceptEdge]:
    """Create temporal edges between all pairs of concepts from the same interaction."""
    if len(concept_names) < 2:
        return []

    # Fetch node IDs
    result = await db.execute(
        select(ConceptNode).where(ConceptNode.name.in_(concept_names))
    )
    nodes = {n.name: n for n in result.scalars().all()}

    edges = []
    now = datetime.utcnow()
    for a, b in combinations(concept_names, 2):
        if a not in nodes or b not in nodes:
            continue
        # Ensure consistent ordering (smaller UUID first)
        src, tgt = sorted([nodes[a], nodes[b]], key=lambda n: str(n.id))

        # Check if edge exists
        existing = await db.execute(
            select(ConceptEdge).where(
                ConceptEdge.source_id == src.id,
                ConceptEdge.target_id == tgt.id,
                ConceptEdge.edge_type == "temporal",
            )
        )
        edge = existing.scalar_one_or_none()

        if edge:
            edge.weight = min(edge.weight + 0.5, 10.0)
            edge.last_reinforced = now
        else:
            edge = ConceptEdge(
                source_id=src.id,
                target_id=tgt.id,
                edge_type="temporal",
                weight=1.0,
                context=context,
            )
            db.add(edge)
        edges.append(edge)

    await db.commit()
    return edges


async def load_graph(db: AsyncSession) -> nx.Graph:
    """Load the concept graph from DB into NetworkX."""
    g = nx.Graph()

    # Load nodes
    node_result = await db.execute(select(ConceptNode))
    for node in node_result.scalars().all():
        g.add_node(node.name, state=node.state, count=node.encounter_count,
                    half_life=node.half_life_hours, node_id=str(node.id))

    # Load edges
    edge_result = await db.execute(select(ConceptEdge))
    # We need node names for edges, build id->name map
    id_result = await db.execute(select(ConceptNode.id, ConceptNode.name))
    id_to_name = {row[0]: row[1] for row in id_result.all()}

    for edge in edge_result.scalars().all():
        src_name = id_to_name.get(edge.source_id)
        tgt_name = id_to_name.get(edge.target_id)
        if src_name and tgt_name:
            if g.has_edge(src_name, tgt_name):
                # Accumulate weight from multiple edge types
                g[src_name][tgt_name]["weight"] += edge.weight
            else:
                g.add_edge(src_name, tgt_name, weight=edge.weight,
                           edge_type=edge.edge_type)

    return g


def walk_neighborhood(
    graph: nx.Graph,
    concept_names: List[str],
    max_depth: int = 2,
    min_weight: float = 0.3,
) -> Dict[str, List[Tuple[str, float, List[str]]]]:
    """Walk the graph from given concepts. Returns related concepts with paths.

    Returns: {concept_name: [(neighbor, weight, path), ...]}
    """
    results: Dict[str, List[Tuple[str, float, List[str]]]] = {}

    for start in concept_names:
        if start not in graph:
            continue
        neighbors = []
        # BFS with depth limit
        for target in graph.nodes():
            if target == start or target in concept_names:
                continue
            try:
                path = nx.shortest_path(graph, start, target)
                if len(path) - 1 > max_depth:
                    continue
                # Calculate path weight (product of edge weights)
                weight = 1.0
                for i in range(len(path) - 1):
                    edge_data = graph[path[i]][path[i + 1]]
                    weight *= edge_data.get("weight", 1.0)
                if weight >= min_weight:
                    neighbors.append((target, round(weight, 2), path))
            except nx.NetworkXNoPath:
                continue

        neighbors.sort(key=lambda x: -x[1])
        if neighbors:
            results[start] = neighbors[:5]

    return results


def format_graph_context(
    neighborhood: Dict[str, List[Tuple[str, float, List[str]]]],
    graph: nx.Graph,
) -> str:
    """Format graph walk results for injection into the prompt."""
    if not neighborhood:
        return ""

    lines = []
    for concept, neighbors in neighborhood.items():
        related = [f"{n[0]} (strength {n[1]})" for n in neighbors]
        if related:
            lines.append(f"- {concept} connects to: {', '.join(related)}")

    if not lines:
        return ""

    return "CONCEPT CONNECTIONS (from the user's personal knowledge graph):\n" + "\n".join(lines)
