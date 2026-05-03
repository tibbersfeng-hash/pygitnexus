"""Cross-repo impact analysis.

Two-phase approach (matching GitNexus):
Phase 1: Local impact BFS within the source repo's KuzuDB
Phase 2: Bridge DB fan-out to find cross-repo consumers
"""

from __future__ import annotations

import os
from pathlib import Path

from ...graph.store import GraphStore
from .bridge_db import query_bridge
from .storage import get_group_dir, load_group_config

# Cypher queries for bridge lookups
CY_NEIGHBORS_UPSTREAM = """
    MATCH (consumer:Contract)-[l:ContractLink]->(provider:Contract)
    WHERE provider.repo = $localRepo
      AND provider.symbolUid IN $uids
      AND provider.role = 'provider'
    RETURN consumer.repo AS neighborRepo,
           consumer.symbolUid AS neighborUid,
           consumer.filePath AS neighborFilePath,
           consumer.symbolName AS neighborName,
           l.matchType AS matchType,
           l.confidence AS confidence,
           l.contractId AS contractId
"""

CY_NEIGHBORS_DOWNSTREAM = """
    MATCH (consumer:Contract)-[l:ContractLink]->(provider:Contract)
    WHERE consumer.repo = $localRepo
      AND consumer.symbolUid IN $uids
      AND consumer.role = 'consumer'
    RETURN provider.repo AS neighborRepo,
           provider.symbolUid AS neighborUid,
           provider.filePath AS neighborFilePath,
           provider.symbolName AS neighborName,
           l.matchType AS matchType,
           l.confidence AS confidence,
           l.contractId AS contractId
"""


def _collect_impact_uids(
    store: GraphStore,
    sym_id: str,
    sym_type: str,
    direction: str,
    max_depth: int,
    relation_types: list[str],
    min_confidence: float,
    include_tests: bool = False,
) -> dict:
    """Run local impact BFS and collect all affected symbol UIDs."""
    visited: set[str] = {sym_id}
    frontier = [sym_id]
    impacted: list[dict] = []

    for depth in range(1, max_depth + 1):
        if not frontier:
            break

        id_list = ", ".join(f"'{fid.replace(chr(39), chr(39)+chr(39))}'" for fid in frontier)
        rel_list = ", ".join(f"'{t}'" for t in relation_types)
        conf_clause = f" AND r.confidence >= {min_confidence}" if min_confidence > 0 else ""

        if direction == "upstream":
            cypher = (
                f"MATCH (caller)-[r:CodeRelation]->(n) "
                f"WHERE n.id IN [{id_list}] AND r.type IN [{rel_list}]{conf_clause} "
                f"RETURN n.id AS sourceId, caller.id AS id, caller.name AS name, "
                f"labels(caller) AS nodeType, caller.filePath AS filePath, "
                f"r.type AS relType, r.confidence AS confidence"
            )
        else:
            cypher = (
                f"MATCH (n)-[r:CodeRelation]->(callee) "
                f"WHERE n.id IN [{id_list}] AND r.type IN [{rel_list}]{conf_clause} "
                f"RETURN n.id AS sourceId, callee.id AS id, callee.name AS name, "
                f"labels(callee) AS nodeType, callee.filePath AS filePath, "
                f"r.type AS relType, r.confidence AS confidence"
            )

        try:
            related = store.query(cypher)
        except Exception:
            break

        next_frontier = []
        for rel in related:
            rel_id = rel.get("id", "")
            file_path = rel.get("filePath", "")
            if not include_tests and file_path and ("test" in file_path.lower() or "Test" in file_path):
                continue
            if rel_id and rel_id not in visited:
                visited.add(rel_id)
                next_frontier.append(rel_id)
                confidence = rel.get("confidence", 0) or 0
                node_type = rel.get("nodeType", "")
                if isinstance(node_type, dict) and node_type:
                    node_type = next(iter(node_type.values()), "")
                impacted.append({
                    "depth": depth,
                    "id": rel_id,
                    "name": rel.get("name", ""),
                    "type": node_type,
                    "filePath": file_path,
                    "relationType": rel.get("relType", ""),
                    "confidence": round(confidence, 2),
                })
        frontier = next_frontier

    # Group by depth
    grouped: dict[str, list[dict]] = {}
    for item in impacted:
        key = f"d={item['depth']}"
        grouped.setdefault(key, []).append(item)

    direct = len(grouped.get("d=1", []))
    if direct == 0:
        risk = "LOW"
    elif direct <= 5:
        risk = "MEDIUM"
    elif direct <= 15:
        risk = "HIGH"
    else:
        risk = "CRITICAL"

    return {
        "target": {"id": sym_id, "type": sym_type},
        "direction": direction,
        "risk": risk,
        "summary": {
            "total_affected": len(impacted),
            "direct_dependents": direct,
            "max_depth_reached": max(g["depth"] for g in impacted) if impacted else 0,
        },
        "byDepth": {k: grouped[k] for k in sorted(grouped)},
        "all_uids": list(visited),
    }


