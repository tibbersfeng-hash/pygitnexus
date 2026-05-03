"""Bridge DB management.

A separate KuzuDB that stores:
- Contract nodes: provider/consumer API endpoints
- ContractLink relations: consumer → provider cross-repo matches
- RepoSnapshot nodes: indexing metadata
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ...graph.store import GraphStore

BRIDGE_SCHEMA = [
    """CREATE NODE TABLE Contract (
        id STRING,
        contractId STRING,
        type STRING,
        role STRING,
        repo STRING,
        service STRING,
        symbolUid STRING,
        filePath STRING,
        symbolName STRING,
        confidence DOUBLE,
        meta STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE RepoSnapshot (
        id STRING,
        indexedAt STRING,
        lastCommit STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE REL TABLE ContractLink (
        FROM Contract TO Contract,
        matchType STRING,
        confidence DOUBLE,
        contractId STRING,
        fromRepo STRING,
        toRepo STRING
    )""",
]

BRIDGE_SCHEMA_VERSION = 1


def _make_contract_id(repo: str, contract_id: str, role: str, file_path: str) -> str:
    """Generate a stable contract node ID."""
    import hashlib
    raw = f"{repo}\x00{contract_id}\x00{role}\x00{file_path}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def ensure_bridge_schema(store: GraphStore) -> None:
    """Create bridge DB schema, ignoring errors for existing tables."""
    conn = store.get_connection()
    for q in BRIDGE_SCHEMA:
        try:
            conn.execute(q)
        except Exception as e:
            if "already exists" in str(e).lower():
                continue
            raise


def sync_bridge(
    group_dir: str,
    contracts: list[dict],
    cross_links: list[dict],
    repo_snapshots: dict,
    missing_repos: list[str],
) -> dict:
    """Build and write the bridge DB.

    Uses atomic swap: write to temp file, then rename.

    Args:
        group_dir: group directory path
        contracts: list of contract dicts
        cross_links: list of cross-link dicts
        repo_snapshots: {group_path: {indexedAt, lastCommit}}
        missing_repos: list of missing repo paths

    Returns:
        Report dict with counts.
    """
    import shutil
    import json

    bridge_dir = os.path.join(group_dir, "bridge")
    final_path = os.path.join(bridge_dir, "kuzu")
    tmp_path = os.path.join(bridge_dir, "kuzu_tmp")
    bak_path = os.path.join(bridge_dir, "kuzu.bak")

    # Clean up any leftover tmp — don't create it, KuzuDB auto-creates
    if os.path.exists(tmp_path):
        shutil.rmtree(tmp_path)
    os.makedirs(bridge_dir, exist_ok=True)

    report = {
        "contractsInserted": 0,
        "contractsFailed": 0,
        "linksInserted": 0,
        "linksFailed": 0,
    }

    try:
        # 1. Create temp DB
        tmp_store = GraphStore(Path(tmp_path))
        ensure_bridge_schema(tmp_store)

        # 2. Insert contracts
        for c in contracts:
            try:
                cid = _make_contract_id(
                    c["repo"], c["contractId"], c["role"], c.get("filePath", "")
                )
                tmp_store.get_connection().execute(
                    "CREATE (n:Contract {"
                    "  id: $id, contractId: $contractId, type: $type,"
                    "  role: $role, repo: $repo, service: $service,"
                    "  symbolUid: $symbolUid, filePath: $filePath,"
                    "  symbolName: $symbolName, confidence: $confidence,"
                    "  meta: $meta"
                    "})",
                    {
                        "id": cid,
                        "contractId": c["contractId"],
                        "type": c.get("type", "http"),
                        "role": c["role"],
                        "repo": c["repo"],
                        "service": c.get("service", ""),
                        "symbolUid": c.get("symbolUid", ""),
                        "filePath": c.get("filePath", ""),
                        "symbolName": c.get("symbolName", ""),
                        "confidence": c.get("confidence", 0.0),
                        "meta": json.dumps(c.get("meta", {})),
                    },
                )
                c["_node_id"] = cid
                report["contractsInserted"] += 1
            except Exception:
                report["contractsFailed"] += 1

        # 3. Insert RepoSnapshot nodes
        for repo_id, snap in repo_snapshots.items():
            try:
                tmp_store.get_connection().execute(
                    "CREATE (n:RepoSnapshot {id: $id, indexedAt: $ia, lastCommit: $lc})",
                    {"id": repo_id, "ia": snap.get("indexedAt", ""), "lc": snap.get("lastCommit", "")},
                )
            except Exception:
                pass

        # 4. Insert ContractLink relations
        # Build lookup: symbol_uid + repo → contract node_id
        contract_lookup: dict[tuple[str, str], str] = {}
        for c in contracts:
            if "_node_id" in c:
                contract_lookup[(c["repo"], c.get("symbolUid", ""))] = c["_node_id"]

        for link in cross_links:
            try:
                from_id = contract_lookup.get((link["from"]["repo"], link["from"].get("symbol_uid", "")))
                to_id = contract_lookup.get((link["to"]["repo"], link["to"].get("symbol_uid", "")))
                if not from_id or not to_id:
                    continue

                tmp_store.get_connection().execute(
                    "MATCH (a:Contract), (b:Contract) "
                    "WHERE a.id = $fromId AND b.id = $toId "
                    "CREATE (a)-[r:ContractLink {"
                    "  matchType: $matchType, confidence: $confidence,"
                    "  contractId: $contractId, fromRepo: $fromRepo, toRepo: $toRepo"
                    "}]->(b)",
                    {
                        "fromId": from_id,
                        "toId": to_id,
                        "matchType": link.get("matchType", "exact"),
                        "confidence": link.get("confidence", 0.0),
                        "contractId": link.get("contractId", ""),
                        "fromRepo": link["from"]["repo"],
                        "toRepo": link["to"]["repo"],
                    },
                )
                report["linksInserted"] += 1
            except Exception:
                report["linksFailed"] += 1

        tmp_store.close()

    finally:
        pass

    # 5. Atomic swap
    os.makedirs(bridge_dir, exist_ok=True)
    if os.path.exists(final_path):
        try:
            if os.path.exists(bak_path):
                shutil.rmtree(bak_path)
            os.rename(final_path, bak_path)
        except OSError:
            pass
    try:
        os.rename(tmp_path, final_path)
    except OSError:
        # Fallback: copy
        if os.path.exists(final_path):
            shutil.rmtree(final_path)
        shutil.copytree(tmp_path, final_path)
    finally:
        if os.path.exists(tmp_path):
            shutil.rmtree(tmp_path, ignore_errors=True)

    # 6. Write meta.json
    import json as _json
    from datetime import datetime, timezone

    meta = {
        "version": BRIDGE_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "missingRepos": missing_repos,
        "report": report,
    }
    with open(os.path.join(group_dir, "meta.json"), "w", encoding="utf-8") as f:
        _json.dump(meta, f, indent=2)

    return report


def query_bridge(
    group_dir: str,
    cypher: str,
    params: dict | None = None,
) -> list[dict]:
    """Query the bridge DB in read-only mode."""
    bridge_path = os.path.join(group_dir, "bridge", "kuzu")
    if not os.path.exists(bridge_path):
        return []

    store = GraphStore(Path(bridge_path))
    try:
        if params:
            rows = store.query(cypher, params)
        else:
            rows = store.query(cypher)
        return rows
    finally:
        store.close()
