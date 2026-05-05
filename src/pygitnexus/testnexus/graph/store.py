"""KuzuDB store for TestNexus."""

from __future__ import annotations

from pathlib import Path

import kuzu

from . import schema


class GraphStore:
    """Wraps a KuzuDB database for TestGraph operations."""

    def __init__(self, db_path: str | Path) -> None:
        self._db = kuzu.Database(str(db_path))
        self._conn = kuzu.Connection(self._db)

    def init_schema(self) -> None:
        """Create all node and relation tables."""
        for q in schema.ALL_SCHEMA_QUERIES:
            self._execute(q)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def insert_node(self, table: str, props: dict) -> None:
        """Insert a single node using Cypher CREATE."""
        props_str = ", ".join(f"{k}: ${k}" for k in props)
        cypher = f"CREATE (n:{table} {{{props_str}}})"
        self._execute(cypher, props)

    def insert_relation(
        self,
        from_table: str,
        to_table: str,
        from_id: str,
        to_id: str,
        rel_type: str,
        confidence: float = 1.0,
        reason: str = "",
    ) -> None:
        """Insert a relation edge using MATCH + CREATE."""
        cypher = (
            f"MATCH (a:{from_table} {{id: $from_id}}), (b:{to_table} {{id: $to_id}}) "
            f"CREATE (a)-[r:TestRelation {{type: $rel_type, confidence: $confidence, reason: $reason}}]->(b)"
        )
        self._execute(cypher, {
            "from_id": from_id,
            "to_id": to_id,
            "rel_type": rel_type,
            "confidence": confidence,
            "reason": reason,
        })

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a Cypher query and return results as list of dicts."""
        result = self._execute(cypher, params or {})
        return _fetch_results(result)

    def execute(self, cypher: str, params: dict | None = None) -> None:
        """Execute a Cypher statement (INSERT, UPDATE, DELETE) without returning results."""
        self._execute(cypher, params or {})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _execute(self, cypher: str, params: dict | None = None) -> kuzu.QueryResult:
        if params:
            return self._conn.execute(cypher, params)
        return self._conn.execute(cypher)


def _fetch_results(result: kuzu.QueryResult) -> list[dict]:
    """Convert a Kuzu QueryResult to a list of dicts."""
    cols = result.get_column_names()
    rows: list[dict] = []
    while result.has_next():
        row = result.get_next()
        rows.append(dict(zip(cols, row)))
    return rows
