"""Shared frontend-to-backend mapping logic.

Used by both the web dashboard (HTTP API) and MCP Server.
All functions are synchronous and return plain Python dicts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ─── Constants ────────────────────────────────────────────────────────

_FRONTEND_EXTENSIONS = {".js", ".ts", ".vue"}


def _guess_page_name(file_path: Path, root: Path) -> str:
    """Guess a human-readable page name from a frontend file path."""
    rel = str(file_path.relative_to(root))
    parts = Path(rel).parts
    for keyword in ("views", "pages", "screens", "router", "service", "api"):
        if keyword in parts:
            idx = parts.index(keyword)
            remaining = parts[idx + 1:]
            return "/".join(remaining)
    return rel


def _guess_page_name_from_path(file_path_str: str, repo_path: str) -> str:
    """Guess page name from string paths (used when file is not on disk)."""
    fp = Path(file_path_str)
    root = Path(repo_path)
    try:
        return _guess_page_name(fp, root)
    except ValueError:
        return fp.name


# ─── Frontend scanning ────────────────────────────────────────────────

def _scan_frontend_api_calls(frontend_path: str) -> list[dict]:
    """Scan frontend source files for API call patterns.

    Returns list of { page, httpMethod, httpPath, line, file }.
    """
    root = Path(frontend_path)
    if not root.is_dir():
        return []

    results: list[dict] = []
    for ext in _FRONTEND_EXTENSIONS:
        for fp in root.rglob(f"*{ext}"):
            if "node_modules" in str(fp) or "dist" in str(fp) or "vendor" in str(fp):
                continue
            try:
                content = fp.read_text(errors="replace")
            except (OSError, PermissionError):
                continue

            rel_path = str(fp.relative_to(root))
            page_name = _guess_page_name(fp, root)

            for i, line_text in enumerate(content.splitlines(), 1):
                # Pattern: axios.get('/path') / http.get('/path') / api.get('/path')
                for m in re.finditer(
                    r"""(?:axios|request|http|api|client)\.(get|post|put|delete|patch)\s*\(\s*['"`]([^'"`]+)['"`]""",
                    line_text, re.IGNORECASE,
                ):
                    method = m.group(1).upper()
                    path = m.group(2)
                    results.append({
                        "page": page_name,
                        "httpMethod": method,
                        "httpPath": path,
                        "line": i,
                        "file": rel_path,
                    })

                # Pattern: http.request("METHOD", "/path", ...)
                for m in re.finditer(
                    r"""(?:axios|request|http|api|client)\.request\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"`]([^'"`]+)['"`]""",
                    line_text, re.IGNORECASE,
                ):
                    method = m.group(1).upper()
                    path = m.group(2)
                    results.append({
                        "page": page_name,
                        "httpMethod": method,
                        "httpPath": path,
                        "line": i,
                        "file": rel_path,
                    })

                # Pattern: fetch('/path')
                for m in re.finditer(
                    r"""fetch\s*\(\s*['"]([^'"]+)['"]""",
                    line_text,
                ):
                    path = m.group(1)
                    if path.startswith(("http://", "https://", ".", "/")):
                        method = "GET"
                        lower = line_text.lower()
                        if "post" in lower:
                            method = "POST"
                        elif "put" in lower:
                            method = "PUT"
                        elif "delete" in lower:
                            method = "DELETE"
                        elif "patch" in lower:
                            method = "PATCH"
                        results.append({
                            "page": page_name,
                            "httpMethod": method,
                            "httpPath": path,
                            "line": i,
                            "file": rel_path,
                        })

                # Pattern: { url: '/path' } or { path: '/path' }
                for m in re.finditer(
                    r"""(?:url|path)\s*:\s*['"]([^'"]+)['"]""",
                    line_text,
                ):
                    path = m.group(1)
                    if not path.startswith(("/", "http", "/api")):
                        continue
                    method = "GET"
                    context = "\n".join(content.splitlines()[max(0, i - 3):i + 3]).lower()
                    if "method" in context:
                        for mm in re.finditer(
                            r"""method\s*:\s*['"](GET|POST|PUT|DELETE|PATCH)['"]""",
                            context, re.IGNORECASE,
                        ):
                            method = mm.group(1).upper()
                            break
                    results.append({
                        "page": page_name,
                        "httpMethod": method,
                        "httpPath": path,
                        "line": i,
                        "file": rel_path,
                    })

    return results


def _extract_vue_routes(frontend_path: str) -> list[dict]:
    """Extract Vue router path → component mappings."""
    root = Path(frontend_path)
    results: list[dict] = []

    router_patterns = [
        "src/router/index.js",
        "src/router/index.ts",
        "src/router/routes.js",
        "src/router/routes.ts",
        "src/routes/index.js",
        "src/routes/index.ts",
        "router/index.js",
        "router/index.ts",
    ]

    for rp in router_patterns:
        fp = root / rp
        if not fp.exists():
            continue
        try:
            content = fp.read_text(errors="replace")
        except (OSError, PermissionError):
            continue

        route_re = re.compile(r"path\s*:\s*['\"]([^'\"]+)['\"]")
        component_re = re.compile(r"(?:component|name)\s*:\s*(?:['\"]([^'\"]+)['\"]|(\w+))")

        for i, line in enumerate(content.splitlines(), 1):
            path_match = route_re.search(line)
            if path_match:
                path = path_match.group(1)
                comp_match = component_re.search(line)
                component = comp_match.group(1) or comp_match.group(2) or "" if comp_match else ""
                results.append({
                    "path": path,
                    "component": component,
                    "file": rp,
                    "line": i,
                })

    return results


# ─── API matching ─────────────────────────────────────────────────────

def _match_api_to_endpoint(
    frontend_calls: list[dict],
    backend_endpoints: list[dict],
) -> list[dict]:
    """Match frontend API calls to backend HTTP endpoints by path.

    Supports: exact match, prefix match with param substitution.
    """
    matches = []
    for call in frontend_calls:
        call_path = call["httpPath"]
        call_method = call["httpMethod"]

        for ctrl in backend_endpoints:
            for ep in ctrl.get("endpoints", []):
                ep_path = ep.get("path", "")
                ep_method = ep.get("method", "")

                # Exact match
                if call_path == ep_path and call_method == ep_method:
                    matches.append({
                        **call,
                        "controller": ctrl["name"],
                        "controllerMethod": ep["methodName"],
                        "backendPath": ep_path,
                        "matchType": "exact",
                    })
                    continue

                # Param substitution match: /api/user/123 matches /api/user/{id}
                if call_method == ep_method:
                    pattern = ep_path
                    for param in re.finditer(r"\{(\w+)\}", pattern):
                        pattern = pattern.replace(
                            "{" + param.group(1) + "}",
                            r"([^/]+)",
                        )
                    if re.fullmatch(pattern, call_path):
                        matches.append({
                            **call,
                            "controller": ctrl["name"],
                            "controllerMethod": ep["methodName"],
                            "backendPath": ep_path,
                            "matchType": "param",
                        })

    return matches


# ─── DB chain helpers ─────────────────────────────────────────────────

def _parse_attrs(raw: Any) -> dict:
    """Parse annotation attributes (may be JSON string or dict)."""
    attrs = raw or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except (json.JSONDecodeError, TypeError):
            attrs = {}
    return attrs if isinstance(attrs, dict) else {}


def _find_table_for_repo(repo_name: str, table_map: dict, repo_file_map: dict) -> str:
    """Find the table name associated with a repository by matching entity names."""
    repo_file = repo_file_map.get(repo_name, "")
    repo_base = repo_name.replace("Repository", "")
    for cls_name, tbl in table_map.items():
        short = cls_name.split(".")[-1]
        if repo_base == short:
            return tbl["name"]

    if repo_file:
        repo_path = Path(repo_file)
        parts = repo_path.parts
        try:
            repo_idx = parts.index("repository")
            model_parts = list(parts[:repo_idx]) + ["model"] + list(parts[repo_idx + 1:])
            model_dir = str(Path(*model_parts[:-1]))
            for cls_name, tbl in table_map.items():
                fp = tbl.get("filePath", "")
                if fp and str(Path(fp).parent) == model_dir:
                    return tbl["name"]
        except ValueError:
            pass

    return ""


def _detect_orm(repo_name: str, repo_file_map: dict, store: Any) -> str:
    """Detect which ORM framework the repository uses."""
    repo_file = repo_file_map.get(repo_name, "")
    if repo_file:
        anns = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c) "
            "WHERE c.name = $name AND c.filePath = $fp "
            "RETURN a.name AS annName",
            {"name": repo_name, "fp": repo_file},
        )
        ann_names = {a["annName"] for a in anns}
        if "Mapper" in ann_names:
            return "MyBatis"
        if "Entity" in ann_names:
            return "JPA/Hibernate"
    return "Spring Data JDBC"