def group_impact(
    group_name: str,
    repo_path: str,  # group path like "app/backend"
    target: str,
    db_path: str | Path,
    direction: str = "upstream",
    max_depth: int = 3,
    min_confidence: float = 0,
    include_tests: bool = False,
    relation_types: list[str] | None = None,
) -> dict:
    """Cross-repo impact analysis.

    Args:
        group_name: name of the group
        repo_path: group path (e.g. "app/backend")
        target: symbol name to analyze
        db_path: path to the repo's KuzuDB
        direction: upstream or downstream
        max_depth: max BFS depth
        min_confidence: minimum confidence
        include_tests: include test files
        relation_types: filter relation types

    Returns:
        Impact result dict with local + cross-repo sections.
    """
    group_dir = get_group_dir(group_name)
    try:
        config = load_group_config(group_dir)
    except Exception as e:
        return {"error": str(e)}

    rel_types = relation_types or ["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"]

    # Phase 1: Local impact
    store = GraphStore(Path(db_path))
    try:
        # Resolve target symbol
        rows = store.query(
            "MATCH (n) WHERE n.name = $name RETURN n.id AS id, n.name AS name, "
            "labels(n) AS nodeType, n.filePath AS filePath",
            {"name": target},
        )
        if not rows:
            store.close()
            return {"error": f"Target '{target}' not found in local DB."}
        if len(rows) > 1:
            # Try to disambiguate
            candidates = []
            for r in rows:
                nt = r.get("nodeType", {})
                type_str = next(iter(nt.values()), "") if isinstance(nt, dict) else ""
                candidates.append(f"  - {r.get('name', '')} ({type_str}) at {r.get('filePath', '')}")
            store.close()
            return {
                "error": f"Ambiguous: Found {len(rows)} symbols matching '{target}'.\n"
                + "\n".join(candidates)
            }

        sym = rows[0]
        sym_id = sym.get("id", "")
        nt = sym.get("nodeType", {})
        sym_type = next(iter(nt.values()), "") if isinstance(nt, dict) else ""

        local = _collect_impact_uids(
            store, sym_id, sym_type, direction, max_depth, rel_types,
            min_confidence, include_tests,
        )
    finally:
        store.close()

    # Phase 2: Bridge DB fan-out
    cross = []
    out_of_scope = []

    uids = local.get("all_uids", [])
    if not uids:
        return {
            "local": local,
            "group": group_name,
            "cross": [],
            "outOfScope": [],
            "summary": {
                "direct": local["summary"]["direct_dependents"],
                "processes_affected": 0,
                "cross_repo_hits": 0,
            },
            "risk": local["risk"],
        }

    # Query bridge DB for cross-repo links
    cypher = CY_NEIGHBORS_UPSTREAM if direction == "upstream" else CY_NEIGHBORS_DOWNSTREAM
    try:
        bridge_rows = query_bridge(group_dir, cypher, {
            "localRepo": repo_path,
            "uids": uids,
        })
    except Exception as e:
        return {"error": f"Bridge DB query failed: {e}", "local": local}

    neighbors = []
    for row in bridge_rows:
        neighbor_repo = row.get("neighborRepo", "") or row.get("neighborRepo", "")
        neighbor_uid = row.get("neighborUid", "")
        if not neighbor_repo or not neighbor_uid:
            continue
        neighbors.append({
            "neighborRepo": str(neighbor_repo),
            "neighborUid": str(neighbor_uid),
            "neighborFilePath": str(row.get("neighborFilePath", "")),
            "neighborName": str(row.get("neighborName", "")),
            "matchType": str(row.get("matchType", "exact")),
            "confidence": float(row.get("confidence", 0)),
            "contractId": str(row.get("contractId", "")),
        })

    neighbors.sort(key=lambda n: n["confidence"], reverse=True)

    # For each neighbor, run local impact in their repo
    seen = set()
    for n in neighbors:
        key = f"{n['neighborRepo']}\x00{n['neighborUid']}\x00{n['contractId']}"
        if key in seen:
            continue
        seen.add(key)

        # Find the neighbor's DB path from config
        neighbor_reg_name = config.repos.get(n["neighborRepo"])
        if not neighbor_reg_name:
            out_of_scope.append({
                "from": n["neighborRepo"] if direction == "upstream" else repo_path,
                "to": repo_path if direction == "upstream" else n["neighborRepo"],
                "contractId": n["contractId"],
                "reason": "repo not in registry",
            })
            continue

        # Resolve neighbor's DB path from registry
        from ...storage.repo_manager import get_repo as _get_repo
        neighbor_info = _get_repo(neighbor_reg_name)
        if not neighbor_info:
            out_of_scope.append({
                "from": n["neighborRepo"] if direction == "upstream" else repo_path,
                "to": repo_path if direction == "upstream" else n["neighborRepo"],
                "contractId": n["contractId"],
                "reason": "repo not indexed",
            })
            continue

        neighbor_db = Path(neighbor_info.path) / ".pygitnexus" / "kuzu"
        if not neighbor_db.exists():
            out_of_scope.append({
                "from": n["neighborRepo"] if direction == "upstream" else repo_path,
                "to": repo_path if direction == "upstream" else n["neighborRepo"],
                "contractId": n["contractId"],
                "reason": "DB not found",
            })
            continue

        # Run impact-by-UID in the neighbor repo
        neighbor_store = GraphStore(neighbor_db)
        try:
            # Find the neighbor method by UID (exact match)
            neighbor_rows = neighbor_store.query(
                "MATCH (n) WHERE n.id = $uid RETURN n.id AS id, n.name AS name, "
                "labels(n) AS nodeType, n.filePath AS filePath",
                {"uid": n["neighborUid"]},
            )
            if not neighbor_rows:
                out_of_scope.append({
                    "from": n["neighborRepo"] if direction == "upstream" else repo_path,
                    "to": repo_path if direction == "upstream" else n["neighborRepo"],
                    "contractId": n["contractId"],
                    "reason": "symbol not found in neighbor DB",
                })
                continue

            n_sym = neighbor_rows[0]
            n_id = n_sym.get("id", "")
            n_type = n_sym.get("nodeType", {})
            n_type_str = next(iter(n_type.values()), "") if isinstance(n_type, dict) else ""

            # Run BFS from this symbol in the neighbor's DB
            if direction == "upstream":
                # We want callers of this consumer in the neighbor repo
                impact_cypher = (
                    f"MATCH (caller)-[r:CodeRelation]->(n) "
                    f"WHERE n.id = '{n_id}' AND r.type = 'CALLS' "
                    f"RETURN caller.id AS id, caller.name AS name, "
                    f"labels(caller) AS nodeType, caller.filePath AS filePath, "
                    f"r.confidence AS confidence"
                )
                caller_rows = neighbor_store.query(impact_cypher)
                callers = []
                for cr in caller_rows:
                    c_type = cr.get("nodeType", {})
                    c_type_str = next(iter(c_type.values()), "") if isinstance(c_type, dict) else ""
                    callers.append({
                        "id": cr.get("id", ""),
                        "name": cr.get("name", ""),
                        "type": c_type_str,
                        "filePath": cr.get("filePath", ""),
                        "confidence": cr.get("confidence", 0),
                    })

                grouped: dict[str, list[dict]] = {}
                if callers:
                    grouped["d=1"] = callers
                neighbor_impact = {
                    "risk": "MEDIUM" if callers else "LOW",
                    "summary": {
                        "total_affected": len(callers),
                        "direct_dependents": len(callers),
                    },
                    "byDepth": grouped,
                }
            else:
                # Downstream: callees
                impact_cypher = (
                    f"MATCH (n)-[r:CodeRelation]->(callee) "
                    f"WHERE n.id = '{n_id}' AND r.type = 'CALLS' "
                    f"RETURN callee.id AS id, callee.name AS name, "
                    f"labels(callee) AS nodeType, callee.filePath AS filePath, "
                    f"r.confidence AS confidence"
                )
                callee_rows = neighbor_store.query(impact_cypher)
                callees = []
                for cr in callee_rows:
                    c_type = cr.get("nodeType", {})
                    c_type_str = next(iter(c_type.values()), "") if isinstance(c_type, dict) else ""
                    callees.append({
                        "id": cr.get("id", ""),
                        "name": cr.get("name", ""),
                        "type": c_type_str,
                        "filePath": cr.get("filePath", ""),
                        "confidence": cr.get("confidence", 0),
                    })
                grouped = {}
                if callees:
                    grouped["d=1"] = callees
                neighbor_impact = {
                    "risk": "MEDIUM" if callees else "LOW",
                    "summary": {
                        "total_affected": len(callees),
                        "direct_dependents": len(callees),
                    },
                    "byDepth": grouped,
                }
        finally:
            neighbor_store.close()

        cross.append({
            "repo": neighbor_reg_name,
            "repo_path": n["neighborRepo"],
            "contract": {
                "id": n["contractId"],
                "match_type": n["matchType"],
                "confidence": n["confidence"],
            },
            "symbol": {
                "name": n["neighborName"],
                "filePath": n["neighborFilePath"],
            },
            "impact": neighbor_impact,
        })

    # Merge risk
    local_risk = local.get("risk", "LOW")
    cross_risks = [c["impact"].get("risk", "LOW") for c in cross]
    merged_risk = _merge_risk(local_risk, cross_risks)

    return {
        "local": local,
        "group": group_name,
        "cross": cross,
        "outOfScope": out_of_scope,
        "summary": {
            "direct": local["summary"]["direct_dependents"],
            "processes_affected": 0,
            "cross_repo_hits": len(cross),
        },
        "risk": merged_risk,
    }


def _merge_risk(local: str, cross_risks: list[str]) -> str:
    """Merge local and cross-repo risk levels."""
    if local == "CRITICAL":
        return "CRITICAL"
    high_conf = any(r in ("HIGH", "CRITICAL") for r in cross_risks)
    if len(cross_risks) >= 3:
        return "CRITICAL"
    if high_conf:
        return "HIGH"
    if len(cross_risks) > 0 and local in ("LOW", "UNKNOWN"):
        return "MEDIUM"
    return local
