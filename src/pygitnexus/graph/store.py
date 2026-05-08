"""KuzuDB store: schema creation, node/edge insertion, batched writes, and queries."""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from pathlib import Path

import kuzu

from . import schema


def _infer_table_from_mapper(mapper_class: str, method_name: str) -> str | None:
    """Infer table name from Mapper class name."""
    entity = mapper_class.replace("Mapper", "").replace("DAO", "").replace("Repository", "")
    prefixes = ["tb_", "t_", "newbee_mall_"]
    for p in prefixes:
        if p in entity.lower():
            return f"{p}{entity.lower().replace(p, '')}"
    snake = re.sub(r'([A-Z])', r'_\1', entity).lower().lstrip("_")
    return f"tb_{snake}" if snake else None

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
            # Use forward slashes to avoid Windows backslash escape issues in Cypher
            safe_path = csv_path.replace("\\", "/")
            self._execute(
                f"COPY {table} FROM '{safe_path}' (header=true)"
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
            # Use forward slashes to avoid Windows backslash escape issues in Cypher
            safe_node = node_csv.replace("\\", "/")
            self._execute(f"COPY {table} FROM '{safe_node}' (header=true, parallel=false)")
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
            safe_rel = rel_csv.replace("\\", "/")
            self._execute(
                f"COPY CodeRelation FROM '{safe_rel}' "
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
            safe_path = csv_path.replace("\\", "/")
            self._execute(
                f"COPY CodeRelation FROM '{safe_path}' "
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

    def symbol_context(self, name: str, max_depth: int = 30) -> dict:
        """Get full context for a symbol: callers, callees, imports, accesses.

        Uses BFS expansion up to `max_depth` hops for callers and callees.
        """
        # ─── Callers (BFS upstream) ─────────────────────────────────────

        # First: find target method's full key
        target_rows = self.query(
            "MATCH (n:Method) WHERE n.name = $name "
            "RETURN n.className AS className, n.name AS methodName LIMIT 5",
            {"name": name},
        )
        target_key = None
        if target_rows:
            row = target_rows[0]
            target_key = f"{row['className']}.{row['methodName']}"

        callers_flat: list[dict] = []
        if target_key:
            # Level 1: direct CALLS callers
            callers_level1 = self.query(
                "MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(target) "
                "WHERE target.name = $name "
                "RETURN caller.name as caller, caller.className as callerClass, "
                "r.confidence as confidence",
                {"name": name},
            )
            # Include frontend pages that USES_ENDPOINT → API ← EXPOSES ← this backend method
            frontend_callers = self.query(
                "MATCH (caller:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
                "<-[r2:CodeRelation {type: 'EXPOSES'}]-(target:Method) "
                "WHERE target.name = $name "
                "RETURN caller.name as caller, caller.className as callerClass, "
                "r1.confidence as confidence",
                {"name": name},
            )
            # Three-hop: USES_ENDPOINT → API ← EXPOSES ← Controller → CALLS → Interface
            ep_two_hop = self.query(
                "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
                "<-[r2:CodeRelation {type: 'EXPOSES'}]-(ctrl:Method)-[r3:CodeRelation {type: 'CALLS'}]->(iface:Method) "
                "WHERE iface.name = $name "
                "RETURN page.name as caller, page.className as callerClass, "
                "       ctrl.name as ctrlName, ctrl.className as ctrlClass, "
                "       r1.confidence as confidence",
                {"name": name},
            )

            # Build caller tree with BFS
            seen_callers: set[str] = set()
            caller_lookup: dict[str, dict] = {}

            # Attach two-hop USES_ENDPOINT: Controller as parent, page as child
            page_map: dict[str, dict] = {}
            for fc in ep_two_hop:
                ctrl_key = f"{fc['ctrlClass']}.{fc['ctrlName']}"
                if ctrl_key not in page_map:
                    page_map[ctrl_key] = {
                        "caller": fc["ctrlName"],
                        "callerClass": fc.get("ctrlClass", ""),
                        "confidence": fc.get("confidence"),
                        "relType": "CALLS (via Interface)",
                        "children": [],
                    }
                    seen_callers.add(ctrl_key)
                    caller_lookup[ctrl_key] = page_map[ctrl_key]
                if not any(ch.get("caller") == fc["caller"] for ch in page_map[ctrl_key]["children"]):
                    page_node = {
                        "caller": fc["caller"],
                        "callerClass": fc.get("pageClass", ""),
                        "confidence": None,
                        "relType": "USES_ENDPOINT",
                        "children": [],
                    }
                    page_map[ctrl_key]["children"].append(page_node)
                    page_key = f"{fc.get('pageClass', '')}.{fc['caller']}"
                    if page_key not in seen_callers:
                        seen_callers.add(page_key)
                        caller_lookup[page_key] = page_node

            # Attach direct USES_ENDPOINT callers
            for fc in frontend_callers:
                page_key = f"{fc.get('callerClass', '')}.{fc['caller']}"
                already_child = any(
                    any(ch.get("caller") == fc["caller"] for ch in pm.get("children", []))
                    for pm in page_map.values()
                )
                if not already_child and page_key not in seen_callers:
                    seen_callers.add(page_key)
                    node = {
                        "caller": fc["caller"],
                        "callerClass": fc.get("callerClass", ""),
                        "confidence": fc.get("confidence"),
                        "relType": "USES_ENDPOINT",
                        "children": [],
                    }
                    page_map[page_key] = node
                    caller_lookup[page_key] = node

            # Add pure Java CALLS callers
            for c in callers_level1:
                key = f"{c['callerClass']}.{c['caller']}" if c.get('callerClass') else c['caller']
                already_in_page = any(
                    any(ch.get("callerClass") == c.get("callerClass") and ch.get("caller") == c["caller"]
                        for ch in pm.get("children", []))
                    for pm in page_map.values()
                )
                if not already_in_page and key not in seen_callers:
                    seen_callers.add(key)
                    node = {
                        "caller": c["caller"],
                        "callerClass": c.get("callerClass", ""),
                        "confidence": c.get("confidence"),
                        "children": [],
                    }
                    page_map[key] = node
                    caller_lookup[key] = node

            # BFS expansion for callers (additional hops beyond level 1)
            current_level = list(seen_callers)
            for _depth in range(2, max_depth + 1):
                if not current_level:
                    break
                # Build OR conditions for batch query
                conditions_list = []
                for key in current_level:
                    if "." in key:
                        cls_part = key.rsplit(".", 1)[0]
                        name_part = key.rsplit(".", 1)[1]
                        conditions_list.append(
                            f"(target.className = {json.dumps(cls_part)} AND target.name = {json.dumps(name_part)})"
                        )
                    else:
                        conditions_list.append(f"target.name = {json.dumps(key)}")
                if not conditions_list:
                    break
                conditions = " OR ".join(conditions_list)
                rows = self.query(
                    f"MATCH (caller:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(target:Method) "
                    f"WHERE {conditions} "
                    f"RETURN caller.className AS callerClass, caller.name AS callerName, "
                    f"       target.className AS tgtClass, target.name AS tgtName, "
                    f"       r.confidence AS confidence"
                )
                next_level: list[str] = []
                for r in rows:
                    tgt_key = f"{r['tgtClass']}.{r['tgtName']}"
                    caller_key = f"{r['callerClass']}.{r['callerName']}"
                    if tgt_key in seen_callers and caller_key not in seen_callers:
                        seen_callers.add(caller_key)
                        node = {
                            "caller": r["callerName"],
                            "callerClass": r.get("callerClass", ""),
                            "confidence": r.get("confidence"),
                            "children": [],
                        }
                        if tgt_key in caller_lookup:
                            caller_lookup[tgt_key]["children"].append(node)
                        caller_lookup[caller_key] = node
                        next_level.append(caller_key)
                current_level = next_level

            callers_flat = list(page_map.values())

        # ─── Callees (BFS downstream) ───────────────────────────────────

        callees_level1 = self.query(
            "MATCH (source)-[r:CodeRelation {type: 'CALLS'}]->(callee) "
            "WHERE source.name = $name "
            "RETURN callee.name as callee, callee.className as calleeClass, "
            "r.confidence as confidence",
            {"name": name},
        )

        callee_map: dict[str, dict] = {}
        seen_callees: set[str] = set()
        callee_lookup: dict[str, dict] = {}
        for c in callees_level1:
            key = f"{c['calleeClass']}.{c['callee']}" if c.get('calleeClass') else c['callee']
            if key not in seen_callees:
                seen_callees.add(key)
                node = {
                    "callee": c["callee"],
                    "calleeClass": c.get("calleeClass", ""),
                    "confidence": c.get("confidence"),
                    "children": [],
                }
                callee_map[key] = node
                callee_lookup[key] = node

        # BFS expansion for callees
        current_level = list(seen_callees)
        for _depth in range(2, max_depth + 1):
            if not current_level:
                break
            conditions_list = []
            for key in current_level:
                if "." in key:
                    cls_part = key.rsplit(".", 1)[0]
                    name_part = key.rsplit(".", 1)[1]
                    conditions_list.append(
                        f"(source.className = {json.dumps(cls_part)} AND source.name = {json.dumps(name_part)})"
                    )
                else:
                    conditions_list.append(f"source.name = {json.dumps(key)}")
            if not conditions_list:
                break
            conditions = " OR ".join(conditions_list)
            rows = self.query(
                f"MATCH (source:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(child:Method) "
                f"WHERE {conditions} "
                f"RETURN source.className AS srcClass, source.name AS srcName, "
                f"       child.name AS childName, child.className AS childClass, "
                f"       r.confidence AS confidence"
            )
            next_level: list[str] = []
            for r in rows:
                parent_key = f"{r['srcClass']}.{r['srcName']}"
                child_key = f"{r['childClass']}.{r['childName']}"
                if parent_key in callee_lookup and child_key not in seen_callees:
                    seen_callees.add(child_key)
                    node = {
                        "callee": r["childName"],
                        "calleeClass": r.get("childClass", ""),
                        "confidence": r.get("confidence"),
                        "children": [],
                    }
                    callee_lookup[parent_key]["children"].append(node)
                    callee_lookup[child_key] = node
                    next_level.append(child_key)
            current_level = next_level

        # Infer tables for Mapper callees
        for key, node in callee_map.items():
            if "Mapper" in key or "DAO" in key:
                parts = key.rsplit(".", 1)
                if len(parts) == 2:
                    table_name = _infer_table_from_mapper(parts[0], parts[1])
                    if table_name:
                        node["children"].append({
                            "callee": table_name,
                            "calleeClass": "",
                            "confidence": None,
                            "relType": "SQL (MyBatis)",
                            "children": [],
                        })

        callees_flat = list(callee_map.values())

        # ─── Imports ─────────────────────────────────────────────────────

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

        # ─── ACCESSES (for Fields) ───────────────────────────────────────

        field_check = self.query("MATCH (n:Field) WHERE n.name = $name RETURN n.id as id", {"name": name})
        accesses = []
        if field_check:
            accesses = self.query(
                "MATCH (source)-[r:CodeRelation {type: 'ACCESSES'}]->(target:Field) "
                "WHERE target.name = $name "
                "RETURN source.name as accessor, source.className as accessorClass, "
                "r.confidence as confidence",
                {"name": name},
            )

        return {
            "callers": callers_flat,
            "callees": callees_flat,
            "imports": imports,
            "accesses": accesses,
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