# ─── Public query functions ──────────────────────────────────────────

def query_db_chain(repo_path: str, store: Any) -> dict:
    """Query DB call chain: Controller → Service → Repository → Table.

    Args:
        repo_path: Repository path (for error messages).
        store: GraphStore instance.

    Returns:
        Dict with tables, chains, repository_methods, jdbcTemplate_services.
    """
    # 1. Tables from @Table annotations
    tables_raw = store.query(
        "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
        "WHERE a.name = 'Table' "
        "RETURN c.name AS className, c.filePath AS filePath, a.attributes AS attrs"
    )
    table_map: dict[str, dict] = {}
    for t in tables_raw:
        attrs = _parse_attrs(t.get("attrs"))
        table_map[t["className"]] = {
            "name": attrs.get("path", attrs.get("name", "")),
            "className": t["className"],
        }

    # 2. Repositories
    repos = store.query(
        "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Interface) "
        "WHERE a.name = 'Repository' "
        "RETURN c.name AS name, c.filePath AS filePath"
    )
    repo_name_to_file: dict[str, str] = {}
    for r in repos:
        repo_name_to_file[r["name"]] = r.get("filePath", "")

    # 3. Service → Repository calls
    svc_repo = store.query(
        "MATCH (svc:Method)-[r:CodeRelation]->(repo:Method) "
        "WHERE r.type = 'CALLS' AND repo.filePath CONTAINS '/repository/' "
        "RETURN svc.className AS svcName, svc.name AS svcMethod, "
        "       repo.className AS repoName, repo.name AS repoMethod"
    )
    svc_repo_map: dict[tuple, dict] = {}
    for sr in svc_repo:
        svc_repo_map[(sr["svcName"], sr["svcMethod"])] = {
            "repoName": sr["repoName"],
            "repoMethod": sr["repoMethod"],
        }

    # 4. Controller → Service calls
    ctrl_svc = store.query(
        "MATCH (ctrl:Method)-[r:CodeRelation]->(svc:Method) "
        "WHERE r.type = 'CALLS' "
        "AND ctrl.filePath CONTAINS '/controller/' "
        "AND svc.filePath CONTAINS '/service/' "
        "RETURN ctrl.className AS ctrlName, ctrl.name AS ctrlMethod, "
        "       svc.className AS svcName, svc.name AS svcMethod"
    )

    # 5. Build chains
    chains = []
    for cs in ctrl_svc:
        key = (cs["svcName"], cs["svcMethod"])
        if key in svc_repo_map:
            sr = svc_repo_map[key]
            repo_name = sr["repoName"]
            table_name = _find_table_for_repo(repo_name, table_map, repo_name_to_file)
            orm_type = _detect_orm(repo_name, repo_name_to_file, store)
            chains.append({
                "controller": cs["ctrlName"],
                "controllerMethod": cs["ctrlMethod"],
                "service": cs["svcName"],
                "serviceMethod": cs["svcMethod"],
                "repository": repo_name,
                "repoMethod": sr["repoMethod"],
                "table": table_name or "",
                "ormType": orm_type,
                "operation": "READ",
            })

    # 6. Repository method details
    repo_methods = []
    for repo_name, repo_file in repo_name_to_file.items():
        simple_name = repo_name.split(".")[-1]
        repo_m = store.query(
            "MATCH (m:Method) WHERE m.className = $name "
            "RETURN m.name AS methodName, m.parameterCount AS params",
            {"name": simple_name},
        )
        for rm in repo_m:
            inferred = _infer_spring_data_method(rm["methodName"], table_map, repo_name)
            if inferred:
                repo_methods.append(inferred)

    # 7. JdbcTemplate services
    jt_fields = store.query(
        "MATCH (f:Field) WHERE f.name = 'jdbcTemplate' "
        "RETURN DISTINCT f.className AS serviceName"
    )
    jdbcTemplate_services = sorted([f["serviceName"] for f in jt_fields])

    return {
        "tables": list(table_map.values()),
        "chains": chains,
        "jdbcTemplate_services": jdbcTemplate_services,
        "repository_methods": repo_methods,
    }


