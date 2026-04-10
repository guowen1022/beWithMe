"""Knowledge graph module — concepts, edges, mastery, and graph walks.

Public API:
    parse_concepts(answer)       -> list[str]       # extract concept names from model answer
    upsert_concepts(db, user_id, names)   -> list[ConceptNode]  # create/update nodes with HLR
    link_concepts(db, user_id, names, context)  -> list[ConceptEdge]  # create temporal edges
    get_graph_context(db, user_id, concept_names) -> str  # walk graph, return prompt-ready text
    get_concepts(db, user_id)    -> list[ConceptNode]  # all concepts for debug panel
    decay_edges(db, user_id, half_life_days)     # prune weak edges
"""

from app.knowledge.concepts import (
    parse_concepts,
    upsert_concepts,
    get_concepts,
)
from app.knowledge.edges import (
    link_concepts,
    decay_edges,
)
from app.knowledge.graph import get_graph_context
from app.knowledge.hlr import compute_mastery, mastery_to_state
from app.knowledge.models import ConceptNode, ConceptEdge
from app.knowledge.visualize import get_graph_data

__all__ = [
    "parse_concepts",
    "upsert_concepts",
    "link_concepts",
    "get_graph_context",
    "get_concepts",
    "decay_edges",
    "compute_mastery",
    "mastery_to_state",
    "ConceptNode",
    "ConceptEdge",
    "get_graph_data",
]
