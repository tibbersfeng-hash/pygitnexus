"""Query interface for the knowledge graph."""

from __future__ import annotations

from ..graph.store import GraphStore


def query(store: GraphStore, keyword: str, limit: int = 20) -> list[dict]:
    """Search for symbols matching a keyword.

    Supports compound names like 'ClassName.methodName' — splits on '.'
    and matches className and name separately.
    Returns id field for structural lookups (e.g., accessor detection).
    """
    columns = (
        "n.id as id, labels(n) as types, n.name as name, n.className as className, "
        "n.filePath as filePath, n.startLine as startLine"
    )
    # Check if keyword looks like a compound name (e.g., "MallUser.setLoginName")
    if "." in keyword:
        parts = keyword.rsplit(".", 1)
        class_part = parts[0]
        name_part = parts[1]
        return store.query(
            f"MATCH (n) WHERE n.name CONTAINS $name AND n.className CONTAINS $cls "
            f"RETURN {columns} "
            "LIMIT $limit",
            {"name": name_part, "cls": class_part, "limit": limit},
        )
    return store.query(
        f"MATCH (n) WHERE n.name CONTAINS $name "
        f"RETURN {columns} "
        "LIMIT $limit",
        {"name": keyword, "limit": limit},
    )


def symbol_context(store: GraphStore, name: str, max_depth: int = 30) -> dict:
    """Get full context for a symbol with BFS expansion up to max_depth hops."""
    return store.symbol_context(name, max_depth)


def run_cypher(store: GraphStore, cypher: str) -> list[dict]:
    """Execute a raw Cypher query."""
    return store.query(cypher)