def _infer_spring_data_method(method_name: str, table_map: dict, repo_name: str) -> dict | None:
    """Infer SQL operation from Spring Data method name."""
    name_lower = method_name.lower()
    if name_lower.startswith(("find", "get", "read", "count", "exists")):
        op = "COUNT" if name_lower.startswith("count") or name_lower.startswith("exists") else "READ"
    elif name_lower.startswith(("delete", "remove")):
        op = "DELETE"
    elif name_lower.startswith(("save", "insert", "update")):
        op = "WRITE"
    else:
        return None

    table_name = ""
    for cls_name, tbl in table_map.items():
        short = cls_name.split(".")[-1]
        repo_base = repo_name.replace("Repository", "")
        if repo_base == short:
            table_name = tbl["name"]
            break

    return {
        "repository": repo_name,
        "method": method_name,
        "operation": op,
        "table": table_name,
    }


def _build_backend_endpoints(store: Any) -> list[dict]:
    """Load backend HTTP endpoints from knowledge graph.

    Returns list of { name, endpoints: [{ method, path, methodName }] }.
    """
    ctrl_query = """
        MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class)
        WHERE a.name IN ['RestController', 'Controller']
        RETURN c.name AS ctrlName, c.filePath AS filePath
        ORDER BY c.name
    """
    controllers = store.query(ctrl_query)

    backend_endpoints = []
    for ctrl in controllers:
        ctrl_name = ctrl["ctrlName"]
        simple_name = ctrl_name.split(".")[-1]

        base_path = ""
        mp_result = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
            "WHERE a.name = 'RequestMapping' AND c.name = $name "
            "RETURN a.attributes AS attrs LIMIT 1",
            {"name": ctrl_name},
        )
        if mp_result:
            attrs = _parse_attrs(mp_result[0].get("attrs"))
            base_path = attrs.get("path", "") if isinstance(attrs, dict) else ""

        ep_result = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
            "WHERE a.name IN ['GetMapping','PostMapping','PutMapping','DeleteMapping','PatchMapping'] "
            "AND m.className = $simple "
            "RETURN a.name AS httpAnn, a.attributes AS attrs, m.name AS methodName",
            {"simple": simple_name},
        )

        endpoints = []
        for ep in ep_result:
            attrs = _parse_attrs(ep.get("attrs"))
            ep_path = attrs.get("path", "") if isinstance(attrs, dict) else ""
            http_map = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
                        "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}
            method = http_map.get(ep["httpAnn"], ep["httpAnn"])
            full_path = base_path + ep_path if base_path else ep_path
            endpoints.append({
                "method": method,
                "path": full_path,
                "methodName": ep["methodName"],
            })

        backend_endpoints.append({
            "name": simple_name,
            "endpoints": endpoints,
        })

    return backend_endpoints


def _enrich_with_db_chain(store: Any, matched: list[dict]) -> list[dict]:
    """Enrich matched API calls with DB chain data (tables, service)."""
    ctrl_to_tables: dict[str, set[str]] = {}
    ctrl_to_service: dict[str, str] = {}
    for chain in query_db_chain("", store).get("chains", []):
        ctrl_key = chain["controller"]
        ctrl_to_tables.setdefault(ctrl_key, set()).add(chain.get("table", ""))
        ctrl_to_service[ctrl_key] = chain.get("service", "")

    for m in matched:
        ctrl = m.get("controller", "")
        m["linkedTables"] = sorted(ctrl_to_tables.get(ctrl, []))
        m["linkedService"] = ctrl_to_service.get(ctrl, "")

    return matched


def query_frontend_pages_single_repo(repo_path: str, store: Any) -> dict:
    """Single-repo frontend→backend mapping.

    Uses USES_ENDPOINT relations from knowledge graph if available,
    falls back to regex scanning of frontend files.

    Args:
        repo_path: Repository path.
        store: GraphStore instance.

    Returns:
        Dict with mode, source, apiCalls, frontendPages, etc.
    """
    # Check for USES_ENDPOINT relations
    uses_endpoint_rows = store.query(
        "MATCH (fe:Method)-[r:CodeRelation]->(be:Method) "
        "WHERE r.type = 'USES_ENDPOINT' "
        "RETURN fe.name AS feMethod, fe.className AS feClass, "
        "       fe.filePath AS feFile, be.name AS beMethod, "
        "       be.className AS beClass, be.filePath AS beFile, "
        "       r.httpMethod AS httpMethod, r.httpPath AS httpPath, "
        "       r.confidence AS confidence "
        "ORDER BY fe.filePath, fe.name"
    )

    if uses_endpoint_rows:
        return _handle_uses_endpoint(store, uses_endpoint_rows, repo_path)
    else:
        return _handle_scan_fallback(store, repo_path)


def _handle_uses_endpoint(store: Any, uses_endpoint_rows: list[dict], repo_path: str) -> dict:
    """Handle single-repo mode using USES_ENDPOINT relations."""
    backend_endpoints = _build_backend_endpoints(store)

    ctrl_to_tables: dict[str, set[str]] = {}
    ctrl_to_service: dict[str, str] = {}
    for chain in query_db_chain(repo_path, store).get("chains", []):
        ctrl_key = chain["controller"]
        ctrl_to_tables.setdefault(ctrl_key, set()).add(chain.get("table", ""))
        ctrl_to_service[ctrl_key] = chain.get("service", "")

    matched = []
    for row in uses_endpoint_rows:
        fe_file = row.get("feFile", "")
        fe_page = _guess_page_name_from_path(fe_file, repo_path)
        be_class = row.get("beClass", "")
        be_ctrl = be_class.split(".")[-1] if be_class else ""

        matched.append({
            "page": fe_page,
            "httpMethod": row.get("httpMethod", ""),
            "httpPath": row.get("httpPath", ""),
            "controller": be_ctrl,
            "controllerMethod": row.get("beMethod", ""),
            "feFile": fe_file,
            "feMethod": row.get("feMethod", ""),
            "matchType": "USES_ENDPOINT",
            "confidence": row.get("confidence", 0),
            "linkedTables": sorted(ctrl_to_tables.get(be_ctrl, [])),
            "linkedService": ctrl_to_service.get(be_ctrl, ""),
        })

    # Deduplicate
    seen_keys = set()
    unique_matched = []
    for m in matched:
        key = (m["page"], m["httpMethod"], m["httpPath"], m.get("controller", ""))
        if key not in seen_keys:
            seen_keys.add(key)
            unique_matched.append(m)

    return {
        "mode": "single-repo",
        "source": "USES_ENDPOINT",
        "apiCalls": unique_matched,
        "totalApiCalls": len(unique_matched),
        "matchedApiCalls": len(unique_matched),
        "routes": [],
        "frontendPages": sorted(set(m["page"] for m in unique_matched)),
        "backendEndpoints": backend_endpoints,
        "unmatchedCalls": 0,
    }


