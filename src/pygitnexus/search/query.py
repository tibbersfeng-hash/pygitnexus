"""Query interface for the knowledge graph."""

from __future__ import annotations

import re
from ..graph.store import GraphStore

# Patterns that indicate a Java getter/setter method
_GETTER_SETTER_RE = re.compile(
    r"^(get|set|is)[A-Z]"
    r"|^(equals|hashCode|toString|clone|finalize|getClass|notify|notifyAll|wait)$"
)


def _is_getter_or_setter(name: str) -> bool:
    """Check if a method name looks like a trivial getter/setter."""
    return bool(_GETTER_SETTER_RE.match(name))


def _filter_getter_setters(rows: list[dict]) -> list[dict]:
    """Remove getter/setter methods from query results."""
    filtered = []
    for row in rows:
        types_raw = row.get("types", "")
        # types can be a dict like {'Method': 'Method'} or a string
        is_method = False
        if isinstance(types_raw, dict):
            is_method = "Method" in types_raw
        elif isinstance(types_raw, str):
            is_method = "Method" in types_raw
        if is_method and _is_getter_or_setter(row.get("name", "")):
            continue
        filtered.append(row)
    return filtered


def query(store: GraphStore, keyword: str, limit: int = 20) -> list[dict]:
    """Search for symbols matching a keyword.

    Supports compound names like 'ClassName.methodName' — splits on '.'
    and matches className and name separately.

    Getter/setter methods (getXxx, setXxx, isXxx) are filtered out.
    """
    # Check if keyword looks like a compound name (e.g., "MallUser.setLoginName")
    if "." in keyword:
        parts = keyword.rsplit(".", 1)
        class_part = parts[0]
        name_part = parts[1]
        rows = store.query(
            "MATCH (n) WHERE n.name CONTAINS $name AND n.className CONTAINS $cls "
            "RETURN labels(n) as types, n.name as name, n.className as className, "
            "n.filePath as filePath, n.startLine as startLine "
            "LIMIT $limit",
            {"name": name_part, "cls": class_part, "limit": limit},
        )
    else:
        rows = store.query(
            "MATCH (n) WHERE n.name CONTAINS $name "
            "RETURN labels(n) as types, n.name as name, n.className as className, "
            "n.filePath as filePath, n.startLine as startLine "
            "LIMIT $limit",
            {"name": keyword, "limit": limit},
        )
    return _filter_getter_setters(rows)


def symbol_context(store: GraphStore, name: str, max_depth: int = 30) -> dict:
    """Get full context for a symbol with BFS expansion up to max_depth hops."""
    return store.symbol_context(name, max_depth)


def run_cypher(store: GraphStore, cypher: str) -> list[dict]:
    """Execute a raw Cypher query."""
    return store.query(cypher)
