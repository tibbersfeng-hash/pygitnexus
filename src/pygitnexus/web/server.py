"""Web dashboard: Starlette ASGI app serving PyGitNexus knowledge graph data."""

from __future__ import annotations

import json
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
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        node_counts = {}
        for t in ["File", "Folder", "Class", "Interface", "Method", "Field"]:
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


async def api_query(request: Request) -> JSONResponse:
    keyword = request.query_params.get("q", "")
    if not keyword:
        return _err("Missing 'q' parameter")
    limit = int(request.query_params.get("limit", "50"))
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


async def api_symbol(request: Request) -> JSONResponse:
    name = request.query_params.get("name", "")
    if not name:
        return _err("Missing 'name' parameter")
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
    try:
        ctx = symbol_context(store, name)
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
    """List all repo groups."""
    from ..storage.repo_manager import list_groups

    groups = list_groups()
    return _ok([{
        "name": g.name,
        "label": g.label,
        "repos": [{"name": r.name, "path": r.path, "role": r.role} for r in g.repos],
    } for g in groups])


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
                min(max_depth, 10), rel_types,
                include_tests=False, min_confidence=0,
            )
            downstream = _impact_bfs(
                store, sym_id, sym_type, "downstream",
                min(max_depth, 10), rel_types,
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
    """Return call chain tree for mindmap visualization."""
    target = request.query_params.get("target", "")
    if not target:
        return _err("Missing 'target' parameter")
    repo = _get_repo_param(request)
    store, _ = _load_store(repo)
    if store is None:
        return _err("No indexed repository found")
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
        # Actual flow: HTML page → USES_ENDPOINT → Controller → CALLS → target
        upstream = []
        if impl_iface_short:
            callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                "WHERE target.className = $iface AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 20",
                {"iface": impl_iface_short, "method": method_name},
            )
            # Two-hop: HTML pages → USES_ENDPOINT → Controller → CALLS → Interface
            ep_callers = store.query(
                "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(ctrl:Method)-[r2:CodeRelation {type: 'CALLS'}]->(iface:Method) "
                "WHERE iface.className = $iface AND iface.name = $method "
                "RETURN page.name AS pageName, page.className AS pageClass, "
                "       ctrl.name AS ctrlName, ctrl.className AS ctrlClass LIMIT 20",
                {"iface": impl_iface_short, "method": method_name},
            )

            # Build tree: USES_ENDPOINT pages as parents, Controller as child
            page_map: dict[str, dict] = {}
            for c in ep_callers:
                pk = c["pageName"]
                ctrl_key = f"{c['ctrlClass']}.{c['ctrlName']}"
                if pk not in page_map:
                    page_map[pk] = {"name": pk, "via": "USES_ENDPOINT", "children": []}
                if not any(ch["name"] == ctrl_key for ch in page_map[pk]["children"]):
                    page_map[pk]["children"].append({
                        "name": ctrl_key,
                        "via": "CALLS (via Interface)",
                        "children": [],
                    })

            # Add pure Java CALLS callers not covered by USES_ENDPOINT
            for c in callers:
                key = f"{c['callerClass']}.{c['callerName']}"
                if key not in page_map:
                    already_child = any(
                        any(ch["name"] == key for ch in pm["children"])
                        for pm in page_map.values()
                    )
                    if not already_child:
                        page_map[key] = {"name": key, "via": "CALLS (via Interface)", "children": []}

            upstream = list(page_map.values())
        else:
            # Direct callers (for Controllers, Services without Interface)
            callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 20",
                {"cls": class_name, "method": method_name},
            )
            # Also include frontend pages via USES_ENDPOINT
            ep_callers = store.query(
                "MATCH (caller:Method)-[r:CodeRelation {type: 'USES_ENDPOINT'}]->(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN caller.name AS callerName, caller.className AS callerClass LIMIT 20",
                {"cls": class_name, "method": method_name},
            )

            # For Controller targets: USES_ENDPOINT pages are the callers (direct)
            # For Service/Impl targets without Interface: try two-hop to find page→Controller→target
            two_hop = store.query(
                "MATCH (page:Method)-[r1:CodeRelation {type: 'USES_ENDPOINT'}]->(ctrl:Method)-[r2:CodeRelation {type: 'CALLS'}]->(target:Method) "
                "WHERE target.className = $cls AND target.name = $method "
                "RETURN page.name AS pageName, page.className AS pageClass, "
                "       ctrl.name AS ctrlName, ctrl.className AS ctrlClass LIMIT 20",
                {"cls": class_name, "method": method_name},
            )

            page_map: dict[str, dict] = {}
            # Build tree from two-hop USES_ENDPOINT: page → Controller → target
            for c in two_hop:
                pk = c["pageName"]
                ctrl_key = f"{c['ctrlClass']}.{c['ctrlName']}"
                if pk not in page_map:
                    page_map[pk] = {"name": pk, "via": "USES_ENDPOINT", "children": []}
                if not any(ch["name"] == ctrl_key for ch in page_map[pk]["children"]):
                    page_map[pk]["children"].append({
                        "name": ctrl_key,
                        "via": "CALLS",
                        "children": [],
                    })

            # Add flat USES_ENDPOINT callers (if any page directly calls this method)
            for c in ep_callers:
                pk = c["callerName"]
                if pk not in page_map:
                    page_map[pk] = {"name": pk, "via": "USES_ENDPOINT", "children": []}

            # Add pure Java CALLS callers
            for c in callers:
                key = f"{c['callerClass']}.{c['callerName']}"
                if key not in page_map:
                    already_child = any(
                        any(ch["name"] == key for ch in pm["children"])
                        for pm in page_map.values()
                    )
                    if not already_child:
                        page_map[key] = {"name": key, "via": "CALLS", "children": []}

            upstream = list(page_map.values())

        # Downstream: build nested tree with depth-2 expansion
        callees = store.query(
            "MATCH (source:Method)-[r:CodeRelation {type: 'CALLS'}]->(callee:Method) "
            "WHERE source.className = $cls AND source.name = $method "
            "RETURN callee.name AS calleeName, callee.className AS calleeClass LIMIT 30",
            {"cls": class_name, "method": method_name},
        )
        downstream: list[dict] = []
        callee_map: dict[str, dict] = {}
        for c in callees:
            key = f"{c['calleeClass']}.{c['calleeName']}"
            node: dict[str, Any] = {"name": key, "via": "CALLS", "children": []}
            callee_map[key] = node
            downstream.append(node)

        # Depth-2 expansion: batch query all children of callees
        if callee_map:
            callee_keys = list(callee_map.keys())
            # Build OR conditions for the batch query
            conditions = " OR ".join(
                f"(source.className = {json.dumps(k.rsplit('.', 1)[0])} AND source.name = {json.dumps(k.rsplit('.', 1)[1])})"
                for k in callee_keys
            )
            depth2_rows = store.query(
                f"MATCH (source:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(child:Method) "
                f"WHERE {conditions} "
                f"RETURN source.className AS srcClass, source.name AS srcName, "
                f"       child.name AS childName, child.className AS childClass LIMIT 200"
            )
            for r in depth2_rows:
                parent_key = f"{r['srcClass']}.{r['srcName']}"
                child_key = f"{r['childClass']}.{r['childName']}"
                if parent_key in callee_map:
                    if not any(ch["name"] == child_key for ch in callee_map[parent_key]["children"]):
                        callee_map[parent_key]["children"].append({
                            "name": child_key,
                            "via": "CALLS",
                            "children": [],
                        })

        # Depth-2: expand Mapper → Table by inference (MyBatis mappers have no outgoing CALLS edges)
        for key, node in callee_map.items():
            if "Mapper" in key or "DAO" in key:
                parts = key.rsplit(".", 1)
                if len(parts) == 2:
                    table_name = _infer_table_from_mapper(parts[0], parts[1])
                    if table_name:
                        if not any(ch["name"] == table_name for ch in node["children"]):
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