def _handle_scan_fallback(store: Any, repo_path: str) -> dict:
    """Single-repo fallback: scan frontend files within the repo."""
    frontend_calls = _scan_frontend_api_calls(repo_path)
    routes = _extract_vue_routes(repo_path)

    if not frontend_calls:
        return {
            "mode": "single-repo",
            "source": "scan",
            "apiCalls": [],
            "totalApiCalls": 0,
            "matchedApiCalls": 0,
            "routes": [],
            "frontendPages": [],
            "backendEndpoints": [],
            "unmatchedCalls": 0,
        }

    backend_endpoints = _build_backend_endpoints(store)
    matched = _match_api_to_endpoint(frontend_calls, backend_endpoints)

    # Deduplicate
    seen_keys = set()
    unique_matched = []
    for m in matched:
        key = (m["page"], m["httpMethod"], m["httpPath"], m.get("controller", ""))
        if key not in seen_keys:
            seen_keys.add(key)
            unique_matched.append(m)

    # Enrich with DB chain
    ctrl_to_tables: dict[str, set[str]] = {}
    ctrl_to_service: dict[str, str] = {}
    for chain in query_db_chain(repo_path, store).get("chains", []):
        ctrl_key = chain["controller"]
        ctrl_to_tables.setdefault(ctrl_key, set()).add(chain.get("table", ""))
        ctrl_to_service[ctrl_key] = chain.get("service", "")

    for m in unique_matched:
        ctrl = m.get("controller", "")
        m["linkedTables"] = sorted(ctrl_to_tables.get(ctrl, []))
        m["linkedService"] = ctrl_to_service.get(ctrl, "")

    return {
        "mode": "single-repo",
        "source": "scan",
        "apiCalls": unique_matched,
        "totalApiCalls": len(frontend_calls),
        "matchedApiCalls": len(unique_matched),
        "routes": routes,
        "frontendPages": sorted(set(m["page"] for m in unique_matched)),
        "backendEndpoints": backend_endpoints,
        "unmatchedCalls": len(frontend_calls) - len(unique_matched),
    }


def query_frontend_pages_group(group_name: str) -> dict:
    """Cross-repo frontend→backend mapping via Group.

    Loads backend endpoints from one repo's knowledge graph,
    scans frontend files from another repo, and matches in memory.

    Args:
        group_name: Group name from registry.

    Returns:
        Dict with mode, group, frontendRepo, backendRepo, pages, apiCalls.
    """
    from ..storage.repo_manager import get_group

    group = get_group(group_name)
    if group is None:
        return {"error": f"Group '{group_name}' not found"}

    frontend_repo = None
    backend_repo = None
    for r in group.repos:
        if r.role == "frontend":
            frontend_repo = r
        elif r.role == "backend":
            backend_repo = r

    if backend_repo is None:
        return {"error": f"Group '{group_name}' has no repo with role 'backend'"}

    # Load backend store
    backend_path = Path(backend_repo.path)
    db_path = backend_path / ".pygitnexus" / "kuzu"
    if not db_path.exists():
        return {"error": f"Backend repo not indexed: {backend_repo.path}"}

    from ..graph.store import GraphStore
    backend_store = GraphStore(db_path)
    try:
        backend_endpoints = _build_backend_endpoints(backend_store)

        # DB chain enrichment
        ctrl_to_tables: dict[str, set[str]] = {}
        ctrl_to_service: dict[str, str] = {}
        for chain in query_db_chain(str(backend_repo.path), backend_store).get("chains", []):
            ctrl_key = chain["controller"]
            ctrl_to_tables.setdefault(ctrl_key, set()).add(chain.get("table", ""))
            ctrl_to_service[ctrl_key] = chain.get("service", "")

        # Scan frontend
        api_calls = []
        routes = []
        if frontend_repo:
            api_calls = _scan_frontend_api_calls(frontend_repo.path)
            routes = _extract_vue_routes(frontend_repo.path)

        # Match
        matched = _match_api_to_endpoint(api_calls, backend_endpoints)

        # Deduplicate
        seen_keys = set()
        unique_matched = []
        for m in matched:
            key = (m["page"], m["httpMethod"], m["httpPath"], m.get("controller", ""))
            if key not in seen_keys:
                seen_keys.add(key)
                unique_matched.append(m)

        # Enrich
        for m in unique_matched:
            ctrl = m.get("controller", "")
            m["linkedTables"] = sorted(ctrl_to_tables.get(ctrl, []))
            m["linkedService"] = ctrl_to_service.get(ctrl, "")

        return {
            "mode": "cross-repo",
            "group": group_name,
            "frontendRepo": frontend_repo.name if frontend_repo else "",
            "backendRepo": backend_repo.name,
            "apiCalls": unique_matched,
            "totalApiCalls": len(api_calls),
            "matchedApiCalls": len(unique_matched),
            "routes": routes,
            "frontendPages": sorted(set(m["page"] for m in unique_matched)),
            "backendEndpoints": backend_endpoints,
            "unmatchedCalls": len(api_calls) - len(unique_matched),
        }
    finally:
        backend_store.close()


# ─── DB Field Impact ────────────────────────────────────────────────

def _scan_mybatis_tables(repo_path: str) -> dict[str, str]:
    """Scan MyBatis XML mapper files to extract table name mappings.

    Returns {table_name: mapper_name} e.g., {'tb_newbee_mall_user': 'MallUserMapper'}.
    """
    root = Path(repo_path)
    table_to_mapper: dict[str, str] = {}
    _SQL_KEYWORDS = {"SELECT", "WHERE", "SET", "AND", "OR", "ORDER", "GROUP",
                     "LIMIT", "VALUES", "id", "IF", "ELSE", "FOREACH", "WHEN"}

    for xml_file in root.rglob("**/*Mapper.xml"):
        try:
            content = xml_file.read_text(errors="replace")
        except (OSError, PermissionError):
            continue
        mapper_name = xml_file.stem  # e.g., "AdminUserMapper"
        tables = set()
        for m in re.finditer(r"(?:FROM|INTO|UPDATE)\s+([a-zA-Z_]\w*)", content, re.IGNORECASE):
            t = m.group(1)
            if t not in _SQL_KEYWORDS:
                tables.add(t)
        for t in tables:
            table_to_mapper[t] = mapper_name

    return table_to_mapper


