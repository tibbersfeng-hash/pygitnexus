"""Contract extraction from repo KuzuDB.

A Contract represents an API endpoint that can be matched across repos:
- Provider: a controller method that serves an HTTP endpoint
- Consumer: code that calls an HTTP endpoint (frontend API function)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class Contract:
    """A cross-repo matchable contract."""
    contract_id: str  # e.g. "http::GET::/api/status"
    contract_type: str  # "http", "grpc", "topic"
    role: str  # "provider" or "consumer"
    repo: str  # group path (e.g. "app/backend")
    service: str  # optional service grouping
    symbol_uid: str  # Method/Function node id
    symbol_ref_file: str  # file path
    symbol_ref_name: str  # method/function name
    confidence: float = 1.0
    meta: dict | None = None


def normalize_http_path(path: str) -> str:
    """Normalize HTTP path for matching.

    - Remove trailing slashes
    - Collapse double slashes
    - Keep path params like {id} or :id as-is for exact matching
    """
    path = path.replace("//", "/").rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return path


def extract_http_contracts(
    store,
    repo_path: str,
    group_path: str,
) -> list[Contract]:
    """Extract HTTP contracts from a repo's KuzuDB.

    Looks for:
    1. Methods with httpMethod/httpPath properties (Spring controllers)
    2. CALLS relations with httpMethod/httpPath (frontend API calls)

    Args:
        store: GraphStore instance connected to the repo's KuzuDB
        repo_path: absolute path to the repo root
        group_path: group path identifier (e.g. "app/backend")
    """
    contracts: list[Contract] = []

    # 1. Extract provider contracts: Method nodes with HTTP info
    try:
        provider_rows = store.query(
            "MATCH (m:Method) "
            "WHERE m.httpMethod IS NOT NULL AND m.httpMethod <> '' "
            "AND m.httpPath IS NOT NULL AND m.httpPath <> '' "
            "RETURN m.id AS id, m.name AS name, m.filePath AS filePath, "
            "       m.className AS className, m.httpMethod AS httpMethod, "
            "       m.httpPath AS httpPath"
        )
        for row in provider_rows:
            method = row.get("httpMethod", "") or ""
            path = row.get("httpPath", "") or ""
            if not method or not path:
                continue

            path = normalize_http_path(str(path))
            contract_id = f"http::{method.upper()}::{path}"
            symbol_uid = row.get("id", "")
            if not symbol_uid:
                continue

            contracts.append(Contract(
                contract_id=contract_id,
                contract_type="http",
                role="provider",
                repo=group_path,
                service="",
                symbol_uid=symbol_uid,
                symbol_ref_file=str(row.get("filePath", "")),
                symbol_ref_name=str(row.get("name", "")),
                confidence=1.0,
            ))
    except Exception:
        pass

    # 2. Extract consumer contracts: CALLS relations with HTTP info
    try:
        consumer_rows = store.query(
            "MATCH (caller)-[r:CodeRelation]->(target) "
            "WHERE r.type = 'CALLS' "
            "AND r.httpMethod IS NOT NULL AND r.httpMethod <> '' "
            "AND r.httpPath IS NOT NULL AND r.httpPath <> '' "
            "RETURN caller.id AS caller_id, caller.name AS caller_name, "
            "       caller.filePath AS caller_path, "
            "       r.httpMethod AS httpMethod, r.httpPath AS httpPath"
        )
        for row in consumer_rows:
            method = row.get("httpMethod", "") or ""
            path = row.get("httpPath", "") or ""
            if not method or not path:
                continue

            path = normalize_http_path(str(path))
            contract_id = f"http::{method.upper()}::{path}"
            caller_id = row.get("caller_id", "")
            if not caller_id:
                continue

            contracts.append(Contract(
                contract_id=contract_id,
                contract_type="http",
                role="consumer",
                repo=group_path,
                service="",
                symbol_uid=caller_id,
                symbol_ref_file=str(row.get("caller_path", "")),
                symbol_ref_name=str(row.get("caller_name", "")),
                confidence=0.9,
            ))
    except Exception:
        pass

    return contracts
