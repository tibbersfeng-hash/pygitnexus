"""KuzuDB store: schema creation, node/edge insertion, batched writes, and queries."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

import kuzu

from . import schema

# String values that need quoting in Cypher
_STRING_TYPES = {"STRING"}


class GraphStore:
    """Wraps a KuzuDB database for graph operations."""

    def __init__(self, db_path: str | Path) -> None:
        self._db = kuzu.Database(str(db_path))
        self._conn = kuzu.Connection(self._db)

    def get_connection(self) -> kuzu.Connection:
        """Return the underlying KuzuDB connection."""
        return self._conn

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """Create all node and relation tables."""
        for q in schema.ALL_SCHEMA_QUERIES:
            self._execute(q)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Write operations (single)
    # ------------------------------------------------------------------

    def insert_node(self, table: str, props: dict) -> None:
        """Insert a single node into a table using CREATE."""
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
        confidence: float = 0.9,
        reason: str = "",
    ) -> None:
        """Insert a CodeRelation edge using MATCH + CREATE."""
        cypher = (
            f"MATCH (a:{from_table} {{id: $from_id}}), (b:{to_table} {{id: $to_id}}) "
            f"CREATE (a)-[r:CodeRelation {{type: $rel_type, confidence: $confidence, reason: $reason}}]->(b)"
        )
        self._execute(cypher, {
            "from_id": from_id,
            "to_id": to_id,
            "rel_type": rel_type,
            "confidence": confidence,
            "reason": reason,
        })

    # ------------------------------------------------------------------
    # Batch write operations (UNWIND-based)
    # ------------------------------------------------------------------

    def bulk_unwind_insert(self, table: str, rows: list[dict]) -> None:
        """Insert multiple nodes using UNWIND for batch performance."""
        if not rows:
            return
        props_str = ", ".join(f"{k}: row.{k}" for k in rows[0].keys())
        cypher = f"UNWIND $rows AS row CREATE (n:{table} {{{props_str}}})"
        self._execute(cypher, {"rows": rows})

    def bulk_copy_nodes(
        self,
        table: str,
        rows: list[dict],
    ) -> None:
        """Insert multiple nodes using COPY FROM CSV for maximum throughput."""
        if not rows:
            return
        csv_path = os.path.join(tempfile.gettempdir(), f"kuzu_nodes_{os.getpid()}.csv")
        try:
            headers = list(rows[0].keys())
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for row in rows:
                    writer.writerow([row.get(h, "") for h in headers])
            self._execute(
                f"COPY {table} FROM '{csv_path}' (header=true)"
            )
        finally:
            os.remove(csv_path)

    def bulk_copy_nodes_with_defines(
        self,
        table: str,
        rows: list[dict],
        rel_type: str,
        from_table: str = "File",
    ) -> None:
        """Insert nodes and their DEFINES edges using two CSV COPY passes.

        Much faster than UNWIND+MATCH for large batches because:
        1. COPY FROM is optimized for bulk loads
        2. Relation CSV COPY avoids per-row MATCH lookups
        """
        if not rows:
            return

        # Pass 1: Insert nodes (all props except from_id)
        node_headers = [k for k in rows[0].keys() if k != "from_id"]
        node_csv = os.path.join(tempfile.gettempdir(), f"kuzu_nodes_{os.getpid()}.csv")
        try:
            with open(node_csv, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(node_headers)
                for row in rows:
                    writer.writerow([row.get(h, "") for h in node_headers])
            self._execute(f"COPY {table} FROM '{node_csv}' (header=true, parallel=false)")
        finally:
            os.remove(node_csv)

        # Pass 2: Insert relations via CSV COPY
        rel_csv = os.path.join(tempfile.gettempdir(), f"kuzu_defines_{os.getpid()}.csv")
        try:
            with open(rel_csv, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["from_id", "to_id", "type", "confidence", "reason",
                                 "httpMethod", "httpPath", "httpParams"])
                reason = f"file defines {table.lower()}"
                for row in rows:
                    writer.writerow([row["from_id"], row["id"], rel_type, "1.0", reason,
                                     "", "", ""])
            self._execute(
                f"COPY CodeRelation FROM '{rel_csv}' "
                f"(header=true, from='{from_table}', to='{table}')"
            )
        finally:
            os.remove(rel_csv)

    def bulk_unwind_insert_with_defines(
        self,
        table: str,
        rows: list[dict],
        rel_type: str,
    ) -> None:
        """Insert nodes and their DEFINES edge from File in one UNWIND.

        Fast for small batches. For large batches, use bulk_copy_nodes_with_defines.
        """
        if not rows:
            return
        node_keys = [k for k in rows[0].keys() if k != "from_id"]
        node_props = ", ".join(f"{k}: row.{k}" for k in node_keys)
        cypher = (
            f"UNWIND $rows AS row "
            f"CREATE (n:{table} {{{node_props}}}) "
            f"WITH row, n "
            f"MATCH (f:File {{id: row.from_id}}) "
            f"CREATE (f)-[r:CodeRelation {{type: $rel_type, confidence: 1.0, reason: $reason}}]->(n)"
        )
        self._execute(cypher, {
            "rows": rows,
            "rel_type": rel_type,
            "reason": f"file defines {table.lower()}",
        })

    def bulk_unwind_relation(
        self,
        from_table: str,
        to_table: str,
        relations: list[dict],
        rel_type: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Insert multiple relations using UNWIND."""
        if not relations:
            return
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{from_table} {{id: row.from_id}}), (b:{to_table} {{id: row.to_id}}) "
            f"CREATE (a)-[r:CodeRelation {{type: $rel_type, confidence: $confidence, reason: $reason}}]->(b)"
        )
        self._execute(cypher, {
            "rows": relations,
            "rel_type": rel_type,
            "confidence": confidence,
            "reason": reason,
        })

    def bulk_unwind_relation_with_confidence(
        self,
        from_table: str,
        to_table: str,
        relations: list[dict],
        rel_type: str,
        reason: str,
    ) -> None:
        """Insert multiple relations using per-row confidence values."""
        if not relations:
            return
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{from_table} {{id: row.from_id}}), (b:{to_table} {{id: row.to_id}}) "
            f"CREATE (a)-[r:CodeRelation {{type: $rel_type, confidence: row.confidence, reason: $reason}}]->(b)"
        )
        self._execute(cypher, {
            "rows": relations,
            "rel_type": rel_type,
            "reason": reason,
        })

    def bulk_copy_relations(
        self,
        from_table: str,
        to_table: str,
        relations: list[dict],
        rel_type: str,
        reason: str,
        extra_columns: list[str] | None = None,
    ) -> None:
        """Insert relations using COPY FROM CSV for maximum throughput.

        Each relation dict must have 'from_id', 'to_id', and 'confidence' keys.
        Uses a temporary CSV file and Kuzu's COPY command with explicit FROM/TO.

        Args:
            extra_columns: Optional list of additional column names to include.
        """
        if not relations:
            return
        csv_path = os.path.join(tempfile.gettempdir(), f"kuzu_rel_{os.getpid()}.csv")
        cols = ["from_id", "to_id", "type", "confidence", "reason"]
        if extra_columns:
            cols.extend(extra_columns)
        try:
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                for r in relations:
                    row = [r["from_id"], r["to_id"], rel_type, r["confidence"], reason]
                    if extra_columns:
                        for col in extra_columns:
                            row.append(r.get(col, "") or "")
                    writer.writerow(row)
            self._execute(
                f"COPY CodeRelation FROM '{csv_path}' "
                f"(header=true, from='{from_table}', to='{to_table}')"
            )
        finally:
            os.remove(csv_path)

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a Cypher query and return results as list of dicts."""
        result = self._execute(cypher, params or {})
        return _fetch_results(result)

    def search_symbol(self, name: str) -> list[dict]:
        """Search for symbols matching a name across all node tables."""
        cypher = (
            f"MATCH (n) WHERE n.name CONTAINS $name "
            f"RETURN labels(n) as type, n.name as name, n.filePath as filePath, "
            f"n.startLine as startLine, n.endLine as endLine"
        )
        return self.query(cypher, {"name": name})

    def symbol_context(self, name: str) -> dict:
        """Get full context for a symbol: callers, callees, relations."""
        callers = self.query(
            "MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(target) "
            "WHERE target.name = $name "
            "RETURN caller.name as caller, caller.className as callerClass, "
            "r.confidence as confidence",
            {"name": name},
        )
        callees = self.query(
            "MATCH (source)-[r:CodeRelation {type: 'CALLS'}]->(callee) "
            "WHERE source.name = $name "
            "RETURN callee.name as callee, callee.className as calleeClass, "
            "r.confidence as confidence",
            {"name": name},
        )
        file_paths = self.query(
            "MATCH (n) WHERE n.name = $name RETURN n.filePath as filePath",
            {"name": name},
        )
        imports = []
        if file_paths:
            fps = [fp["filePath"] for fp in file_paths if fp.get("filePath")]
            if fps:
                imports = self.query(
                    "MATCH (f)-[r:CodeRelation {type: 'IMPORTS'}]->(target) "
                    "WHERE f.filePath IN $fps "
                    "RETURN target.name as target, r.reason as reason",
                    {"fps": fps},
                )
        return {
            "callers": callers,
            "callees": callees,
            "imports": imports,
        }

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