def _scan_mybatis_fields(store: Any, mapper_name: str, repo_path: str) -> list[dict]:
    """Infer fields accessed by a MyBatis mapper method.

    Uses @Param annotations and method name patterns.
    """
    fields: list[dict] = []

    # Get methods with @Param annotations
    rows = store.query(
        "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
        "WHERE a.name = 'Param' "
        "AND m.className = $mapper "
        "RETURN m.name AS method, a.attributes AS attrs",
        {"mapper": mapper_name},
    )
    seen_fields = set()
    for row in rows:
        attrs = _parse_attrs(row.get("attrs"))
        param_val = attrs.get("path", attrs.get("value", ""))
        if param_val and param_val not in seen_fields:
            seen_fields.add(param_val)
            fields.append({
                "field": param_val,
                "snake_field": _camel_to_snake(param_val) if not param_val.startswith("tb_") else param_val,
                "access_type": "READ",
            })

    # Also infer from method names (e.g., selectByUserId → user_id field)
    all_methods = store.query(
        "MATCH (m:Method) WHERE m.className = $mapper RETURN m.name AS method",
        {"mapper": mapper_name},
    )
    for m in all_methods:
        method_name = m["method"]
        # Extract fields from "selectBy{Field}", "update{Field}", etc.
        name_fields = re.findall(r"By([A-Z][a-zA-Z0-9]+)", method_name)
        for nf in name_fields:
            # Skip generic names
            if nf.lower() in ("primary", "batch", "all", "list", "count", "total"):
                continue
            snake = _camel_to_snake(nf)
            if snake not in seen_fields:
                seen_fields.add(snake)
                fields.append({
                    "field": nf,
                    "snake_field": snake,
                    "access_type": "READ",
                })

    return fields


def _camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    return re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")


def _infer_mybatis_fields(store: Any, mapper_name: str, method_name: str) -> list[dict]:
    """Infer which DB fields are accessed by a MyBatis mapper method.

    Uses @Param annotations on the method and method name patterns.
    """
    fields: list[dict] = []
    seen: set[str] = set()
    _SKIP_FIELDS = {"primary", "batch", "all", "list", "count", "total", "stock",
                    "key", "selective", "done", "ids", "primarykey", "primarykeyselective"}

    # @Param annotations on this specific method
    rows = store.query(
        "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
        "WHERE a.name = 'Param' AND m.className = $mapper AND m.name = $method "
        "RETURN a.attributes AS attrs",
        {"mapper": mapper_name, "method": method_name},
    )
    for row in rows:
        attrs = _parse_attrs(row.get("attrs"))
        param_val = attrs.get("path", attrs.get("value", ""))
        if param_val:
            snake = _camel_to_snake(param_val)
            if snake not in seen:
                seen.add(snake)
                fields.append({
                    "field": param_val,
                    "snake_field": snake,
                    "access_type": "READ",
                })

    # Method name patterns: selectBy{Field}And{Field2} → split by And/Or
    match = re.search(r"By([A-Z].*)", method_name)
    if match:
        conditions_part = match.group(1)
        conditions_part = re.sub(r"(?:OrderBy|Set|Update)[A-Za-z]+$", "", conditions_part)
        field_names = re.split(r"And|Or", conditions_part)
        for fn in field_names:
            if not fn or fn.lower() in _SKIP_FIELDS:
                continue
            snake = _camel_to_snake(fn)
            if snake in seen:
                continue
            seen.add(snake)
            fields.append({
                "field": fn,
                "snake_field": snake,
                "access_type": "READ",
            })

    return fields if fields else [{"field": "*", "snake_field": "*", "access_type": _infer_operation(method_name)}]


def _scan_mybatis_fields(store: Any, mapper_name: str, repo_path: str) -> list[dict]:
    """Infer fields accessed by all methods in a MyBatis mapper.

    Uses @Param annotations and method name patterns across all methods.
    """
    fields: list[dict] = []

    # Get methods with @Param annotations
    rows = store.query(
        "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
        "WHERE a.name = 'Param' "
        "AND m.className = $mapper "
        "RETURN m.name AS method, a.attributes AS attrs",
        {"mapper": mapper_name},
    )
    seen_fields = set()
    for row in rows:
        attrs = _parse_attrs(row.get("attrs"))
        param_val = attrs.get("path", attrs.get("value", ""))
        if param_val and param_val not in seen_fields:
            seen_fields.add(param_val)
            fields.append({
                "field": param_val,
                "snake_field": _camel_to_snake(param_val) if not param_val.startswith("tb_") else param_val,
                "access_type": "READ",
            })

    # Also infer from method names (e.g., selectByUserId → user_id field)
    all_methods = store.query(
        "MATCH (m:Method) WHERE m.className = $mapper RETURN m.name AS method",
        {"mapper": mapper_name},
    )
    for m in all_methods:
        method_name = m["method"]
        # Extract fields from "selectBy{Field}", "update{Field}", etc.
        name_fields = re.findall(r"By([A-Z][a-zA-Z0-9]+)", method_name)
        for nf in name_fields:
            # Skip generic names
            if nf.lower() in ("primary", "batch", "all", "list", "count", "total"):
                continue
            snake = _camel_to_snake(nf)
            if snake not in seen_fields:
                seen_fields.add(snake)
                fields.append({
                    "field": nf,
                    "snake_field": snake,
                    "access_type": "READ",
                })

    return fields


