"""Web dashboard: Starlette ASGI app serving PyGitNexus knowledge graph data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from ..graph.store import GraphStore
from ..mcp.server import (
    _DEFAULT_REL_TYPES,
    _VALID_REL_TYPES,
    _impact_bfs,
    _load_store,
    _resolve_db_path,
)
from ..search.query import query as graph_query, symbol_context, run_cypher
from ..storage.repo_manager import list_repos
from .frontend_mapper import (
    query_db_chain,
    query_db_field_impact,
    query_frontend_pages_single_repo,
    query_frontend_pages_group,
)

# ─── Pluggable scheduled task detectors ───────────────────────────────
# Each detector is a dict with:
#   - name: human-readable name
#   - annotation: annotation name to match (or None for method-name pattern)
#   - method_pattern: optional regex to match method names
#   - parse_attrs: function to extract schedule info from annotation attributes

_TASK_DETECTORS: list[dict] = [
    {
        "name": "Spring @Scheduled",
        "annotation": "Scheduled",
        "method_pattern": None,
    },
    {
        "name": "Quartz @ScheduledMethod",
        "annotation": "ScheduledMethod",
        "method_pattern": None,
    },
    {
        "name": "Spring @Async (potential background task)",
        "annotation": "Async",
        "method_pattern": None,
    },
    {
        "name": "Method name patterns",
        "annotation": None,
        "method_pattern": r"^(schedule|cron|timer|periodic|recurring)",
    },
]

# HTML template is read at module load time
_DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text()
_VIZ_DEMO_HTML = (Path(__file__).parent / "viz_demo.html").read_text()


def _ok(data: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data})


def _err(msg: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status_code)


def _get_repo_param(request: Request) -> str | None:
    repo = request.query_params.get("repo")
    if repo:
        return repo
    import os
    return os.environ.get("PYGITNEXUS_WEB_REPO")


# ─── Frontend path fuzzy matching helpers ─────────────────────────────

# Common frontend-only path prefixes to strip when matching backend APIs
_FE_PREFIXES_TO_STRIP = ["/user/", "/client/", "/app/", "/mobile/", "/v1/", "/api/", "/api/v1/"]

# Singular ↔ plural path normalization pairs
_SINGULAR_PLURAL = {
    "/order": "/orders",
    "/orders": "/order",
    "/address": "/addresses",
    "/addresses": "/address",
    "/item": "/items",
    "/items": "/item",
    "/product": "/products",
    "/products": "/product",
}


def _normalize_path(path: str) -> str:
    """Canonicalize a path for matching: strip prefixes, singularize."""
    p = path
    for prefix in _FE_PREFIXES_TO_STRIP:
        if p.startswith(prefix):
            p = p[len(prefix) - 1:]  # keep leading /
            break
    p = p.rstrip("/")
    # Always singularize: map plural back to singular for canonical comparison
    for sing, plur in _SINGULAR_PLURAL.items():
        if p == plur or p.startswith(plur + "/"):
            p = sing + p[len(plur):]
            break
    return p


def _match_fe_call_to_backend(fe_method: str, fe_path: str, be_method: str, be_path: str) -> bool:
    """Check if a frontend CALL matches a backend API node via fuzzy matching."""
    if fe_method != be_method:
        return False
    # Exact match
    if fe_path == be_path:
        return True
    # Canonical normalized match (both sides)
    if _normalize_path(fe_path) == _normalize_path(be_path):
        return True
    # Suffix match: backend path is suffix of frontend path (e.g. fe=/user/login, be=/login)
    if fe_path.endswith(be_path) and be_path.startswith("/"):
        return True
    # Suffix + normalized: frontend suffix matches normalized backend
    if fe_path.endswith(_normalize_path(be_path)) and be_path.startswith("/"):
        return True
    return False


def _query_frontend_pages(fe_store: GraphStore, backend_method: str, backend_path: str) -> list[dict]:
    """Query frontend repo for CALLS relations that fuzzy-match a backend API.

    Frontend KuzuDB stores HTTP calls as CALLS relations with httpMethod/httpPath
    properties on the relation, not USES_ENDPOINT relations to API nodes.
    """
    # Fetch all CALLS relations that have HTTP info
    fe_calls = fe_store.query(
        "MATCH (page:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
        "WHERE r.httpMethod IS NOT NULL AND r.httpPath IS NOT NULL "
        "RETURN page.name AS pageName, page.filePath AS pageFile, "
        "       r.httpMethod AS feMethod, r.httpPath AS fePath LIMIT 50"
    )
    results = []
    for fc in fe_calls:
        fe_m = fc.get("feMethod", "")
        fe_p = fc.get("fePath", "")
        if fe_m and fe_p and _match_fe_call_to_backend(fe_m, fe_p, backend_method, backend_path):
            # Extract human-readable page name from file path
            file_path = fc.get("pageFile", "")
            if file_path:
                # Use the filename as page name (e.g., "Login.vue")
                display_page_name = Path(file_path).name
            else:
                display_page_name = fc["pageName"]
            results.append({
                "pageName": display_page_name,
                "pageFile": file_path,
                "feMethod": fc["pageName"],  # original function name
                "pageClass": "",
                "httpMethod": backend_method,
                "httpPath": backend_path,
            })
    return results


# ─── Routes ───────────────────────────────────────────────────────────

async def dashboard(request: Request) -> HTMLResponse:
    return HTMLResponse(_DASHBOARD_HTML)


async def viz_demo(request: Request) -> HTMLResponse:
    return HTMLResponse(_VIZ_DEMO_HTML)


async def api_list_repos(request: Request) -> JSONResponse:
    try:
        repos = list_repos()
        data = [
            {
                "name": r.name,
                "path": r.path,
                "language": r.language,
                "indexed_at": r.indexed_at,
                "stats": r.stats or {},
            }
            for r in repos
        ]
        return _ok(data)
    except Exception as e:
        return _err(str(e))


async def api_stats(request: Request) -> JSONResponse:
    group_param = request.query_params.get("group", "")
    repo = _get_repo_param(request)

    if group_param:
        return _stats_group(group_param)

    if not repo:
        return _err("No repository or group selected")

    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        node_counts = {}
        for t in ["File", "Folder", "Class", "Interface", "Method", "Field", "API"]:
            result = store.query(f"MATCH (n:{t}) RETURN count(n) AS cnt")
            node_counts[t.lower()] = result[0]["cnt"] if result else 0

        rel_result = store.query(
            "MATCH ()-[r:CodeRelation]->() RETURN r.type AS type, count(r) AS cnt"
        )
        relations = {r["type"]: r["cnt"] for r in rel_result}

        return _ok({"nodes": node_counts, "relations": relations})
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


def _get_group_info(group_name: str):
    """Get group info from new group.yaml storage, falling back to legacy registry.json."""
    # Try new group.yaml system first
    from ..core.group.storage import get_group_dir, list_groups as list_group_dirs
    from ..core.group.config_parser import parse_group_yaml
    from ..storage.repo_manager import get_repo, get_group as get_legacy_group

    available = list_group_dirs()
    if group_name in available:
        group_dir = get_group_dir(group_name)
        try:
            config = parse_group_yaml(os.path.join(group_dir, "group.yaml"))
        except Exception:
            pass
        else:
            # Convert GroupConfig to a GroupInfo-like object
            repos = []
            for role, reg_name in config.repos.items():
                repo = get_repo(reg_name)
                if repo:
                    repos.append(type("Repo", (), {"name": reg_name, "path": repo.path, "role": role})())
                else:
                    repos.append(type("Repo", (), {"name": reg_name, "path": "", "role": role})())
            return type("Group", (), {"name": config.name, "label": config.description or config.name, "repos": repos})()

    # Fallback to legacy registry.json
    return get_legacy_group(group_name)


def _auto_detect_group(repo_path: str):
    """Find a group that contains the given repo and has a frontend role."""
    from ..core.group.storage import get_group_dir, list_groups as list_group_dirs
    from ..core.group.config_parser import parse_group_yaml
    from ..storage.repo_manager import get_repo

    for group_name in list_group_dirs():
        group = _get_group_info(group_name)
        if group:
            for r in group.repos:
                if r.path and repo_path and (r.path == repo_path or repo_path.startswith(r.path)):
                    # Check if group has a frontend repo
                    has_frontend = any(fe.role == "frontend" for fe in group.repos)
                    if has_frontend:
                        return group
    return None


def _stats_group(group_name: str) -> JSONResponse:
    group = _get_group_info(group_name)
    if group is None:
        return _err(f"Group '{group_name}' not found")

    backend_repos = [r for r in group.repos if r.role == "backend"]
    total_nodes: dict[str, int] = {}
    total_rels: dict[str, int] = {}

    for repo_info in backend_repos:
        db_path = Path(repo_info.path) / ".pygitnexus" / "kuzu"
        if not db_path.exists():
            continue
        store = GraphStore(db_path)
        try:
            for t in ["File", "Folder", "Class", "Interface", "Method", "Field", "API"]:
                result = store.query(f"MATCH (n:{t}) RETURN count(n) AS cnt")
                cnt = result[0]["cnt"] if result else 0
                key = t.lower()
                total_nodes[key] = total_nodes.get(key, 0) + cnt

            rel_result = store.query(
                "MATCH ()-[r:CodeRelation]->() RETURN r.type AS type, count(r) AS cnt"
            )
            for r in rel_result:
                total_rels[r["type"]] = total_rels.get(r["type"], 0) + r["cnt"]
        except Exception:
            pass
        finally:
            store.close()

    return _ok({"nodes": total_nodes, "relations": total_rels})


async def api_query(request: Request) -> JSONResponse:
    """Search symbols — supports single-repo and group (cross-repo) mode.

    In group mode, searches all backend repos' knowledge graphs and
    also scans frontend repo files for Vue/JS/TS symbols.
    """
    keyword = request.query_params.get("q", "")
    if not keyword:
        return _err("Missing 'q' parameter")
    limit = int(request.query_params.get("limit", "50"))
    group_param = request.query_params.get("group", "")

    if group_param:
        return _query_group(keyword, limit, group_param)

    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        results = graph_query(store, keyword, limit=limit)
        return _ok(results)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


def _query_group(keyword: str, limit: int, group_name: str) -> JSONResponse:
    """Search across all repos in a group — backend graph + frontend scan."""
    group = _get_group_info(group_name)
    if group is None:
        return _err(f"Group '{group_name}' not found")

    all_results: list[dict] = []
    stores: list[GraphStore] = []

    # Search backend repos
    backend_repos = [r for r in group.repos if r.role == "backend"]
    if not backend_repos:
        return _err(f"Group '{group_name}' has no backend repo")

    for repo_info in backend_repos:
        db_path = Path(repo_info.path) / ".pygitnexus" / "kuzu"
        if not db_path.exists():
            continue
        store = GraphStore(db_path)
        stores.append(store)
        try:
            results = graph_query(store, keyword, limit=limit)
            for r in results:
                r["source"] = repo_info.name
                r["sourceRole"] = "backend"
            all_results.extend(results)
        except Exception:
            # Skip repos with schema differences or corrupted DBs
            pass

    # Close backend stores
    for s in stores:
        s.close()

    # Scan frontend repo for symbols (components, functions, API calls)
    frontend_repos = [r for r in group.repos if r.role == "frontend"]
    if frontend_repos:
        fe_repo = frontend_repos[0]
        fe_symbols = _scan_frontend_symbols(fe_repo.path, keyword, limit)
        for s in fe_symbols:
            s["source"] = fe_repo.name
            s["sourceRole"] = "frontend"
        all_results.extend(fe_symbols)

    # Deduplicate by (name, type, filePath) and sort
    seen = set()
    deduped: list[dict] = []
    for r in all_results:
        key = (r.get("name", ""), r.get("types", ""), r.get("filePath", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # Limit total results
    deduped = deduped[:limit]

    return _ok(deduped)


def _scan_frontend_symbols(frontend_path: str, keyword: str, limit: int) -> list[dict]:
    """Scan Vue/JS/TS files for symbols matching the keyword.

    Returns results compatible with graph_query output format:
    { types, name, className, filePath, startLine, source, sourceRole }

    Scans for:
    - File names (e.g., Cart.vue)
    - Vue component names (name: 'XxxPage')
    - Exported functions/variables
    - API call paths (axios.get, fetch, etc.)
    - Import statements
    - Router path definitions (path: '/login' -> Login.vue)
    - Dynamic Vue page imports (import('@/views/Login.vue'))
    - Vue <template> text content
    """
    import re
    from pathlib import Path as _Path

    root = _Path(frontend_path)
    if not root.is_dir():
        return []

    results: list[dict] = []
    kw_lower = keyword.lower()
    extensions = {".vue", ".js", ".ts", ".jsx", ".tsx"}
    skip_dirs = {"node_modules", "dist", "build", "vendor", ".git"}

    # Pass 1: Scan all files for standard patterns
    for ext in extensions:
        for fp in root.rglob(f"*{ext}"):
            if any(skip in str(fp) for skip in skip_dirs):
                continue

            stem = fp.stem
            rel_path = str(fp.relative_to(root))

            # Match filename (e.g., Cart.vue → matches "Cart", "cart")
            if kw_lower in stem.lower():
                results.append({
                    "types": "File",
                    "name": stem + ext,
                    "className": "",
                    "filePath": rel_path,
                    "startLine": None,
                })

            try:
                content = fp.read_text(errors="replace")
            except (OSError, PermissionError):
                continue

            lines = content.splitlines()

            for i, line_text in enumerate(lines, 1):
                # Match Vue component names: export default { name: 'XxxPage' }
                for m in re.finditer(
                    r"""name\s*:\s*['"]([^'"]+)['"]""", line_text,
                ):
                    component_name = m.group(1)
                    if kw_lower in component_name.lower():
                        results.append({
                            "types": "Component",
                            "name": component_name,
                            "className": "",
                            "filePath": rel_path,
                            "startLine": i,
                        })

                # Match exported functions: export const xxx = (...) => or export function xxx
                for m in re.finditer(
                    r"""export\s+(?:const|function|let|var)\s+(\w+)""", line_text,
                ):
                    func_name = m.group(1)
                    if kw_lower in func_name.lower():
                        results.append({
                            "types": "Function",
                            "name": func_name,
                            "className": "",
                            "filePath": rel_path,
                            "startLine": i,
                        })

                # Match API call paths: axios.get('/api/xxx'), fetch('/api/xxx')
                for m in re.finditer(
                    r"""(?:axios|request|http|api|client)\.(?:get|post|put|delete|patch|request)\s*\(\s*['"`]([^'"`]+)['"`]""",
                    line_text, re.IGNORECASE,
                ):
                    api_path = m.group(1)
                    if kw_lower in api_path.lower():
                        page_name = fp.stem
                        results.append({
                            "types": "APIPath",
                            "name": api_path,
                            "className": page_name,
                            "filePath": rel_path,
                            "startLine": i,
                        })

                # Match import statements with keyword
                for m in re.finditer(
                    r"""import\s+.*?['"]([^'"]+)['"]""", line_text,
                ):
                    import_path = m.group(1)
                    if kw_lower in import_path.lower():
                        results.append({
                            "types": "Import",
                            "name": import_path,
                            "className": "",
                            "filePath": rel_path,
                            "startLine": i,
                        })

    # Pass 2: Parse router files for route path → page mappings
    # Look for files named router/index.js, router.js, routes.js, etc.
    router_patterns = ["**/router/index.js", "**/router/index.ts",
                       "**/router.js", "**/router.ts",
                       "**/routes.js", "**/routes.ts"]
    for pattern in router_patterns:
        for router_file in root.glob(pattern):
            if not router_file.is_file():
                continue
            try:
                router_content = router_file.read_text(errors="replace")
            except (OSError, PermissionError):
                continue

            # Extract route definitions: { path: '/login', name: 'login', component: () => import('@/views/Login.vue') }
            # Match path + component pairs on same or nearby lines
            route_blocks = re.findall(
                r"path\s*:\s*['\"]([^'\"]+)['\"]"
                r".*?"
                r"(?:name\s*:\s*['\"]([^'\"]+)['\"])?"
                r".*?"
                r"(?:"
                    r"component\s*:\s*\(\)\s*=>\s*import\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
                    r"|"
                    r"component\s*:\s*(\w+)"
                r")",
                router_content,
                re.DOTALL | re.IGNORECASE,
            )
            for path_val, name_val, dynamic_import, static_component in route_blocks:
                # Determine the component file from dynamic import or static reference
                component_file = dynamic_import or static_component
                if component_file:
                    # Resolve alias like @/views/Login.vue → views/Login.vue
                    resolved_path = component_file.replace("@/", "").replace("~/", "")
                    # Extract page name from component path
                    page_name = _Path(resolved_path).stem

                    # Match keyword against route path, route name, or page name
                    searchable_values = [path_val, name_val or "", page_name, resolved_path]
                    if any(kw_lower in v.lower() for v in searchable_values):
                        results.append({
                            "types": "Page",
                            "name": f"{page_name} ({path_val})",
                            "className": name_val or "",
                            "filePath": str(router_file.relative_to(root)),
                            "startLine": None,
                        })

    # Pass 3: Scan for dynamic Vue page imports across all JS/TS files
    # Pattern: import('@/views/Login.vue') or import("../views/Login.vue")
    for ext in {".js", ".ts", ".jsx", ".tsx"}:
        for fp in root.rglob(f"*{ext}"):
            if any(skip in str(fp) for skip in skip_dirs):
                continue
            try:
                content = fp.read_text(errors="replace")
            except (OSError, PermissionError):
                continue
            rel_path = str(fp.relative_to(root))

            for m in re.finditer(
                r"""import\s*\(\s*['"]([^'"']*?/([^/'"]+)\.vue)['"]\s*\)""", content,
            ):
                full_import = m.group(1)
                vue_file = m.group(2)
                if kw_lower in vue_file.lower() or kw_lower in full_import.lower():
                    results.append({
                        "types": "Page",
                        "name": f"{vue_file} (via {full_import})",
                        "className": "",
                        "filePath": rel_path,
                        "startLine": None,
                    })

    # Pass 4: Scan Vue <template> sections for keyword in text content
    for fp in root.rglob("*.vue"):
        if any(skip in str(fp) for skip in skip_dirs):
            continue
        try:
            content = fp.read_text(errors="replace")
        except (OSError, PermissionError):
            continue
        rel_path = str(fp.relative_to(root))

        # Extract <template>...</template> content
        template_match = re.search(r"<template[^>]*>(.*?)</template>", content, re.DOTALL)
        if template_match:
            template_content = template_match.group(1)
            # Check for keyword in visible text (strip HTML tags for text matching)
            text_content = re.sub(r"<[^>]+>", " ", template_content)
            if kw_lower in text_content.lower():
                # Find the line number where keyword appears
                line_num = None
                for li, lt in enumerate(template_content.splitlines(), 1):
                    if kw_lower in lt.lower():
                        line_num = li
                        break
                stem = fp.stem
                results.append({
                    "types": "Page",
                    "name": f"{stem} (template content)",
                    "className": "",
                    "filePath": rel_path,
                    "startLine": line_num,
                })

    return results

async def api_symbol(request: Request) -> JSONResponse:
    name = request.query_params.get("name", "")
    if not name:
        return _err("Missing 'name' parameter")
    class_param = request.query_params.get("class", "")
    repo = _get_repo_param(request)
    store, repo_root = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    # Auto-detect group for frontend repo querying (same as api_mindmap)
    group_param = ""
    if repo_root:
        auto_group = _auto_detect_group(str(repo_root))
        if auto_group:
            group_param = auto_group.name
    try:
        # Parse "ClassName.methodName" format for precise filtering
        method_name = name
        class_filter = ""
        if "." in name:
            parts = name.rsplit(".", 1)
            class_filter = parts[0]
            method_name = parts[1]

        # Use class_param as fallback for class_filter
        if not class_filter and class_param:
            class_filter = class_param

        # Use the same approach as api_mindmap: find the exact method first
        if class_filter:
            rows = store.query(
                "MATCH (n:Method) WHERE n.name = $name AND n.className CONTAINS $cls "
                "RETURN n.name AS name, n.className AS className LIMIT 5",
                {"name": method_name, "cls": class_filter},
            )
        else:
            rows = store.query(
                "MATCH (n:Method) WHERE n.name = $name "
                "RETURN n.name AS name, n.className AS className LIMIT 5",
                {"name": method_name},
            )
        if not rows:
            # Fall back to generic symbol_context (for non-Method symbols)
            ctx = symbol_context(store, name)
            return _ok(ctx)

        # Prefer ServiceImpl, then Service, then exact class_filter match
        row = rows[0]
        for r in rows:
            if "ServiceImpl" in r["className"] or "ServiceImpl" in (r.get("className") or ""):
                row = r
                break
        else:
            for r in rows:
                if "Service" in r["className"] and "Impl" not in r.get("className", ""):
                    row = r
                    break
            else:
                # Fallback: exact class_filter match
                for r in rows:
                    if r["className"] == class_filter:
                        row = r
                        break

        actual_class = row["className"]
        actual_name = row["name"]

        # Check if this is an Impl class → find Interface via IMPLEMENTS
        impl_iface = None
        impl_iface_short = None
        iface_rows = store.query(
            "MATCH (c:Class)-[r]->(t) "
            "WHERE c.name CONTAINS $cls AND r.type = 'IMPLEMENTS' RETURN t.name AS ifaceName LIMIT 1",
            {"cls": actual_class},
        )
        if iface_rows:
            impl_iface = iface_rows[0]["ifaceName"]
            impl_iface_short = impl_iface.split(".")[-1]

        # Query callers/callees with precise class+name filtering (same as api_mindmap)
        # CALLS callers: methods that call this method directly
        callers = store.query(
            "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
            "WHERE target.name = $method AND target.className = $cls "
            "RETURN caller.name AS caller, caller.className AS callerClass, r.confidence AS confidence",
            {"method": actual_name, "cls": actual_class},
        )

        # If target is an Impl, also find callers that call via the Interface
        iface_callers = []
        if impl_iface_short:
            iface_callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                "WHERE target.name = $method AND target.className = $iface "
                "RETURN caller.name AS caller, caller.className AS callerClass, r.confidence AS confidence",
                {"method": actual_name, "iface": impl_iface_short},
            )

        # USES_ENDPOINT callers (frontend pages) — include file path and API info
        frontend_callers = store.query(
            "MATCH (caller:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
            "<-[r2:CodeRelation {type: 'EXPOSES'}]-(target:Method) "
            "WHERE target.name = $method AND target.className = $cls "
            "RETURN caller.name AS caller, caller.className AS callerClass, "
            "       caller.filePath AS callerFile, "
            "       api.httpMethod AS httpMethod, api.httpPath AS httpPath, "
            "       r1.confidence AS confidence",
            {"method": actual_name, "cls": actual_class},
        )

        # Three-hop: HTML pages → USES_ENDPOINT → API ← EXPOSES ← Controller → CALLS → Interface
        ep_two_hop = store.query(
            "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
            "<-[r2:CodeRelation {type: 'EXPOSES'}]-(ctrl:Method)-[r3:CodeRelation {type: 'CALLS'}]->(iface:Method) "
            "WHERE iface.name = $method AND iface.className = $iface "
            "RETURN page.name AS caller, page.className AS callerClass, "
            "       page.filePath AS callerFile, "
            "       ctrl.name AS ctrlName, ctrl.className AS ctrlClass, "
            "       api.httpMethod AS httpMethod, api.httpPath AS httpPath, "
            "       r1.confidence AS confidence",
            {"method": actual_name, "iface": impl_iface_short or actual_class},
        )

        # Also query frontend repo for USES_ENDPOINT connections (group mode, same as api_mindmap)
        if group_param and impl_iface_short:
            group = _get_group_info(group_param)
            if group:
                for fe_repo in group.repos:
                    if fe_repo.role == "frontend":
                        fe_db = Path(fe_repo.path) / ".pygitnexus" / "kuzu"
                        if fe_db.exists():
                            try:
                                fe_store = GraphStore(fe_db)
                                # Get all API nodes exposed by Controllers that call the Interface
                                api_rows = store.query(
                                    "MATCH (ctrl:Method)-[r1:CodeRelation {type: 'EXPOSES'}]->(api:API), "
                                    "(ctrl:Method)-[r2:CodeRelation {type: 'CALLS'}]->(iface:Method) "
                                    "WHERE iface.className = $iface AND iface.name = $method "
                                    "RETURN api AS apiNode, ctrl.className AS ctrlClass, ctrl.name AS ctrlName LIMIT 20",
                                    {"iface": impl_iface_short, "method": actual_name},
                                )
                                for api_row in api_rows:
                                    api_node = api_row.get("apiNode", {})
                                    hm = api_node.get("httpMethod", "") if isinstance(api_node, dict) else ""
                                    hp = api_node.get("httpPath", "") if isinstance(api_node, dict) else ""
                                    ctrl_cls = api_row.get("ctrlClass", "")
                                    ctrl_name = api_row.get("ctrlName", "")
                                    if hm and hp:
                                        fe_matches = _query_frontend_pages(fe_store, hm, hp)
                                        for fm in fe_matches:
                                            ep_two_hop.append({
                                                "caller": fm["pageName"],
                                                "callerClass": "",
                                                "ctrlName": ctrl_name,
                                                "ctrlClass": ctrl_cls,
                                                "confidence": fm.get("confidence"),
                                                "feMethod": fm.get("feMethod", ""),
                                                "httpMethod": fm.get("httpMethod", ""),
                                                "httpPath": fm.get("httpPath", ""),
                                            })
                                fe_store.close()
                            except Exception:
                                pass

        # Build hierarchical callers
        # ECharts BT (bottom-to-top) renders children ABOVE parent.
        # We want to visually show: Login.vue(top) → POST /login → onSubmit → PersonalController.login(bottom)
        # Since children render above parent, we need: Controller(outer) → API → function → page(inner/leaf)
        page_map: dict[str, dict] = {}

        # Three-hop: build inverted tree Controller → API → function → page
        for fc in (ep_two_hop or []):
            ctrl_key = f"{fc['ctrlClass']}.{fc['ctrlName']}"
            caller_file = fc.get("callerFile", "")
            display_page = Path(caller_file).name if caller_file else fc["caller"]
            fe_method = fc.get("feMethod", "")
            http_method = fc.get("httpMethod", "")
            http_path = fc.get("httpPath", "")

            if ctrl_key not in page_map:
                page_map[ctrl_key] = {
                    "caller": fc["ctrlName"],
                    "callerClass": fc.get("ctrlClass", ""),
                    "confidence": fc.get("confidence"),
                    "relType": "CALLS (via Interface)",
                    "children": [],
                }

            ctrl_children = page_map[ctrl_key]["children"]

            # Inverted: Controller(outer) → API → function → page(inner/leaf)
            # ECharts BT will render page at top, Controller at bottom
            page_node = {
                "caller": display_page,
                "callerClass": "",
                "confidence": None,
                "relType": "USES_ENDPOINT",
                "children": [],
            }

            if http_method and http_path:
                api_label = f"{http_method} {http_path}"
                api_node = {
                    "caller": api_label,
                    "callerClass": "",
                    "confidence": None,
                    "relType": "EXPOSES",
                    "is_api": True,
                    "children": [],
                }

                # function → page (leaf)
                if fe_method and fe_method != display_page:
                    func_node = {
                        "caller": fe_method,
                        "callerClass": fc.get("callerClass"),
                        "confidence": None,
                        "relType": "CALLS",
                        "children": [page_node],
                    }
                    api_node["children"].append(func_node)
                else:
                    api_node["children"].append(page_node)

                # Controller → API
                if not any(ch.get("caller") == api_label for ch in ctrl_children):
                    ctrl_children.append(api_node)
            else:
                if not any(ch.get("caller") == display_page for ch in ctrl_children):
                    ctrl_children.append(page_node)

        # Direct USES_ENDPOINT callers — with page → function → API structure
        for fc in (frontend_callers or []):
            # Use file path to get display page name
            caller_file = fc.get("callerFile", "")
            display_name = Path(caller_file).name if caller_file else fc["caller"]
            fe_method = fc["caller"]
            page_key = display_name
            http_method = fc.get("httpMethod", "")
            http_path = fc.get("httpPath", "")

            # Check if already a child of any Controller
            already_child = any(
                any(ch.get("caller") == display_name for ch in pm.get("children", []))
                for pm in page_map.values()
            )
            if already_child:
                continue
            if page_key in page_map:
                continue

            page_map[page_key] = {
                "caller": display_name,
                "callerClass": fc.get("callerClass", ""),
                "confidence": fc.get("confidence"),
                "relType": "USES_ENDPOINT",
                "children": [],
            }

            # Add API node and function under page
            if http_method and http_path:
                api_label = f"{http_method} {http_path}"
                api_entry = {
                    "caller": api_label,
                    "callerClass": "",
                    "confidence": None,
                    "relType": "EXPOSES",
                    "is_api": True,
                    "children": [],
                }
                page_map[page_key]["children"].append(api_entry)

                if fe_method and fe_method != display_name:
                    api_entry["children"].append({
                        "caller": fe_method,
                        "callerClass": fc["callerClass"],
                        "confidence": None,
                        "relType": "CALLS",
                        "children": [],
                    })

        # Java CALLS callers (direct)
        for c in callers:
            key = f"{c['callerClass']}.{c['caller']}" if c.get('callerClass') else c['caller']
            already_in_page = any(
                any(ch.get("callerClass") == c.get("callerClass") and ch.get("caller") == c["caller"]
                    for ch in pm.get("children", []))
                for pm in page_map.values()
            )
            if not already_in_page and key not in page_map:
                page_map[key] = {
                    "caller": c["caller"],
                    "callerClass": c.get("callerClass", ""),
                    "confidence": c.get("confidence"),
                    "children": [],
                }

        # Java CALLS callers via Interface
        for c in (iface_callers or []):
            key = f"{c['callerClass']}.{c['caller']}" if c.get('callerClass') else c['caller']
            already_in_page = any(
                any(ch.get("callerClass") == c.get("callerClass") and ch.get("caller") == c["caller"]
                    for ch in pm.get("children", []))
                for pm in page_map.values()
            )
            if not already_in_page and key not in page_map:
                page_map[key] = {
                    "caller": c["caller"],
                    "callerClass": c.get("callerClass", ""),
                    "confidence": c.get("confidence"),
                    "children": [],
                }

        # MAPS_TO callers: MyBatis Mapper methods that map to this setter/getter
        maps_to_callers = store.query(
            "MATCH (caller:Method)-[r:CodeRelation {type: 'MAPS_TO'}]->(target:Method) "
            "WHERE target.name = $method AND target.className = $cls "
            "RETURN caller.name AS caller, caller.className AS callerClass, r.confidence AS confidence",
            {"method": actual_name, "cls": actual_class},
        )
        for c in (maps_to_callers or []):
            key = f"{c['callerClass']}.{c['caller']}" if c.get('callerClass') else c['caller']
            if key not in page_map:
                page_map[key] = {
                    "caller": c["caller"],
                    "callerClass": c.get("callerClass", ""),
                    "confidence": c.get("confidence"),
                    "relType": "MAPS_TO (MyBatis)",
                    "children": [],
                }

        callers_flat = list(page_map.values())

        # Callees: methods this method calls (with depth-2 expansion, same as api_mindmap)
        callees = store.query(
            "MATCH (source:Method)-[r:CodeRelation {type: 'CALLS'}]->(callee:Method) "
            "WHERE source.name = $method AND source.className = $cls "
            "RETURN callee.name AS callee, callee.className AS calleeClass, r.confidence AS confidence",
            {"method": actual_name, "cls": actual_class},
        )

        # If target is an Impl, also find callees from the Interface definition
        iface_callees = []
        if impl_iface_short:
            iface_callees = store.query(
                "MATCH (source:Method)-[r:CodeRelation {type: 'CALLS'}]->(callee:Method) "
                "WHERE source.name = $method AND source.className = $iface "
                "RETURN callee.name AS callee, callee.className AS calleeClass, r.confidence AS confidence",
                {"method": actual_name, "iface": impl_iface_short},
            )

        # Build callee tree with BFS expansion up to 30 hops
        callee_map: list[dict] = []
        seen_callees: set[str] = set()
        callee_lookup: dict[str, dict] = {}
        for c in (callees or []):
            key = f"{c['calleeClass']}.{c['callee']}"
            if key not in seen_callees:
                seen_callees.add(key)
                node = {"name": key, "via": "CALLS", "confidence": c.get("confidence"), "children": []}
                callee_map.append(node)
                callee_lookup[key] = node
        for c in (iface_callees or []):
            key = f"{c['calleeClass']}.{c['callee']}"
            if key not in seen_callees:
                seen_callees.add(key)
                node = {"name": key, "via": "CALLS", "confidence": c.get("confidence"), "children": []}
                callee_map.append(node)
                callee_lookup[key] = node

        # BFS expansion up to 30 hops
        _MAX_CALLEE_DEPTH = 30
        current_level = [c["name"] for c in callee_map]
        for _depth in range(2, _MAX_CALLEE_DEPTH + 1):
            if not current_level:
                break
            conditions = " OR ".join(
                f"(source.className = {json.dumps(k.rsplit('.', 1)[0])} AND source.name = {json.dumps(k.rsplit('.', 1)[1])})"
                for k in current_level
            )
            next_level: list[str] = []
            rows = store.query(
                f"MATCH (source:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(child:Method) "
                f"WHERE {conditions} "
                f"RETURN source.className AS srcClass, source.name AS srcName, "
                f"       child.name AS childName, child.className AS childClass"
            )
            for r in rows:
                parent_key = f"{r['srcClass']}.{r['srcName']}"
                child_key = f"{r['childClass']}.{r['childName']}"
                if parent_key in callee_lookup and child_key not in seen_callees:
                    seen_callees.add(child_key)
                    new_node = {"name": child_key, "via": "CALLS", "children": []}
                    callee_lookup[parent_key]["children"].append(new_node)
                    callee_lookup[child_key] = new_node
                    next_level.append(child_key)
            current_level = next_level

        # Expand Mapper → Table by inference (MyBatis mappers have no outgoing CALLS edges)
        for node in callee_map:
            if "Mapper" in node["name"] or "DAO" in node["name"]:
                parts = node["name"].rsplit(".", 1)
                if len(parts) == 2:
                    table_name = _infer_table_from_mapper(parts[0], parts[1])
                    if table_name:
                        if not any(ch["name"] == table_name for ch in node["children"]):
                            node["children"].append({
                                "name": table_name,
                                "via": "SQL (MyBatis)",
                                "children": [],
                            })

        # Imports
        imports = store.query(
            "MATCH (n)-[r:CodeRelation {type: 'IMPORTS'}]->(dep) "
            "WHERE n.name = $name AND n.className = $cls "
            "RETURN dep.name AS import, r.confidence AS confidence",
            {"name": actual_name, "cls": actual_class},
        )
        imports_flat = [{"import": i["import"], "confidence": i.get("confidence")} for i in (imports or [])]

        # Accesses (for Field nodes)
        accesses = store.query(
            "MATCH (accessor)-[r:CodeRelation {type: 'ACCESSES'}]->(target) "
            "WHERE target.name = $name AND target.className = $cls "
            "RETURN accessor.name AS accessor, accessor.className AS accessorClass, r.confidence AS confidence",
            {"name": method_name, "cls": actual_class},
        )
        accesses_flat = [{"accessor": a["accessor"], "accessorClass": a.get("accessorClass", ""), "confidence": a.get("confidence")} for a in (accesses or [])]

        ctx = {
            "callers": callers_flat,
            "callees": callee_map,
            "imports": imports_flat,
            "accesses": accesses_flat,
        }
        return _ok(ctx)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_cypher(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON body")
    cypher = body.get("query", "")
    if not cypher:
        return _err("Missing 'query' in body")
    repo = body.get("repo") or _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        results = run_cypher(store, cypher)
        return _ok(results)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_browse(request: Request) -> JSONResponse:
    node_type = request.query_params.get("type", "Class")
    page = max(1, int(request.query_params.get("page", "1")))
    limit = min(100, max(10, int(request.query_params.get("limit", "50"))))
    keyword = request.query_params.get("keyword", "").strip()
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        # KuzuDB doesn't support OFFSET/SKIP, so fetch all and paginate in memory.
        # File/Folder nodes don't have startLine; only code nodes (Class/Interface/Method/Field) do.
        has_start_line = node_type not in ("File", "Folder")
        start_line_clause = ", n.startLine AS startLine" if has_start_line else ""
        has_class_name = node_type in ("Method", "Field")
        class_name_clause = ", n.className AS className" if has_class_name else ""
        nodes = store.query(
            f"MATCH (n:{node_type}) RETURN n.id AS id, n.name AS name, n.filePath AS filePath{start_line_clause}{class_name_clause}, labels(n) AS nodeType ORDER BY n.name"
        )
        if not has_start_line:
            for node in nodes:
                node["startLine"] = None

        # Filter by keyword in name or filePath
        if keyword:
            kw_lower = keyword.lower()
            nodes = [n for n in nodes if kw_lower in n.get("name", "").lower() or kw_lower in (n.get("filePath") or "").lower()]

        total = len(nodes)
        offset = (page - 1) * limit
        page_items = nodes[offset:offset + limit]
        return _ok({
            "type": node_type,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit if total else 0,
            "items": page_items,
        })
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_explorer(request: Request) -> JSONResponse:
    """Return REST API endpoints grouped by controller."""
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        # Step 1: Find all controllers (classes with @RestController)
        ctrl_query = """
            MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class)
            WHERE a.name IN ['RestController', 'Controller']
            RETURN c.name AS ctrlName, c.filePath AS filePath
            ORDER BY c.name
        """
        controllers = store.query(ctrl_query)

        # Step 2: For each controller, get base path from @RequestMapping
        # and methods with HTTP annotations
        result = []
        for ctrl in controllers:
            ctrl_name = ctrl["ctrlName"]
            simple_name = ctrl_name.split(".")[-1]  # e.g. "AgentController"
            base_path = ""

            # Get base path from @RequestMapping on the class
            mp_query = f"""
                MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class)
                WHERE a.name = 'RequestMapping' AND c.name = '{ctrl_name.replace("'", "''")}'
                RETURN a.attributes AS attrs
                LIMIT 1
            """
            mp_result = store.query(mp_query)
            if mp_result:
                attrs = mp_result[0].get("attrs", {})
                if isinstance(attrs, str):
                    try:
                        import json
                        attrs = json.loads(attrs)
                    except (json.JSONDecodeError, TypeError):
                        attrs = {}
                base_path = attrs.get("path", "") if isinstance(attrs, dict) else ""

            # Get endpoints (methods with @GetMapping etc.)
            # Note: Method.className stores simple class name, not FQN
            ep_query = f"""
                MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method)
                WHERE a.name IN ['GetMapping','PostMapping','PutMapping','DeleteMapping','PatchMapping']
                  AND m.className = '{simple_name}'
                RETURN a.name AS httpAnn, a.attributes AS attrs, m.name AS methodName,
                       m.returnType AS returnType, m.isPublic AS isPublic
                ORDER BY m.name
            """
            endpoints = store.query(ep_query)

            endpoint_list = []
            for ep in endpoints:
                attrs = ep.get("attrs", {})
                if isinstance(attrs, str):
                    try:
                        import json
                        attrs = json.loads(attrs)
                    except (json.JSONDecodeError, TypeError):
                        attrs = {}
                ep_path = attrs.get("path", "") if isinstance(attrs, dict) else ""
                http_map = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
                            "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}
                method = http_map.get(ep["httpAnn"], ep["httpAnn"])
                full_path = base_path + ep_path if base_path else ep_path

                endpoint_list.append({
                    "method": method,
                    "path": full_path,
                    "methodName": ep["methodName"],
                    "returnType": ep.get("returnType", ""),
                    "isPublic": ep.get("isPublic", True),
                })

            result.append({
                "name": simple_name,
                "fullName": ctrl_name,
                "filePath": ctrl["filePath"],
                "basePath": base_path,
                "endpoints": endpoint_list,
            })

        return _ok(result)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_tree(request: Request) -> JSONResponse:
    """Return folder/file hierarchy as a tree structure."""
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        # Get all folders and files
        folders = store.query(
            "MATCH (n:Folder) RETURN n.id AS id, n.name AS name, n.filePath AS filePath ORDER BY n.filePath"
        )
        file_nodes = store.query(
            "MATCH (f:File) RETURN f.name AS name, f.filePath AS filePath ORDER BY f.name"
        )

        # Build a nested dict tree from paths
        tree_root: dict = {}

        # Insert all folder paths
        for f in folders:
            fp = f["filePath"]
            parts = Path(fp).parts
            current = tree_root
            for i, part in enumerate(parts):
                if part not in current:
                    sub_path = str(Path(*parts[:i + 1]))
                    current[part] = {
                        "name": part, "path": sub_path, "type": "folder",
                        "id": f"Folder_{sub_path}", "children": {},
                    }
                current = current[part]["children"]

        # Insert all file paths under their folder
        for fn in file_nodes:
            fp = fn.get("filePath", "")
            if not fp:
                continue
            parts = Path(fp).parts
            current = tree_root
            for part in parts[:-1]:
                if part not in current:
                    sub_path = str(Path(*[p for p in parts[:list(parts).index(part) + 1]]))
                    current[part] = {
                        "name": part, "path": sub_path, "type": "folder",
                        "id": f"Folder_{sub_path}", "children": {},
                    }
                current = current[part]["children"]
            file_name = parts[-1]
            if file_name not in current:
                current[file_name] = {
                    "name": file_name, "path": fp, "type": "file",
                    "id": f"File_{fp}",
                }

        # Collect code type counts per folder path
        folder_code_counts: dict[str, dict] = {}
        for node_type in ["Class", "Interface"]:
            rows = store.query(f"MATCH (n:{node_type}) RETURN n.filePath AS filePath")
            for r in rows:
                fp = r.get("filePath", "")
                if fp:
                    folder_path = str(Path(fp).parent)
                    counts = folder_code_counts.setdefault(folder_path, {})
                    counts[node_type.lower()] = counts.get(node_type.lower(), 0) + 1

        # Convert dict to nested list, merging code counts
        def to_list(node: dict, current_path: str = "") -> list:
            result = []
            for key, val in node.items():
                children = val.get("children", {})
                child_path = val.get("path", current_path)
                child_list = to_list(children, child_path) if children else []
                child_list.sort(key=lambda x: (x["type"] != "folder", x["name"]))
                item = {"id": val.get("id", key), "name": val["name"], "type": val["type"]}
                if val.get("path"):
                    item["filePath"] = val["path"]
                if val["type"] == "folder":
                    item["children"] = child_list
                    path_counts = folder_code_counts.get(val.get("path", ""), {})
                    if path_counts:
                        item["code_type_counts"] = path_counts
                result.append(item)
            return result

        tree = to_list(tree_root)
        return _ok(tree)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_scheduled(request: Request) -> JSONResponse:
    """Return scheduled/periodic tasks using pluggable detectors."""
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        all_tasks = []

        for detector in _TASK_DETECTORS:
            if detector["annotation"]:
                # Query by annotation
                rows = store.query(
                    "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
                    f"WHERE a.name = '{detector['annotation']}' "
                    "RETURN m.name AS methodName, m.className AS className, "
                    "       m.filePath AS filePath, a.name AS annName, a.attributes AS attrs "
                    "ORDER BY m.name"
                )
                for row in rows:
                    attrs = row.get("attrs", {})
                    if isinstance(attrs, str):
                        try:
                            attrs = json.loads(attrs)
                        except (json.JSONDecodeError, TypeError):
                            attrs = {}
                    if not isinstance(attrs, dict):
                        attrs = {}

                    if "cron" in attrs:
                        schedule_type = "cron"
                        schedule_expr = attrs["cron"]
                    elif "fixedRate" in attrs:
                        schedule_type = "fixedRate"
                        schedule_expr = attrs["fixedRate"]
                    elif "fixedDelay" in attrs:
                        schedule_type = "fixedDelay"
                        schedule_expr = attrs["fixedDelay"]
                    else:
                        schedule_type = detector["name"]
                        schedule_expr = str(attrs) if attrs else "detected"

                    all_tasks.append({
                        "methodName": row.get("methodName", ""),
                        "className": row.get("className", ""),
                        "filePath": row.get("filePath", ""),
                        "scheduleType": schedule_type,
                        "scheduleExpr": schedule_expr,
                        "initialDelay": attrs.get("initialDelay", attrs.get("fixedDelay", "")),
                        "detector": detector["name"],
                        "attributes": attrs,
                    })
            elif detector["method_pattern"]:
                # Query by method name pattern
                import re
                pattern = re.compile(detector["method_pattern"], re.IGNORECASE)
                all_methods = store.query(
                    "MATCH (m:Method) RETURN m.name AS methodName, m.className AS className, "
                    "       m.filePath AS filePath"
                )
                for row in all_methods:
                    if pattern.match(row.get("methodName", "")):
                        all_tasks.append({
                            "methodName": row["methodName"],
                            "className": row["className"],
                            "filePath": row.get("filePath", ""),
                            "scheduleType": "pattern",
                            "scheduleExpr": f"matched: {detector['name']}",
                            "initialDelay": "",
                            "detector": detector["name"],
                            "attributes": {},
                        })

        return _ok(all_tasks)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_architecture(request: Request) -> JSONResponse:
    """Return layered architecture data (Controller/Service/Repository)."""
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        # Get Controller classes via annotation
        controllers = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
            "WHERE a.name IN ['RestController','Controller'] "
            "RETURN c.name AS name, c.filePath AS filePath ORDER BY c.name"
        )
        controller_names = {c["name"] for c in controllers}

        # Get Service classes
        all_classes = store.query(
            "MATCH (c:Class) RETURN c.name AS name, c.filePath AS filePath"
        )
        service_names = set()
        for c in all_classes:
            fp = c.get("filePath", "") or ""
            if "/service/" in fp:
                service_names.add(c["name"])

        # Get Repository interfaces
        repos = store.query(
            "MATCH (c:Interface) RETURN c.name AS name, c.filePath AS filePath"
        )
        repo_names = set()
        for r in repos:
            fp = r.get("filePath", "") or ""
            if "/repository/" in fp:
                repo_names.add(r["name"])

        # Get method-level CALLS between layers
        ctrl_svc = store.query(
            "MATCH (m1:Method)-[r:CodeRelation]->(m2:Method) "
            "WHERE r.type = 'CALLS' AND m1.filePath CONTAINS '/controller/' "
            "AND m2.filePath CONTAINS '/service/' "
            "RETURN m1.className AS src, m2.className AS target, count(*) AS cnt "
            "ORDER BY cnt DESC"
        )
        svc_repo = store.query(
            "MATCH (m1:Method)-[r:CodeRelation]->(m2:Method) "
            "WHERE r.type = 'CALLS' AND m1.filePath CONTAINS '/service/' "
            "AND m2.filePath CONTAINS '/repository/' "
            "RETURN m1.className AS src, m2.className AS target, count(*) AS cnt "
            "ORDER BY cnt DESC"
        )

        return _ok({
            "controllers": [{"name": c["name"], "filePath": c.get("filePath", "")} for c in controllers],
            "services": [{"name": n} for n in sorted(service_names)],
            "repositories": [{"name": n} for n in sorted(repo_names)],
            "controller_to_service": [dict(r) for r in ctrl_svc],
            "service_to_repository": [dict(r) for r in svc_repo],
        })
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_database(request: Request) -> JSONResponse:
    """Return database-related information (tables, entities, repositories)."""
    repo = _get_repo_param(request)
    store, repo_root = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        # Get @Table annotations on classes → database tables (JPA)
        tables = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
            "WHERE a.name = 'Table' "
            "RETURN c.name AS className, c.filePath AS filePath, a.attributes AS attrs"
        )
        table_list = []
        for t in tables:
            attrs = t.get("attrs", {})
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except (json.JSONDecodeError, TypeError):
                    attrs = {}
            table_list.append({
                "className": t.get("className", ""),
                "filePath": t.get("filePath", ""),
                "tableName": attrs.get("path", attrs.get("name", "")),
                "attributes": attrs,
                "source": "jpa",
            })

        # Get model classes (from /model/ package)
        model_classes = store.query(
            "MATCH (c:Class) WHERE c.filePath CONTAINS '/model/' "
            "RETURN c.name AS name, c.filePath AS filePath ORDER BY c.name"
        )

        # Get repository interfaces
        repositories = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Interface) "
            "WHERE a.name = 'Repository' "
            "RETURN c.name AS name, c.filePath AS filePath ORDER BY c.name"
        )
        # Also find interfaces in /repository/ package without @Repository annotation
        repo_by_path = store.query(
            "MATCH (c:Interface) WHERE c.filePath CONTAINS '/repository/' "
            "RETURN c.name AS name, c.filePath AS filePath ORDER BY c.name"
        )
        existing_repo_names = {r["name"] for r in repositories}
        for r in repo_by_path:
            if r["name"] not in existing_repo_names:
                repositories.append(r)

        # Get @Transactional classes
        transactional = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
            "WHERE a.name = 'Transactional' "
            "RETURN c.name AS name, c.filePath AS filePath ORDER BY c.name"
        )

        # MyBatis: scan XML mapper files for table names
        mybatis_tables: list[dict] = []
        mybatis_mappers: list[dict] = []
        mybatis_sqls: list[dict] = []
        if repo_root:
            from .frontend_mapper import _scan_mybatis_tables, _infer_operation
            tbl_to_mapper = _scan_mybatis_tables(str(repo_root))

            # Get mapper interfaces from graph (from /dao/ package or @Mapper annotation)
            mapper_query = """
                MATCH (c:Interface) WHERE c.filePath CONTAINS '/dao/'
                RETURN c.name AS name, c.filePath AS filePath ORDER BY c.name
            """
            mapper_classes = store.query(mapper_query)

            # Also try @Mapper annotation as fallback
            mapper_by_ann = store.query(
                "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Interface) "
                "WHERE a.name = 'Mapper' "
                "RETURN c.name AS name, c.filePath AS filePath ORDER BY c.name"
            )
            existing_mapper_names = {m["name"] for m in mapper_classes}
            for m in mapper_by_ann:
                if m["name"] not in existing_mapper_names:
                    mapper_classes.append(m)

            for mc in mapper_classes:
                mapper_short = mc["name"].split(".")[-1] if "." in mc["name"] else mc["name"]
                # Reverse lookup: find table name from mapper short name
                table_name_for_mapper = ""
                for tbl, mp in tbl_to_mapper.items():
                    if mp == mapper_short:
                        table_name_for_mapper = tbl
                        break
                mybatis_mappers.append({
                    "name": mc["name"],
                    "filePath": mc.get("filePath", ""),
                    "tableName": table_name_for_mapper,
                })

                # Get mapper method details
                methods = store.query(
                    "MATCH (m:Method) WHERE m.className = $name "
                    "RETURN m.name AS name",
                    {"name": mapper_short},
                )
                for m in methods:
                    op = _infer_operation(m["name"])
                    mybatis_sqls.append({
                        "mapper": mc["name"],
                        "method": m["name"],
                        "operation": op,
                        "tableName": table_name_for_mapper,
                    })

            # Add MyBatis tables to the table list
            existing_names = {t["tableName"] for t in table_list}
            for tbl, mapper in sorted(tbl_to_mapper.items()):
                if tbl not in existing_names:
                    table_list.append({
                        "className": f"{mapper} (MyBatis)",
                        "filePath": "",
                        "tableName": tbl,
                        "attributes": {},
                        "source": "mybatis",
                    })

        result = {
            "tables": table_list,
            "models": [{"name": m["name"], "filePath": m.get("filePath", "")} for m in model_classes],
            "repositories": [{"name": r["name"], "filePath": r.get("filePath", "")} for r in repositories],
            "transactional": [{"name": t["name"], "filePath": t.get("filePath", "")} for t in transactional],
        }
        if mybatis_mappers:
            result["mybatis_mappers"] = mybatis_mappers
        if mybatis_sqls:
            result["mybatis_sqls"] = mybatis_sqls

        return _ok(result)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


# ─── Spring Data method name parser ───────────────────────────────────

def _infer_operation(method_name: str) -> str:
    """Infer DB operation type from Spring Data method name."""
    name_lower = method_name.lower()
    if name_lower.startswith(("find", "get", "read", "query", "list", "count", "exists")):
        if name_lower.startswith("count"):
            return "COUNT"
        return "READ"
    if name_lower.startswith(("delete", "remove")):
        return "DELETE"
    if name_lower.startswith(("save", "insert", "create", "add")):
        return "WRITE"
    if name_lower.startswith(("update", "modify", "set")):
        return "UPDATE"
    return "UNKNOWN"


def _infer_where_clause(method_name: str) -> str:
    """Parse Spring Data method name to infer WHERE clause."""
    import re
    # Extract conditions after "By"
    match = re.search(r"By([A-Z].*)", method_name)
    if not match:
        return ""

    conditions_part = match.group(1)
    # Remove OrderBy suffix
    conditions_part = re.sub(r"OrderBy[A-Za-z]+(?:Asc|Desc)?$", "", conditions_part)
    if not conditions_part:
        return ""

    # Split by And/Or
    fields = re.split(r"And|Or", conditions_part)
    field_names = []
    for f in fields:
        if f:
            # Convert camelCase to snake_case
            snake = re.sub(r"([A-Z])", r"_\1", f).lower().lstrip("_")
            field_names.append(snake)

    return " AND ".join(f"{f} = ?" for f in field_names)


def _infer_order_by(method_name: str) -> str:
    """Parse OrderBy from Spring Data method name."""
    import re
    match = re.search(r"OrderBy([A-Za-z]+?)(Asc|Desc)$", method_name)
    if match:
        field = match.group(1)
        direction = "DESC" if match.group(2) == "Desc" else "ASC"
    else:
        match = re.search(r"OrderBy([A-Za-z]+)$", method_name)
        if not match:
            return ""
        field = match.group(1)
        direction = "ASC"
    snake = re.sub(r"([A-Z])", r"_\1", field).lower().lstrip("_")
    return f"ORDER BY {snake} {direction}"


# ─── DB Chain API ─────────────────────────────────────────────────────

async def api_db_chain(request: Request) -> JSONResponse:
    """Return database operation chain: Controller → Service → Repository → Table."""
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        # 1. Tables from @Table annotations
        tables_raw = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
            "WHERE a.name = 'Table' "
            "RETURN c.name AS className, c.filePath AS filePath, a.attributes AS attrs"
        )
        table_map: dict[str, dict] = {}  # className -> {name, ...}
        for t in tables_raw:
            attrs = t.get("attrs", {})
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except (json.JSONDecodeError, TypeError):
                    attrs = {}
            table_name = attrs.get("path", attrs.get("name", ""))
            table_map[t["className"]] = {
                "name": table_name,
                "className": t["className"],
                "filePath": t.get("filePath", ""),
            }

        # 2. Repositories (Interface + @Repository)
        repos = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Interface) "
            "WHERE a.name = 'Repository' "
            "RETURN c.name AS name, c.filePath AS filePath"
        )
        repo_name_to_file = {}
        for r in repos:
            repo_name_to_file[r["name"]] = r.get("filePath", "")

        # 3. Repository methods
        repo_methods_raw = store.query(
            "MATCH (m:Method) WHERE m.filePath CONTAINS '/repository/' "
            "RETURN m.name AS methodName, m.className AS repoName "
            "ORDER BY m.className, m.name"
        )
        repository_methods = []
        for rm in repo_methods_raw:
            repo_name = rm["repoName"]
            method_name = rm["methodName"]
            # Infer table from repo file path → entity class → @Table
            table_name = _find_table_for_repo(repo_name, table_map, repo_name_to_file)
            operation = _infer_operation(method_name)
            where = _infer_where_clause(method_name)
            order = _infer_order_by(method_name)

            inferred_parts = []
            import re
            top_match = re.match(r"find(Top\d+)", method_name)
            if top_match:
                inferred_parts.append(f"SELECT * LIMIT {top_match.group(1)[3:]}")
            elif operation == "COUNT":
                inferred_parts.append("SELECT COUNT(*)")
            else:
                inferred_parts.append("SELECT *")
            if table_name:
                inferred_parts.append(f"FROM {table_name}")
            else:
                inferred_parts.append("FROM ?")
            if where:
                inferred_parts.append(f"WHERE {where}")
            if order:
                inferred_parts.append(order)

            # Check for @Query annotation
            query_sql = ""
            if method_name:
                q_rows = store.query(
                    "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
                    f"WHERE a.name = 'Query' AND m.name = '{method_name.replace(chr(39), chr(39)*2)}' "
                    "AND m.className = '{repo_name.replace(chr(39), chr(39)*2)}' "
                    "RETURN a.attributes AS attrs LIMIT 1"
                )
                # Use parameterized query instead
                q_rows = store.query(
                    "MATCH (a:Annotation)<-[r:CodeRelation]-(m:Method) "
                    "WHERE a.name = 'Query' AND m.name = $mname AND m.className = $rname "
                    "RETURN a.attributes AS attrs LIMIT 1",
                    {"mname": method_name, "rname": repo_name},
                )
                if q_rows:
                    q_attrs = q_rows[0].get("attrs", {})
                    if isinstance(q_attrs, str):
                        try:
                            q_attrs = json.loads(q_attrs)
                        except (json.JSONDecodeError, TypeError):
                            q_attrs = {}
                    query_sql = q_attrs.get("path", q_attrs.get("value", ""))

            repository_methods.append({
                "repository": repo_name,
                "method": method_name,
                "operation": operation,
                "table": table_name,
                "querySql": query_sql,
                "inferredSql": query_sql if query_sql else " ".join(inferred_parts),
            })

        # 4. Service → Repository calls
        svc_repo = store.query(
            "MATCH (svc:Method)-[r:CodeRelation]->(repo:Method) "
            "WHERE r.type = 'CALLS' AND repo.filePath CONTAINS '/repository/' "
            "RETURN svc.className AS svcName, svc.name AS svcMethod, "
            "       repo.className AS repoName, repo.name AS repoMethod "
            "ORDER BY svc.className, svc.name"
        )
        svc_repo_map: dict[tuple, dict] = {}
        for sr in svc_repo:
            svc_repo_map[(sr["svcName"], sr["svcMethod"])] = {
                "repoName": sr["repoName"],
                "repoMethod": sr["repoMethod"],
            }

        # 5. Controller → Service calls
        ctrl_svc = store.query(
            "MATCH (ctrl:Method)-[r:CodeRelation]->(svc:Method) "
            "WHERE r.type = 'CALLS' "
            "AND ctrl.filePath CONTAINS '/controller/' "
            "AND svc.filePath CONTAINS '/service/' "
            "RETURN ctrl.className AS ctrlName, ctrl.name AS ctrlMethod, "
            "       svc.className AS svcName, svc.name AS svcMethod "
            "ORDER BY ctrl.className, ctrl.name"
        )

        # 6. Build full chains
        chains = []
        for cs in ctrl_svc:
            key = (cs["svcName"], cs["svcMethod"])
            if key in svc_repo_map:
                sr = svc_repo_map[key]
                repo_name = sr["repoName"]
                repo_method = sr["repoMethod"]
                table_name = _find_table_for_repo(repo_name, table_map, repo_name_to_file)

                # Detect ORM type
                orm_type = _detect_orm(repo_name, repo_name_to_file, store)

                chains.append({
                    "controller": cs["ctrlName"],
                    "controllerMethod": cs["ctrlMethod"],
                    "service": cs["svcName"],
                    "serviceMethod": cs["svcMethod"],
                    "repository": repo_name,
                    "repoMethod": repo_method,
                    "table": table_name or "",
                    "ormType": orm_type,
                    "operation": _infer_operation(repo_method),
                })

        # 7. JdbcTemplate services
        jt_fields = store.query(
            "MATCH (f:Field) WHERE f.name = 'jdbcTemplate' "
            "RETURN DISTINCT f.className AS serviceName"
        )
        jdbcTemplate_services = sorted([f["serviceName"] for f in jt_fields])

        return _ok({
            "tables": list(table_map.values()),
            "chains": chains,
            "jdbcTemplate_services": jdbcTemplate_services,
            "repository_methods": repository_methods,
        })
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


def _find_table_for_repo(repo_name: str, table_map: dict, repo_file_map: dict) -> str:
    """Find the table name associated with a repository by matching entity names."""
    repo_file = repo_file_map.get(repo_name, "")

    # Strategy 1: Match by class name similarity
    # e.g., TeamMemberRepository -> TeamMember, ScheduledTaskLogRepository -> ScheduledTaskLog
    repo_base = repo_name.replace("Repository", "")
    for cls_name, tbl in table_map.items():
        short = cls_name.split(".")[-1]
        if repo_base == short:
            return tbl["name"]

    # Strategy 2: Match by repository file path → model package
    if repo_file:
        repo_path = Path(repo_file)
        parts = repo_path.parts
        try:
            repo_idx = parts.index("repository")
            model_parts = list(parts[:repo_idx]) + ["model"] + list(parts[repo_idx + 1:])
            model_dir = str(Path(*model_parts[:-1]))  # .../model
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
    # Check annotations on the repository class/interface
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

    # Default: if it's an Interface + @Repository → Spring Data
    return "Spring Data JDBC"


# ─── Frontend Pages API ───────────────────────────────────────────────
# Delegated to shared module: src/pygitnexus/web/frontend_mapper.py


async def api_frontend_pages(request: Request) -> JSONResponse:
    """Return frontend-to-backend API tracing data.

    Supports two modes:
    - **Single-repo**: Query USES_ENDPOINT relations from the knowledge graph
    - **Cross-repo (group)**: Match frontend API calls from one repo to
      backend endpoints from another repo in a group.
    """
    group_param = request.query_params.get("group", "")
    repo = _get_repo_param(request)

    if group_param:
        result = query_frontend_pages_group(group_param)
        if "error" in result:
            return _err(result["error"])
        return _ok(result)
    elif repo:
        store, _ = _load_store(repo)
        if store is None:
            return _err("No indexed repository found")
        try:
            result = query_frontend_pages_single_repo(repo, store)
            return _ok(result)
        except Exception as e:
            return _err(str(e))
        finally:
            store.close()
    else:
        return _err("Missing 'repo' or 'group' parameter")

# ─── DB Field Impact API ─────────────────────────────────────────────


async def api_db_field_impact(request: Request) -> JSONResponse:
    """Return table/field-level impact: which pages/tasks access a DB table."""
    repo = _get_repo_param(request)
    table = request.query_params.get("table", "")
    group = request.query_params.get("group", "") or None
    if not table:
        return _err("Missing 'table' parameter")
    store, repo_root = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        result = query_db_field_impact(str(repo_root), store, table, group)
        if "error" in result:
            return _err(result["error"])
        return _ok(result)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_db_tables(request: Request) -> JSONResponse:
    """Return all database tables from both JPA (@Table) and MyBatis (XML mappers)."""
    repo = _get_repo_param(request)
    store, repo_root = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        from .frontend_mapper import _scan_mybatis_tables

        tables: list[dict] = []

        # JPA tables from @Table annotations
        tables_raw = store.query(
            "MATCH (a:Annotation)<-[r:CodeRelation]-(c:Class) "
            "WHERE a.name = 'Table' "
            "RETURN c.name AS className, c.filePath AS filePath, a.attributes AS attrs"
        )
        for t in tables_raw:
            attrs = t.get("attrs", {})
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except (json.JSONDecodeError, TypeError):
                    attrs = {}
            tbl_name = attrs.get("path", attrs.get("name", "")) if isinstance(attrs, dict) else ""
            if tbl_name:
                tables.append({
                    "name": tbl_name,
                    "className": t["className"],
                    "source": "jpa",
                })

        # MyBatis tables from XML mapper files
        if repo_root:
            mybatis_tables = _scan_mybatis_tables(str(repo_root))
            existing_names = {t["name"] for t in tables}
            for tbl, mapper in sorted(mybatis_tables.items()):
                if tbl not in existing_names:
                    tables.append({
                        "name": tbl,
                        "className": f"{mapper} (MyBatis Mapper)",
                        "source": "mybatis",
                    })

        return _ok(sorted(tables, key=lambda t: t["name"]))
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


# ─── Group Management API ─────────────────────────────────────────────


async def api_groups(request: Request) -> JSONResponse:
    """List all repo groups — reads from new group storage (group.yaml)."""
    from ..core.group.storage import get_group_dir, list_groups as list_group_dirs
    from ..core.group.config_parser import parse_group_yaml
    from ..storage.repo_manager import get_repo

    group_names = list_group_dirs()
    result = []
    for gname in group_names:
        group_dir = get_group_dir(gname)
        try:
            config = parse_group_yaml(os.path.join(group_dir, "group.yaml"))
        except Exception:
            continue
        repos = []
        for role, reg_name in config.repos.items():
            repo = get_repo(reg_name)
            if repo:
                repos.append({"name": reg_name, "path": repo.path, "role": role})
            else:
                repos.append({"name": reg_name, "path": "", "role": role})
        result.append({
            "name": config.name,
            "label": config.description or config.name,
            "repos": repos,
        })
    return _ok(result)


async def api_groups_create(request: Request) -> JSONResponse:
    """Create a new repo group."""
    from ..storage.repo_manager import create_group

    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON body")

    name = body.get("name", "")
    label = body.get("label", name)
    repos = body.get("repos", [])

    if not name:
        return _err("Missing 'name'")
    if not repos:
        return _err("Missing 'repos' — list of {name, path, role} objects")

    for r in repos:
        if not r.get("role") or r.get("role") not in ("backend", "frontend"):
            return _err(f"Each repo must have a 'role': 'backend' or 'frontend'. Got: {r}")

    group = create_group(name, label, repos)
    return _ok({
        "name": group.name,
        "label": group.label,
        "repos": [{"name": r.name, "path": r.path, "role": r.role} for r in group.repos],
    })


async def api_groups_delete(request: Request) -> JSONResponse:
    """Delete a repo group."""
    from ..storage.repo_manager import delete_group

    name = request.query_params.get("name", "")
    if not name:
        try:
            body = await request.json()
            name = body.get("name", "")
        except Exception:
            pass
    if not name:
        return _err("Missing 'name' parameter")
    if delete_group(name):
        return _ok({"deleted": name})
    return _err(f"Group '{name}' not found")


async def api_impact(request: Request) -> JSONResponse:
    target = request.query_params.get("target", "")
    if not target:
        return _err("Missing 'target' parameter")
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        direction = request.query_params.get("direction", "upstream")
        max_depth = int(request.query_params.get("depth", "3"))
        rel_types_raw = request.query_params.get("relTypes", "")
        if rel_types_raw:
            rel_types = [t for t in rel_types_raw.split(",") if t in _VALID_REL_TYPES]
        else:
            rel_types = _DEFAULT_REL_TYPES
        if not rel_types:
            rel_types = _DEFAULT_REL_TYPES

        # Resolve target symbol
        rows = store.query(
            "MATCH (n) WHERE n.name = $name RETURN n.id AS id, n.name AS name, labels(n) AS nodeType",
            {"name": target},
        )
        if not rows:
            return _err(f"Symbol '{target}' not found")
        row = rows[0]

        sym_id = row.get("id", "")
        nt = row.get("nodeType", {})
        sym_type = next(iter(nt.values()), "") if isinstance(nt, dict) else ""

        if direction == "both":
            # Run BFS in both directions
            upstream = _impact_bfs(
                store, sym_id, sym_type, "upstream",
                min(max_depth, 30), rel_types,
                include_tests=False, min_confidence=0,
            )
            downstream = _impact_bfs(
                store, sym_id, sym_type, "downstream",
                min(max_depth, 30), rel_types,
                include_tests=False, min_confidence=0,
            )
            return _ok({
                "risk": "HIGH" if upstream["risk"] == "HIGH" or upstream["risk"] == "CRITICAL" or downstream["risk"] == "HIGH" or downstream["risk"] == "CRITICAL" else upstream["risk"],
                "summary": {
                    "total_affected": upstream["summary"]["total_affected"] + downstream["summary"]["total_affected"],
                    "upstream_affected": upstream["summary"]["total_affected"],
                    "downstream_affected": downstream["summary"]["total_affected"],
                    "direct_dependents": upstream["summary"].get("direct_dependents", 0) + downstream["summary"].get("direct_dependents", 0),
                },
                "upstream": upstream,
                "downstream": downstream,
            })

        result = _impact_bfs(
            store, sym_id, sym_type, direction,
            min(max_depth, 10), rel_types,
            include_tests=False, min_confidence=0,
        )
        return _ok(result)
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


async def api_mindmap(request: Request) -> JSONResponse:
    """Return call chain tree for mindmap visualization.

    Supports both single-repo and group mode. In group mode,
    also queries the frontend repo for USES_ENDPOINT connections.
    """
    target = request.query_params.get("target", "")
    if not target:
        return _err("Missing 'target' parameter")
    group_param = request.query_params.get("group", "")
    repo = _get_repo_param(request)
    store, repo_root = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    # Auto-detect group if not explicitly provided
    # Use resolved repo_root for group detection (handles short names like "newbee-mall")
    if not group_param:
        auto_group = _auto_detect_group(str(repo_root) if repo_root else (repo or ""))
        if auto_group:
            group_param = auto_group.name
    try:
        # Parse "ClassName.methodName" format
        class_filter = request.query_params.get("class", "")
        if "." in target and not class_filter:
            parts = target.rsplit(".", 1)
            class_filter = parts[0]
            target = parts[1]

        # Find target method (with optional class filter)
        if class_filter:
            rows = store.query(
                "MATCH (n:Method) WHERE n.name = $name AND n.className CONTAINS $cls "
                "RETURN n.name AS name, n.className AS className, n.filePath AS filePath LIMIT 5",
                {"name": target, "cls": class_filter},
            )
        else:
            rows = store.query(
                "MATCH (n:Method) WHERE n.name = $name "
                "RETURN n.name AS name, n.className AS className, n.filePath AS filePath LIMIT 5",
                {"name": target},
            )
        if not rows:
            return _err(f"Method '{target}' not found")
        # Prefer ServiceImpl over Controller
        row = rows[0]
        for r in rows:
            if "ServiceImpl" in r["className"] or "ServiceImpl" in r.get("filePath", ""):
                row = r
                break
            if "Service" in r["className"] and "Impl" not in r["className"]:
                row = r

        method_name = row["name"]
        class_name = row["className"]
        file_path = row["filePath"]

        # Check if this is an Impl class → find Interface via IMPLEMENTS
        impl_iface = None
        impl_iface_short = None
        iface_rows = store.query(
            "MATCH (c:Class)-[r]->(t) "
            "WHERE c.name CONTAINS $cls AND r.type = 'IMPLEMENTS' RETURN t.name AS ifaceName LIMIT 1",
            {"cls": class_name},
        )
        if iface_rows:
            impl_iface = iface_rows[0]["ifaceName"]
            impl_iface_short = impl_iface.split(".")[-1]

        root_label = f"{class_name}.{method_name}"

        # Upstream: USES_ENDPOINT pages as top-level, Controller as child
        # New flow: HTML page → USES_ENDPOINT → API ← EXPOSES ← Controller → CALLS → target
        upstream = []
        if impl_iface_short:
            callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                "WHERE target.className = $iface AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 20",
                {"iface": impl_iface_short, "method": method_name},
            )
            # Three-hop: HTML pages → USES_ENDPOINT → API ← EXPOSES ← Controller → CALLS → Interface
            ep_callers = store.query(
                "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
                "<-[r2:CodeRelation {type: 'EXPOSES'}]-(ctrl:Method)-[r3:CodeRelation {type: 'CALLS'}]->(iface:Method) "
                "WHERE iface.className = $iface AND iface.name = $method "
                "RETURN page.name AS pageName, page.className AS pageClass, "
                "       ctrl.name AS ctrlName, ctrl.className AS ctrlClass, "
                "       api.httpMethod AS httpMethod, api.httpPath AS httpPath LIMIT 20",
                {"iface": impl_iface_short, "method": method_name},
            )

            # Also query frontend repo for CALLS connections (group mode)
            if group_param:
                group = _get_group_info(group_param)
                if group:
                    for fe_repo in group.repos:
                        if fe_repo.role == "frontend":
                            fe_db = Path(fe_repo.path) / ".pygitnexus" / "kuzu"
                            if fe_db.exists():
                                try:
                                    fe_store = GraphStore(fe_db)
                                    # Get all API nodes exposed by Controllers that call the Interface
                                    api_rows = store.query(
                                        "MATCH (ctrl:Method)-[r1:CodeRelation {type: 'EXPOSES'}]->(api:API), "
                                        "(ctrl:Method)-[r2:CodeRelation {type: 'CALLS'}]->(iface:Method) "
                                        "WHERE iface.className = $iface AND iface.name = $method "
                                        "RETURN api AS apiNode, ctrl.className AS ctrlClass, ctrl.name AS ctrlName LIMIT 20",
                                        {"iface": impl_iface_short, "method": method_name},
                                    )
                                    for api_row in api_rows:
                                        api_node = api_row.get("apiNode", {})
                                        hm = api_node.get("httpMethod", "") if isinstance(api_node, dict) else ""
                                        hp = api_node.get("httpPath", "") if isinstance(api_node, dict) else ""
                                        ctrl_cls = api_row.get("ctrlClass", "")
                                        ctrl_name = api_row.get("ctrlName", "")
                                        if hm and hp:
                                            # Query frontend CALLS with fuzzy path matching
                                            fe_matches = _query_frontend_pages(fe_store, hm, hp)
                                            for fm in fe_matches:
                                                ep_callers.append({
                                                    "pageName": fm["pageName"],
                                                    "pageClass": "",
                                                    "ctrlName": ctrl_name,
                                                    "ctrlClass": ctrl_cls,
                                                    "httpMethod": fm["httpMethod"],
                                                    "httpPath": fm["httpPath"],
                                                    "feMethod": fm.get("feMethod", ""),
                                                })
                                    fe_store.close()
                                except Exception:
                                    pass

            # Build tree: controller → API → function → page (inverted for ECharts BT rendering)
            ctrl_map: dict[str, dict] = {}
            for c in ep_callers:
                pk = c["pageName"]
                ctrl_key = f"{c['ctrlClass']}.{c['ctrlName']}"
                api_label = f"{c.get('httpMethod', '?')} {c.get('httpPath', '?')}"
                fe_method = c.get("feMethod", "")
                if ctrl_key not in ctrl_map:
                    ctrl_map[ctrl_key] = {"name": ctrl_key, "via": "CALLS", "children": []}
                ctrl_children = ctrl_map[ctrl_key]["children"]

                # Find or create API node under controller
                api_node = None
                for child in ctrl_children:
                    if child.get("is_api") and child["name"] == api_label:
                        api_node = child
                        break
                if api_node is None:
                    api_node = {
                        "name": api_label,
                        "via": "EXPOSES",
                        "is_api": True,
                        "children": [],
                    }
                    ctrl_children.append(api_node)

                # Add function between API and page, then page as leaf
                if fe_method and fe_method != pk:
                    func_node = None
                    for child in api_node["children"]:
                        if child["name"] == fe_method:
                            func_node = child
                            break
                    if func_node is None:
                        func_node = {
                            "name": fe_method,
                            "via": "CALLS",
                            "children": [],
                        }
                        api_node["children"].append(func_node)
                    # page as innermost leaf
                    if not any(gc["name"] == pk for gc in func_node.get("children", [])):
                        func_node["children"].append({
                            "name": pk,
                            "via": "USES_ENDPOINT",
                            "children": [],
                        })
                else:
                    # No function name, add page directly under API
                    if not any(gc["name"] == pk for gc in api_node["children"]):
                        api_node["children"].append({
                            "name": pk,
                            "via": "USES_ENDPOINT",
                            "children": [],
                        })

            # Add pure Java CALLS callers not covered by USES_ENDPOINT
            def _find_in_tree(name: str, items: list) -> bool:
                """Recursively check if a name exists anywhere in the tree."""
                for item in items:
                    if item.get("name") == name:
                        return True
                    if item.get("children") and _find_in_tree(name, item["children"]):
                        return False
                return False

            for c in callers:
                key = f"{c['callerClass']}.{c['callerName']}"
                if key not in ctrl_map and not _find_in_tree(key, list(ctrl_map.values())):
                    ctrl_map[key] = {"name": key, "via": "CALLS (via Interface)", "children": []}

            upstream = list(ctrl_map.values())

            # MAPS_TO callers: MyBatis Mapper methods that map to this method
            # Expand upstream: Mapper → ServiceImpl → Controller → Page
            mybatis_callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'MAPS_TO'}]->(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 20",
                {"cls": class_name, "method": method_name},
            )
            for c in mybatis_callers:
                mapper_key = f"{c['callerClass']}.{c['callerName']}"
                if mapper_key not in ctrl_map:
                    mapper_node = {"name": mapper_key, "via": "MAPS_TO (MyBatis)", "children": []}

                    # Find CALLS callers of this Mapper method (ServiceImpl)
                    service_callers = store.query(
                        "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                        "WHERE target.name = $methodName AND target.className = $mapperClass "
                        "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 10",
                        {"methodName": c['callerName'], "mapperClass": c['callerClass']},
                    )
                    seen_services: set[str] = set()
                    for sc in service_callers:
                        svc_key = f"{sc['callerClass']}.{sc['callerName']}"
                        if svc_key not in seen_services:
                            seen_services.add(svc_key)
                            svc_node = {"name": svc_key, "via": "CALLS", "children": []}

                            # If ServiceImpl implements an Interface, query callers of the Interface method
                            iface_name = None
                            impl_rows = store.query(
                                "MATCH (c:Class)-[r:CodeRelation {type: 'IMPLEMENTS'}]->(i:Interface) "
                                "WHERE c.name CONTAINS $impl RETURN i.name AS ifaceName LIMIT 1",
                                {"impl": sc['callerClass']},
                            )
                            if impl_rows:
                                iface_name = impl_rows[0]["ifaceName"]

                            target_class = iface_name if iface_name else sc['callerClass']
                            target_simple = target_class.rsplit(".", 1)[-1] if "." in target_class else target_class

                            # Find CALLS callers of this Service/Interface method (Controller)
                            ctrl_callers = store.query(
                                "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                                "WHERE target.name = $methodName AND target.className = $svcClass "
                                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 10",
                                {"methodName": sc['callerName'], "svcClass": target_simple},
                            )
                            seen_ctrls: set[str] = set()
                            for cc in ctrl_callers:
                                ctrl_key = f"{cc['callerClass']}.{cc['callerName']}"
                                if ctrl_key not in seen_ctrls:
                                    seen_ctrls.add(ctrl_key)
                                    ctrl_node = {"name": ctrl_key, "via": "CALLS", "children": []}

                                    # Find USES_ENDPOINT pages that call this Controller
                                    page_callers = store.query(
                                        "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
                                        "<-[r2:CodeRelation {type: 'EXPOSES'}]-(ctrl:Method) "
                                        "WHERE ctrl.name = $methodName AND ctrl.className = $ctrlClass "
                                        "RETURN page.name AS pageName, "
                                        "       api.httpMethod AS httpMethod, api.httpPath AS httpPath LIMIT 10",
                                        {"methodName": cc['callerName'], "ctrlClass": cc['callerClass']},
                                    )
                                    for pc in page_callers:
                                        api_label = f"{pc.get('httpMethod', '?')} {pc.get('httpPath', '?')}"
                                        api_node = {"name": api_label, "via": "EXPOSES", "is_api": True, "children": [
                                            {"name": pc["pageName"], "via": "USES_ENDPOINT", "children": []}
                                        ]}
                                        if not any(ch["name"] == api_label for ch in ctrl_node["children"]):
                                            ctrl_node["children"].append(api_node)

                                    svc_node["children"].append(ctrl_node)

                            if not svc_node["children"]:
                                svc_node["children"] = [{"name": "(no callers found)", "via": "", "children": []}]
                            mapper_node["children"].append(svc_node)

                    if not mapper_node["children"]:
                        mapper_node["children"] = [{"name": "(no callers found)", "via": "", "children": []}]
                    ctrl_map[mapper_key] = mapper_node
                    upstream.append(mapper_node)
        else:
            # Direct callers (for Controllers, Services without Interface)
            callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 20",
                {"cls": class_name, "method": method_name},
            )
            # Frontend pages via USES_ENDPOINT → API → EXPOSES → target
            ep_callers = store.query(
                "MATCH (caller:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
                "<-[r2:CodeRelation {type: 'EXPOSES'}]-(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass, "
                "       caller.filePath AS callerFile, "
                "       api.httpMethod AS httpMethod, api.httpPath AS httpPath LIMIT 20",
                {"cls": class_name, "method": method_name},
            )

            # For Service/Impl targets without Interface: try three-hop to find page→API→Controller→target
            two_hop = store.query(
                "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
                "<-[r2:CodeRelation {type: 'EXPOSES'}]-(ctrl:Method)-[r3:CodeRelation {type: 'CALLS'}]->(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN page.name AS pageName, page.className AS pageClass, "
                "       page.filePath AS pageFile, "
                "       ctrl.name AS ctrlName, ctrl.className AS ctrlClass, "
                "       api.httpMethod AS httpMethod, api.httpPath AS httpPath LIMIT 20",
                {"cls": class_name, "method": method_name},
            )

            # Also query frontend repo for CALLS connections (group mode)
            if group_param:
                group = _get_group_info(group_param)
                if group:
                    for fe_repo in group.repos:
                        if fe_repo.role == "frontend":
                            fe_db = Path(fe_repo.path) / ".pygitnexus" / "kuzu"
                            if fe_db.exists():
                                try:
                                    fe_store = GraphStore(fe_db)
                                    # Get all API nodes that expose the target method
                                    api_rows = store.query(
                                        "MATCH (ctrl:Method)-[r:CodeRelation {type: 'EXPOSES'}]->(api:API) "
                                        "WHERE ctrl.className = $cls AND ctrl.name = $method "
                                        "RETURN api AS apiNode LIMIT 20",
                                        {"cls": class_name, "method": method_name},
                                    )
                                    for api_row in api_rows:
                                        api_node = api_row.get("apiNode", {})
                                        hm = api_node.get("httpMethod", "") if isinstance(api_node, dict) else ""
                                        hp = api_node.get("httpPath", "") if isinstance(api_node, dict) else ""
                                        if hm and hp:
                                            # Query frontend CALLS with fuzzy path matching
                                            fe_matches = _query_frontend_pages(fe_store, hm, hp)
                                            for fm in fe_matches:
                                                two_hop.append({
                                                    "pageName": fm["pageName"],
                                                    "pageClass": "",
                                                    "ctrlName": method_name,
                                                    "ctrlClass": class_name,
                                                    "httpMethod": fm["httpMethod"],
                                                    "httpPath": fm["httpPath"],
                                                    "feMethod": fm.get("feMethod", ""),
                                                })
                                    fe_store.close()
                                except Exception:
                                    pass

            ctrl_map: dict[str, dict] = {}
            # Build tree from two-hop USES_ENDPOINT: controller → API → function → page (inverted for ECharts BT)
            for c in two_hop:
                # Use file path to get display page name (e.g., "Login.vue")
                page_file = c.get("pageFile", "")
                pk = Path(page_file).name if page_file else c["pageName"]
                fe_method = c["pageName"]  # original function name from page
                ctrl_key = f"{c['ctrlClass']}.{c['ctrlName']}"
                api_label = f"{c.get('httpMethod', '?')} {c.get('httpPath', '?')}"
                if ctrl_key not in ctrl_map:
                    ctrl_map[ctrl_key] = {"name": ctrl_key, "via": "CALLS", "children": []}
                ctrl_children = ctrl_map[ctrl_key]["children"]

                # Find or create API node under controller
                api_node = None
                for child in ctrl_children:
                    if child.get("is_api") and child["name"] == api_label:
                        api_node = child
                        break
                if api_node is None:
                    api_node = {
                        "name": api_label,
                        "via": "EXPOSES",
                        "is_api": True,
                        "children": [],
                    }
                    ctrl_children.append(api_node)

                # Add function between API and page, page as innermost leaf
                if fe_method and fe_method != pk:
                    func_node = None
                    for child in api_node["children"]:
                        if child["name"] == fe_method:
                            func_node = child
                            break
                    if func_node is None:
                        func_node = {
                            "name": fe_method,
                            "via": "CALLS",
                            "children": [],
                        }
                        api_node["children"].append(func_node)
                    if not any(gc["name"] == pk for gc in func_node.get("children", [])):
                        func_node["children"].append({
                            "name": pk,
                            "via": "USES_ENDPOINT",
                            "children": [],
                        })
                else:
                    if not any(gc["name"] == pk for gc in api_node["children"]):
                        api_node["children"].append({
                            "name": pk,
                            "via": "USES_ENDPOINT",
                            "children": [],
                        })

            # Add flat USES_ENDPOINT callers with controller → API → function → page structure
            for c in ep_callers:
                caller_file = c.get("callerFile", "")
                pk = Path(caller_file).name if caller_file else c["callerName"]
                fe_method = c["callerName"]
                api_label = f"{c.get('httpMethod', '?')} {c.get('httpPath', '?')}"
                # ep_callers are for direct Controller targets, so the target itself is the Controller
                ctrl_key = f"{class_name}.{method_name}"
                if ctrl_key not in ctrl_map:
                    ctrl_map[ctrl_key] = {"name": ctrl_key, "via": "EXPOSES", "children": []}
                ctrl_children = ctrl_map[ctrl_key]["children"]

                # Find or create API node under controller
                api_node = None
                for child in ctrl_children:
                    if child.get("is_api") and child["name"] == api_label:
                        api_node = child
                        break
                if api_node is None:
                    api_node = {
                        "name": api_label,
                        "via": "EXPOSES",
                        "is_api": True,
                        "children": [],
                    }
                    ctrl_children.append(api_node)

                # Add function under API, page as leaf
                if fe_method and fe_method != pk:
                    if not any(gc["name"] == fe_method for gc in api_node["children"]):
                        api_node["children"].append({
                            "name": fe_method,
                            "via": "CALLS",
                            "children": [{"name": pk, "via": "USES_ENDPOINT", "children": []}],
                        })
                    else:
                        # Add page under existing function
                        for gc in api_node["children"]:
                            if gc["name"] == fe_method:
                                if not any(gc2["name"] == pk for gc2 in gc.get("children", [])):
                                    gc.setdefault("children", []).append({"name": pk, "via": "USES_ENDPOINT", "children": []})
                                break
                else:
                    if not any(gc["name"] == pk for gc in api_node["children"]):
                        api_node["children"].append({
                            "name": pk,
                            "via": "USES_ENDPOINT",
                            "children": [],
                        })

            # Add pure Java CALLS callers (check nested to avoid duplicates)
            def _find_in_tree2(name: str, items: list) -> bool:
                for item in items:
                    if item.get("name") == name:
                        return True
                    if item.get("children") and _find_in_tree2(name, item["children"]):
                        return True
                return False

            for c in callers:
                key = f"{c['callerClass']}.{c['callerName']}"
                if key not in ctrl_map and not _find_in_tree2(key, list(ctrl_map.values())):
                    ctrl_map[key] = {"name": key, "via": "CALLS", "children": []}

            # MAPS_TO callers: MyBatis Mapper methods that map to this method
            # Expand upstream: Mapper → ServiceImpl → Controller → Page
            mybatis_callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'MAPS_TO'}]->(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 20",
                {"cls": class_name, "method": method_name},
            )
            for c in mybatis_callers:
                mapper_key = f"{c['callerClass']}.{c['callerName']}"
                if mapper_key not in ctrl_map:
                    mapper_node = {"name": mapper_key, "via": "MAPS_TO (MyBatis)", "children": []}

                    # Find CALLS callers of this Mapper method (ServiceImpl)
                    service_callers = store.query(
                        "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                        "WHERE target.name = $methodName AND target.className = $mapperClass "
                        "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 10",
                        {"methodName": c['callerName'], "mapperClass": c['callerClass']},
                    )
                    seen_services: set[str] = set()
                    for sc in service_callers:
                        svc_key = f"{sc['callerClass']}.{sc['callerName']}"
                        if svc_key not in seen_services:
                            seen_services.add(svc_key)
                            svc_node = {"name": svc_key, "via": "CALLS", "children": []}

                            # If ServiceImpl implements an Interface, query callers of the Interface method
                            iface_name = None
                            impl_rows = store.query(
                                "MATCH (c:Class)-[r:CodeRelation {type: 'IMPLEMENTS'}]->(i:Interface) "
                                "WHERE c.name CONTAINS $impl RETURN i.name AS ifaceName LIMIT 1",
                                {"impl": sc['callerClass']},
                            )
                            if impl_rows:
                                iface_name = impl_rows[0]["ifaceName"]

                            target_class = iface_name if iface_name else sc['callerClass']
                            target_simple = target_class.rsplit(".", 1)[-1] if "." in target_class else target_class

                            # Find CALLS callers of this Service/Interface method (Controller)
                            ctrl_callers = store.query(
                                "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                                "WHERE target.name = $methodName AND target.className = $svcClass "
                                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 10",
                                {"methodName": sc['callerName'], "svcClass": target_simple},
                            )
                            seen_ctrls: set[str] = set()
                            for cc in ctrl_callers:
                                ctrl_key = f"{cc['callerClass']}.{cc['callerName']}"
                                if ctrl_key not in seen_ctrls:
                                    seen_ctrls.add(ctrl_key)
                                    ctrl_node = {"name": ctrl_key, "via": "CALLS", "children": []}

                                    # Find USES_ENDPOINT pages that call this Controller
                                    page_callers = store.query(
                                        "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(api:API) "
                                        "<-[r2:CodeRelation {type: 'EXPOSES'}]-(ctrl:Method) "
                                        "WHERE ctrl.name = $methodName AND ctrl.className = $ctrlClass "
                                        "RETURN page.name AS pageName, "
                                        "       api.httpMethod AS httpMethod, api.httpPath AS httpPath LIMIT 10",
                                        {"methodName": cc['callerName'], "ctrlClass": cc['callerClass']},
                                    )
                                    for pc in page_callers:
                                        api_label = f"{pc.get('httpMethod', '?')} {pc.get('httpPath', '?')}"
                                        api_node = {"name": api_label, "via": "EXPOSES", "is_api": True, "children": [
                                            {"name": pc["pageName"], "via": "USES_ENDPOINT", "children": []}
                                        ]}
                                        if not any(ch["name"] == api_label for ch in ctrl_node["children"]):
                                            ctrl_node["children"].append(api_node)

                                    svc_node["children"].append(ctrl_node)

                            if not svc_node["children"]:
                                svc_node["children"] = [{"name": "(no callers found)", "via": "", "children": []}]
                            mapper_node["children"].append(svc_node)

                    if not mapper_node["children"]:
                        mapper_node["children"] = [{"name": "(no callers found)", "via": "", "children": []}]
                    ctrl_map[mapper_key] = mapper_node

            upstream = list(ctrl_map.values())

        # Downstream: build nested tree with BFS expansion up to 30 hops
        _MAX_DOWNSTREAM_DEPTH = 30
        callees = store.query(
            "MATCH (source:Method)-[r:CodeRelation {type: 'CALLS'}]->(callee:Method) "
            "WHERE source.className = $cls AND source.name = $method "
            "RETURN callee.name AS calleeName, callee.className AS calleeClass LIMIT 30",
            {"cls": class_name, "method": method_name},
        )
        downstream: list[dict] = []
        callee_map: dict[str, dict] = {}
        seen: set[str] = set()
        for c in callees:
            key = f"{c['calleeClass']}.{c['calleeName']}"
            if key not in seen:
                seen.add(key)
                node: dict[str, Any] = {"name": key, "via": "CALLS", "children": []}
                callee_map[key] = node
                downstream.append(node)

        # BFS expansion up to 30 hops
        current_level = list(callee_map.keys())
        for _depth in range(2, _MAX_DOWNSTREAM_DEPTH + 1):
            if not current_level:
                break
            # Batch query: find all children of current level nodes
            conditions = " OR ".join(
                f"(source.className = {json.dumps(k.rsplit('.', 1)[0])} AND source.name = {json.dumps(k.rsplit('.', 1)[1])})"
                for k in current_level
            )
            next_level: list[str] = []
            rows = store.query(
                f"MATCH (source:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(child:Method) "
                f"WHERE {conditions} "
                f"RETURN source.className AS srcClass, source.name AS srcName, "
                f"       child.name AS childName, child.className AS childClass"
            )
            for r in rows:
                parent_key = f"{r['srcClass']}.{r['srcName']}"
                child_key = f"{r['childClass']}.{r['childName']}"
                if parent_key in callee_map and child_key not in seen:
                    seen.add(child_key)
                    new_node: dict[str, Any] = {"name": child_key, "via": "CALLS", "children": []}
                    callee_map[parent_key]["children"].append(new_node)
                    callee_map[child_key] = new_node
                    next_level.append(child_key)
            current_level = next_level

        # Expand Mapper → Table by inference (MyBatis mappers have no outgoing CALLS edges)
        for key, node in callee_map.items():
            if "Mapper" in key or "DAO" in key:
                parts = key.rsplit(".", 1)
                if len(parts) == 2:
                    table_name = _infer_table_from_mapper(parts[0], parts[1])
                    if table_name and not any(ch["name"] == table_name for ch in node["children"]):
                        node["children"].append({
                            "name": table_name,
                            "via": "SQL (MyBatis)",
                            "children": [],
                        })

        return _ok({
            "root": root_label,
            "upstream": upstream,
            "downstream": downstream,
        })
    except Exception as e:
        return _err(str(e))
    finally:
        store.close()


def _infer_table_from_mapper(mapper_class: str, method_name: str) -> str:
    """Infer table name from Mapper method name conventions."""
    # Strip Mapper suffix from class name
    entity = mapper_class.replace("Mapper", "").replace("DAO", "").replace("Repository", "")
    # Common patterns
    prefixes = ["tb_", "t_", "newbee_mall_"]
    for p in prefixes:
        if p in entity.lower():
            return f"{p}{entity.lower().replace(p, '')}"
    # CamelCase to snake_case
    import re
    snake = re.sub(r'([A-Z])', r'_\1', entity).lower().lstrip("_")
    return f"tb_{snake}" if snake else None


# ─── App factory ──────────────────────────────────────────────────────

def create_app() -> Starlette:
    routes = [
        Route("/", dashboard),
        Route("/demo", viz_demo),
        Route("/api/repos", api_list_repos),
        Route("/api/stats", api_stats),
        Route("/api/query", api_query),
        Route("/api/symbol", api_symbol),
        Route("/api/browse", api_browse),
        Route("/api/tree", api_tree),
        Route("/api/scheduled", api_scheduled),
        Route("/api/architecture", api_architecture),
        Route("/api/database", api_database),
        Route("/api/db-chain", api_db_chain),
        Route("/api/db-field-impact", api_db_field_impact),
        Route("/api/db-tables", api_db_tables),
        Route("/api/explorer", api_explorer),
        Route("/api/frontend-pages", api_frontend_pages),
        Route("/api/groups", api_groups),
        Route("/api/groups/create", api_groups_create, methods=["POST"]),
        Route("/api/groups/delete", api_groups_delete, methods=["POST"]),
        Route("/api/cypher", api_cypher, methods=["POST"]),
        Route("/api/impact", api_impact),
        Route("/api/mindmap", api_mindmap),
        Mount("/static", app=StaticFiles(directory=str(Path(__file__).parent / "static")), name="static"),
    ]

    app = Starlette(
        debug=False,
        routes=routes,
    )
    return app


def start_web(host: str = "127.0.0.1", port: int = 8000, repo: str | None = None) -> None:
    """Start the web dashboard server."""
    import uvicorn

    # Store repo in env for middleware use (optional)
    if repo:
        import os
        os.environ["PYGITNEXUS_WEB_REPO"] = repo

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
