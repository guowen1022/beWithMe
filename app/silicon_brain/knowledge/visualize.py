"""Graph visualization data: export nodes + edges for frontend rendering."""
import uuid
from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.silicon_brain.knowledge.models import ConceptNode, ConceptEdge
from app.silicon_brain.knowledge.hlr import compute_mastery, mastery_to_state


async def get_graph_data(db: AsyncSession, user_id: uuid.UUID) -> Dict[str, Any]:
    """Return full graph as {nodes, edges} for D3/vis.js visualization."""
    now = datetime.utcnow()

    # Nodes
    node_result = await db.execute(
        select(ConceptNode).where(ConceptNode.user_id == user_id).order_by(ConceptNode.last_seen.desc())
    )
    nodes = []
    id_to_name = {}
    for n in node_result.scalars().all():
        ref_time = n.last_recalled_at or n.last_seen
        if ref_time and ref_time.tzinfo is not None:
            ref_time = ref_time.replace(tzinfo=None)
        hours_since = max(0, (now - ref_time).total_seconds() / 3600.0)
        p = compute_mastery(n.half_life_hours, hours_since)
        state = mastery_to_state(p)

        id_to_name[n.id] = n.name
        nodes.append({
            "id": n.name,
            "state": state,
            "mastery": round(p, 3),
            "encounters": n.encounter_count,
            "halfLife": round(n.half_life_hours, 1),
        })

    # Edges
    edge_result = await db.execute(select(ConceptEdge).where(ConceptEdge.user_id == user_id))
    edges = []
    for e in edge_result.scalars().all():
        src = id_to_name.get(e.source_id)
        tgt = id_to_name.get(e.target_id)
        if src and tgt:
            edges.append({
                "source": src,
                "target": tgt,
                "weight": round(e.weight, 2),
                "type": e.edge_type,
            })

    return {"nodes": nodes, "edges": edges}