def _infer_fields_from_method(method_name: str) -> list[dict]:
    """Infer which DB fields are accessed from a Spring Data method name.

    Returns list of {field, snake_field, access_type}.
    """
    name_lower = method_name.lower()
    fields = []

    # Determine operation type
    if name_lower.startswith(("find", "get", "read", "count", "exists", "is")):
        op = "READ"
    elif name_lower.startswith(("delete", "remove")):
        op = "DELETE"
    elif name_lower.startswith(("save", "insert", "create", "add")):
        op = "WRITE"
    elif name_lower.startswith(("update", "modify", "set")):
        op = "UPDATE"
    else:
        op = "UNKNOWN"

    # For WRITE/UPDATE without conditions → all fields
    if op in ("WRITE", "UPDATE"):
        after_by = re.search(r"By([A-Z].*)", method_name)
        if not after_by:
            return [{"field": "*", "snake_field": "*", "access_type": op}]
    elif op == "READ" and name_lower.startswith(("findall", "list", "getall")):
        # No conditions
        order_fields = re.search(r"OrderBy([A-Za-z]+?)(?:Asc|Desc)$", method_name)
        if order_fields:
            fields.append({
                "field": order_fields.group(1),
                "snake_field": _camel_to_snake(order_fields.group(1)),
                "access_type": "READ_ORDER",
            })
        return fields if fields else [{"field": "*", "snake_field": "*", "access_type": op}]

    # Extract conditions after "By"
    match = re.search(r"By([A-Z].*)", method_name)
    if match:
        conditions_part = match.group(1)
        # Remove OrderBy suffix
        order_match = re.search(r"OrderBy([A-Za-z]+?)(?:Asc|Desc)$", conditions_part)
        if order_match:
            fields.append({
                "field": order_match.group(1),
                "snake_field": _camel_to_snake(order_match.group(1)),
                "access_type": "READ_ORDER",
            })
            conditions_part = re.sub(r"OrderBy[A-Za-z]+(?:Asc|Desc)?$", "", conditions_part)

        # Split by And/Or
        field_names = re.split(r"And|Or", conditions_part)
        for f in field_names:
            if f and not f.startswith(("Top", "Distinct")):
                fields.append({
                    "field": f,
                    "snake_field": _camel_to_snake(f),
                    "access_type": op,
                })

    return fields if fields else [{"field": "*", "snake_field": "*", "access_type": op}]


def _infer_operation(method_name: str) -> str:
    """Infer DB operation type from Spring Data method name."""
    name_lower = method_name.lower()
    if name_lower.startswith(("find", "get", "read", "count", "exists")):
        return "READ"
    if name_lower.startswith(("delete", "remove")):
        return "DELETE"
    if name_lower.startswith(("save", "insert", "create", "add")):
        return "WRITE"
    if name_lower.startswith(("update", "modify", "set")):
        return "UPDATE"
    return "UNKNOWN"


