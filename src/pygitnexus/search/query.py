"""Query interface for the knowledge graph."""

from __future__ import annotations

from ..graph.store import GraphStore


def query(store: GraphStore, keyword: str, limit: int = 20) -> list[dict]:
    """Search for symbols matching a keyword.

    Supports compound names like 'ClassName.methodName' — splits on '.'
    and matches className and name separately.
    """
    # Check if keyword looks like a compound name (e.g., "MallUser.setLoginName")
    if "." in keyword:
        parts = keyword.rsplit(".", 1)
        class_part = parts[0]
        name_part = parts[1]
        return store.query(
            "MATCH (n) WHERE n.name CONTAINS $name AND n.className CONTAINS $cls "
            "RETURN labels(n) as types, n.name as name, n.className as className, "
            "n.filePath as filePath, n.startLine as startLine "
            "LIMIT $limit",
            {"name": name_part, "cls": class_part, "limit": limit},
        )
    return store.query(
        "MATCH (n) WHERE n.name CONTAINS $name "
        "RETURN labels(n) as types, n.name as name, n.className as className, "
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
