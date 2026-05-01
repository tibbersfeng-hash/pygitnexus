"""Query interface for the knowledge graph."""

from __future__ import annotations

from ..graph.store import GraphStore


def query(store: GraphStore, keyword: str, limit: int = 20) -> list[dict]:
    """Search for symbols matching a keyword."""
    return store.query(
        "MATCH (n) WHERE n.name CONTAINS $name "
        "RETURN labels(n) as types, n.name as name, "
        "n.filePath as filePath, n.startLine as startLine "
        "LIMIT $limit",
        {"name": keyword, "limit": limit},
    )


def symbol_context(store: GraphStore, name: str) -> dict:
    """Get full context for a symbol."""
    return store.symbol_context(name)


def run_cypher(store: GraphStore, cypher: str) -> list[dict]:
    """Execute a raw Cypher query."""
    return store.query(cypher)