def query_db_field_impact(repo_path: str, store: Any, table_name: str, group: str | None = None) -> dict:
    """Query which top-level entries (pages, scheduled tasks) access a given DB table/field.

    Supports both Spring Data JPA (@Table + Repository) and MyBatis (XML mappers + DAO).

    Args:
        repo_path: Repository path.
        store: GraphStore instance.
        table_name: Database table name (e.g., "team_members").
        group: Optional group name for cross-repo frontend page matching.

    Returns:
        Dict with table, table_level, fields impact data.
    """
    # 1. Find table → entity/mapper mapping
    # Try Spring Data JPA (@Table) first
    tables_raw = store.query(
        "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
        "WHERE a.name = 'Table' "
        "RETURN c.name AS className, a.attributes AS attrs"
    )
    table_to_entity: dict[str, str] = {}
    for t in tables_raw:
        attrs = _parse_attrs(t.get("attrs"))
        tbl_name = attrs.get("path", attrs.get("name", ""))
        if tbl_name:
            table_to_entity[tbl_name] = t["className"]

    is_mybatis = False
    mapper_name: str | None = None

    if table_name in table_to_entity:
        # Spring Data JPA path
        entity_class = table_to_entity[table_name]
        entity_short = entity_class.split(".")[-1]
    else:
        # MyBatis fallback: scan XML mapper files
        mybatis_tables = _scan_mybatis_tables(repo_path)
        if table_name in mybatis_tables:
            is_mybatis = True
            mapper_name = mybatis_tables[table_name]
            entity_class = ""
            entity_short = ""
        else:
            available = sorted(table_to_entity.keys())
            if mybatis_tables:
                available.extend(sorted(mybatis_tables.keys()))
            return {"error": f"Table '{table_name}' not found in knowledge graph. Available: {available}"}

    # 2. Find Repositories/Mappers
    if is_mybatis:
        # MyBatis: use Mapper interface directly
        related_repos = [mapper_name] if mapper_name else []
    else:
        # Spring Data JPA: find Repositories by entity name
        repos = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Interface) "
            "WHERE a.name = 'Repository' "
            "RETURN c.name AS name, c.filePath AS filePath"
        )
        if not repos:
            repos = store.query(
                "MATCH (c:Interface) RETURN c.name AS name, c.filePath AS filePath"
            )
        related_repos = []
        for r in repos:
            repo_name = r["name"]
            repo_base = repo_name.split(".")[-1].replace("Repository", "")
            if repo_base == entity_short or entity_short.startswith(repo_base):
                related_repos.append(repo_name)

        if not related_repos:
            entity_pkg = ".".join(entity_class.split(".")[:-1])
            for r in repos:
                if r["filePath"] and entity_pkg.replace(".", "/") in r["filePath"]:
                    related_repos.append(r["name"])

    # 3. Get all methods from related repos/mappers
    repo_methods = []
    for repo in related_repos:
        simple = repo.split(".")[-1]  # e.g., "TeamMemberRepository" or "AdminUserMapper"
        methods = store.query(
            "MATCH (m:Method) WHERE m.className = $name "
            "RETURN m.name AS methodName",
            {"name": simple},
        )
        for m in methods:
            method_name = m["methodName"]
            if is_mybatis:
                fields = _infer_mybatis_fields(store, simple, method_name)
            else:
                fields = _infer_fields_from_method(method_name)
            repo_methods.append({
                "repository": repo,
                "method": method_name,
                "operation": _infer_operation(method_name),
                "fields": fields,
            })

    # 4. Trace upward: which Services call these repo methods?
    svc_repo_map: dict[str, list[dict]] = {}  # repo_method -> list of callers
    for rm in repo_methods:
        key = rm["method"]
        # Include repository/className filter to avoid cross-Mapper name collisions
        repo_simple = related_repos[0].split(".")[-1] if related_repos else ""
        svc_calls = store.query(
            "MATCH (svc:Method)-[r:CodeRelation]->(repo:Method) "
            "WHERE r.type = 'CALLS' AND repo.name = $mname "
            "AND repo.className = $repoName "
            "RETURN svc.className AS svcClass, svc.name AS svcMethod",
            {"mname": key, "repoName": repo_simple},
        )
        svc_repo_map.setdefault(key, []).extend(svc_calls)

    # 5. Find scheduled tasks
    scheduled_tasks = store.query(
        "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
        "WHERE a.name = 'Scheduled' "
        "RETURN m.className AS className, m.name AS methodName, a.attributes AS attrs"
    )
    scheduled_task_names = {(t["className"], t["methodName"]) for t in scheduled_tasks}

    # 6. Find Controller methods (API endpoints)
    ctrl_methods = store.query(
        "MATCH (m:Method) WHERE m.httpPath IS NOT NULL AND m.httpPath <> '' "
        "RETURN m.className AS className, m.name AS methodName, "
        "       m.httpMethod AS httpMethod, m.httpPath AS httpPath"
    )

    # 7. Trace downward: for each API endpoint, find which repo methods it calls.
    # This gives precise per-endpoint → per-field mapping instead of the coarse
    # "all endpoints that reach any method on this table" approach.
    table_impact = {
        "table": table_name,
        "entityClass": entity_class,
        "repositoryMethods": repo_methods,
        "scheduledTasks": [],
        "apiEndpoints": [],
        "frontendPages": [],
    }

    # Build a set of repo/dao method names for this table
    repo_method_names = {rm["method"] for rm in repo_methods}
    repo_simple = related_repos[0].split(".")[-1] if related_repos else ""

    top_level_scheduled: set[tuple] = set()
    top_level_api: set[tuple] = set()

    # 7a. Find scheduled tasks that call repo methods for this table
    for st in scheduled_tasks:
        svc_class = st["className"]
        svc_method = st["methodName"]
        # Check if this scheduled task calls any of our repo methods
        rows = store.query(
            "MATCH (m1:Method)-[r:CodeRelation]->(m2:Method) "
            "WHERE r.type = 'CALLS' AND m1.className = $cls AND m1.name = $meth "
            "AND m2.className = $repoName "
            "RETURN m2.name AS calledMethod",
            {"cls": svc_class, "meth": svc_method, "repoName": repo_simple},
        )
        for row in rows:
            if row["calledMethod"] in repo_method_names:
                top_level_scheduled.add((svc_class, svc_method))

    # 7b. Find API endpoints that call repo methods for this table
    # For each Controller method with httpPath, trace downward to find repo methods.
    # The graph stores CALLS as:
    #   Controller → Service Interface (CALLS)
    #   Service Impl → Mapper (CALLS)
    #   Service Impl → Service Interface (IMPLEMENTS, Class-level)
    # So we need a 3-hop path: Controller→Interface→Impl→Mapper
    api_to_methods: dict[tuple, set[str]] = {}  # (ctrl, method, hm, hp) -> set of repo methods
    for ctrl in ctrl_methods:
        ctrl_class = ctrl["className"]
        ctrl_method = ctrl["methodName"]
        hm = ctrl.get("httpMethod", "")
        hp = ctrl.get("httpPath", "")

        called = set()

        # Path 1: direct Controller → Mapper (1-hop)
        rows = store.query(
            "MATCH (ctrl:Method)-[r1:CodeRelation]->(m:Method) "
            "WHERE r1.type = 'CALLS' AND ctrl.className = $ctrlClass "
            "AND ctrl.name = $ctrlMethod AND m.className = $repoName "
            "RETURN m.name AS calledMethod",
            {"ctrlClass": ctrl_class, "ctrlMethod": ctrl_method, "repoName": repo_simple},
        )
        for row in rows:
            if row["calledMethod"] in repo_method_names:
                called.add(row["calledMethod"])

        # Path 2: Controller → Service Interface → Service Impl → Mapper (3-hop)
        # The graph stores:
        #   Controller.method --CALLS--> Interface.method (short className)
        #   Impl.method --CALLS--> Mapper.method (Impl className)
        #   But Interface has no CALLS to Mapper — must bridge via Impl.
        # Strategy: find Interface methods the Controller calls, then find
        # Impl methods with the same name in the same package's impl/ subdirectory.
        iface_calls = store.query(
            "MATCH (ctrl:Method)-[r1:CodeRelation]->(iface:Method) "
            "WHERE r1.type = 'CALLS' AND ctrl.className = $ctrlClass "
            "AND ctrl.name = $ctrlMethod "
            "AND iface.filePath IS NOT NULL AND iface.filePath <> '' "
            "RETURN iface.className AS ifaceClass, iface.name AS ifaceMethod, "
            "       iface.filePath AS ifaceFile",
            {"ctrlClass": ctrl_class, "ctrlMethod": ctrl_method},
        )
        for iface_row in iface_calls:
            iface_method = iface_row["ifaceMethod"]
            iface_file = iface_row.get("ifaceFile", "")
            # Derive Impl file path: replace /service/ with /service/impl/
            # and add "Impl" suffix to the filename.
            impl_file = ""
            if iface_file:
                last_slash = iface_file.rfind("/")
                if last_slash > 0:
                    dir_part = iface_file[:last_slash]
                    base_name = iface_file[last_slash + 1:]
                    # e.g., AdminUserService.java -> AdminUserServiceImpl.java
                    impl_base = base_name
                    if base_name.endswith(".java") and not base_name.endswith("Impl.java"):
                        impl_base = base_name[:-5] + "Impl.java"
                    # If already in impl/, just use the file
                    if "/impl/" in dir_part:
                        impl_file = dir_part + "/" + impl_base
                    else:
                        impl_file = dir_part + "/impl/" + impl_base
            if impl_file:
                # Find Impl method with same name
                mapper_rows = store.query(
                    "MATCH (impl:Method)-[r2:CodeRelation]->(m:Method) "
                    "WHERE r2.type = 'CALLS' "
                    "AND impl.filePath = $implFile AND impl.name = $ifaceMethod "
                    "AND m.className = $repoName "
                    "RETURN m.name AS calledMethod",
                    {"implFile": impl_file, "ifaceMethod": iface_method, "repoName": repo_simple},
                )
                for mr in mapper_rows:
                    if mr["calledMethod"] in repo_method_names:
                        called.add(mr["calledMethod"])

        # Path 3: direct 2-hop Controller → Service → Repository (Spring Data JPA)
        # For projects without Interface/Impl pattern, Service directly calls Repository.
        rows3 = store.query(
            "MATCH (ctrl:Method)-[r1:CodeRelation]->(svc:Method)-[r2:CodeRelation]->(m:Method) "
            "WHERE r1.type = 'CALLS' AND r2.type = 'CALLS' "
            "AND ctrl.className = $ctrlClass AND ctrl.name = $ctrlMethod "
            "AND m.className = $repoName "
            "RETURN m.name AS calledMethod",
            {"ctrlClass": ctrl_class, "ctrlMethod": ctrl_method, "repoName": repo_simple},
        )
        for row in rows3:
            if row["calledMethod"] in repo_method_names:
                called.add(row["calledMethod"])

        if called:
            api_to_methods[(ctrl_class, ctrl_method, hm, hp)] = called
            top_level_api.add((ctrl_class, ctrl_method, hm, hp))

    table_impact["scheduledTasks"] = [
        {"class": c, "method": m} for c, m in sorted(top_level_scheduled)
    ]
    table_impact["apiEndpoints"] = [
        {"controller": c, "method": m, "httpMethod": hm, "httpPath": hp}
        for c, m, hm, hp in sorted(top_level_api)
    ]

    # 7b. Bridge frontend pages via group or single-repo
    frontend_pages: list[dict] = []
    # Build a set of (httpMethod, httpPath) from matched API endpoints
    api_set = {(hm, hp) for _, _, hm, hp in top_level_api}

    if group:
        grp_result = query_frontend_pages_group(group)
        if "error" not in grp_result:
            for call in grp_result.get("apiCalls", []):
                key = (call.get("httpMethod", ""), call.get("httpPath", ""))
                if key in api_set:
                    frontend_pages.append({
                        "page": call.get("page", ""),
                        "httpMethod": key[0],
                        "httpPath": key[1],
                        "file": call.get("file", ""),
                    })
    else:
        # Single-repo: use scan fallback
        page_result = query_frontend_pages_single_repo(repo_path, store)
        for call in page_result.get("apiCalls", []):
            key = (call.get("httpMethod", ""), call.get("httpPath", ""))
            if key in api_set:
                frontend_pages.append({
                    "page": call.get("page", ""),
                    "httpMethod": key[0],
                    "httpPath": key[1],
                    "file": call.get("file", ""),
                })

    # Deduplicate frontend_pages
    seen_fp = set()
    unique_fp = []
    for fp in frontend_pages:
        key = (fp["page"], fp["httpMethod"], fp["httpPath"])
        if key not in seen_fp:
            seen_fp.add(key)
            unique_fp.append(fp)
    table_impact["frontendPages"] = unique_fp

    # Build a map: (httpMethod, httpPath) -> list of page names
    api_to_pages: dict[tuple[str, str], list[str]] = {}
    for fp in unique_fp:
        key = (fp["httpMethod"], fp["httpPath"])
        api_to_pages.setdefault(key, []).append(fp["page"])

    # 8. Build field-level impact using the per-endpoint → per-method mapping
    # from step 7 (api_to_methods). This gives precise field-level granularity:
    # each field is linked only to endpoints that actually call the method
    # accessing that field.

    # Reverse index: repo_method -> set of (ctrl, method, hm, hp)
    method_to_api: dict[str, set[tuple]] = {}
    for api_key, called_methods in api_to_methods.items():
        for m in called_methods:
            method_to_api.setdefault(m, set()).add(api_key)

    # Scheduled tasks per method (trace from scheduled task to repo methods)
    method_to_tasks: dict[str, set[tuple]] = {}
    for rm in repo_methods:
        method_key = rm["method"]
        for st_class, st_method in top_level_scheduled:
            rows = store.query(
                "MATCH (m1:Method)-[r:CodeRelation]->(m2:Method) "
                "WHERE r.type = 'CALLS' AND m1.className = $cls AND m1.name = $meth "
                "AND m2.className = $repoName AND m2.name = $repoMethod",
                {"cls": st_class, "meth": st_method, "repoName": repo_simple, "repoMethod": method_key},
            )
            if rows:
                method_to_tasks.setdefault(method_key, set()).add((st_class, st_method))

    field_map: dict[str, dict] = {}
    wildcard_accessed_by: list[dict] = []
    for rm in repo_methods:
        method_key = rm["method"]
        method_api = method_to_api.get(method_key, set())
        method_tasks = method_to_tasks.get(method_key, set())

        for fi in rm["fields"]:
            snake = fi["snake_field"]
            # Wildcard (*) means "all fields" — track separately at table level
            if snake == "*":
                for svc_class, svc_method in method_tasks:
                    wildcard_accessed_by.append({
                        "type": "scheduled_task",
                        "class": svc_class,
                        "method": svc_method,
                    })
                for api_key in method_api:
                    ctrl_class, ctrl_method, hm, hp = api_key
                    wildcard_accessed_by.append({
                        "type": "api_endpoint",
                        "controller": ctrl_class,
                        "method": ctrl_method,
                        "httpMethod": hm,
                        "httpPath": hp,
                        "frontendPages": api_to_pages.get((hm, hp), []),
                    })
                continue

            if snake not in field_map:
                field_map[snake] = {
                    "field": snake,
                    "accessed_by": [],
                    "operations": set(),
                }
            field_map[snake]["operations"].add(fi["access_type"])

            for svc_class, svc_method in method_tasks:
                field_map[snake]["accessed_by"].append({
                    "type": "scheduled_task",
                    "class": svc_class,
                    "method": svc_method,
                })

            for api_key in method_api:
                ctrl_class, ctrl_method, hm, hp = api_key
                field_map[snake]["accessed_by"].append({
                    "type": "api_endpoint",
                    "controller": ctrl_class,
                    "method": ctrl_method,
                    "httpMethod": hm,
                    "httpPath": hp,
                    "frontendPages": api_to_pages.get((hm, hp), []),
                })

    # Deduplicate field accessed_by
    def _deduplicate_accessed_by(accessed_by: list[dict]) -> list[dict]:
        seen = set()
        unique = []
        for item in accessed_by:
            key_parts = []
            for k, v in sorted(item.items()):
                if isinstance(v, list):
                    key_parts.append((k, tuple(v)))
                else:
                    key_parts.append((k, v))
            key = tuple(key_parts)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    fields_result = {}
    for snake, info in field_map.items():
        seen = set()
        unique = []
        for item in info["accessed_by"]:
            # Build hashable key excluding frontendPages list
            key_parts = []
            for k, v in sorted(item.items()):
                if isinstance(v, list):
                    key_parts.append((k, tuple(v)))
                else:
                    key_parts.append((k, v))
            key = tuple(key_parts)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        fields_result[snake] = {
            "field": snake,
            "accessed_by": unique,
            "operations": sorted(info["operations"]),
        }

    return {
        "table": table_name,
        "entityClass": entity_class,
        "table_level": table_impact,
        "fields": fields_result,
        "all_fields_operations": _deduplicate_accessed_by(wildcard_accessed_by),
    }
