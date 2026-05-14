"""MCP Server for PyGitNexus.

Exposes knowledge graph tools via the Model Context Protocol (stdio).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..graph.store import GraphStore
from ..search.query import query as graph_query, query_methods_with_calls, symbol_context, run_cypher
from ..storage.repo_manager import list_repos as _list_repos


def _resolve_db_path(repo: str | None) -> tuple[Path | None, Path | None]:
    """Resolve the KuzuDB path and repo root for a given repo name/path or cwd.

    Returns (db_path, repo_root).
    """
    if repo:
        # Try as absolute path first
        p = Path(repo) / ".pygitnexus" / "kuzu"
        if p.exists():
            return p, Path(repo).resolve()
        # Try as repo name from registry
        for r in _list_repos():
            if r.name == repo:
                root = Path(r.path).resolve()
                p = root / ".pygitnexus" / "kuzu"
                if p.exists():
                    return p, root
        return None, None
    # Default: current directory
    cwd = Path(".").resolve()
    p = cwd / ".pygitnexus" / "kuzu"
    if p.exists():
        return p, cwd
    # Fallback: check registry for cwd
    for r in _list_repos():
        if Path(r.path).resolve() == cwd:
            root = Path(r.path).resolve()
            p = root / ".pygitnexus" / "kuzu"
            if p.exists():
                return p, root
    # Final fallback: return the first indexed repo
    for r in _list_repos():
        root = Path(r.path).resolve()
        p = root / ".pygitnexus" / "kuzu"
        if p.exists():
            return p, root
    return None, None


def _load_store(repo: str | None) -> tuple[GraphStore | None, Path | None]:
    """Load GraphStore for the given repo.

    Returns (store, repo_root).
    """
    db_path, repo_root = _resolve_db_path(repo)
    if db_path:
        return GraphStore(db_path), repo_root
    return None, None


def _format_results(rows: list[dict], max_rows: int = 50) -> str:
    """Format query results as a readable string."""
    if not rows:
        return "(no results)"
    lines = []
    for i, row in enumerate(rows[:max_rows]):
        lines.append(f"--- Result {i + 1} ---")
        for k, v in row.items():
            lines.append(f"  {k}: {v}")
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more)")
    return "\n".join(lines)


# ─── impact ──────────────────────────────────────────────────────────

_VALID_REL_TYPES = {
    "CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS",
    "HAS_METHOD", "HAS_PROPERTY", "ACCESSES",
}

_DEFAULT_REL_TYPES = ["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"]


def _extract_node_type(row: dict) -> str:
    """Extract node type from a Kuzu row — labels(n) returns {'Method': 'Method', ...}."""
    for key, val in row.items():
        if key not in ("id", "name", "filePath", "startLine", "endLine",
                        "sourceId", "relType", "confidence", "type") and isinstance(val, str):
            return val
    return ""


def _impact_bfs(
    store: GraphStore,
    sym_id: str,
    sym_type: str,
    direction: str,
    max_depth: int,
    relation_types: list[str],
    include_tests: bool,
    min_confidence: float,
) -> dict:
    """BFS traversal for impact analysis."""
    impacted = []
    visited: set[str] = {sym_id}
    frontier = [sym_id]

    # For Class/Interface, seed with Constructors so CALLS edges are found
    if sym_type in ("Class", "Interface"):
        try:
            ctors = store.query(
                "MATCH (n)-[hm:CodeRelation]->(c:Constructor) "
                "WHERE n.id = $symId AND hm.type = 'HAS_CONSTRUCTOR' "
                "RETURN c.id AS id, c.name AS name, labels(c) AS nodeType, c.filePath AS filePath",
                {"symId": sym_id},
            )
            for c in ctors:
                cid = c.get("id", "")
                if cid and cid not in visited:
                    visited.add(cid)
                    frontier.append(cid)
        except Exception:
            pass

    traversal_complete = True
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
        except Exception as e:
            traversal_complete = False
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

    # Risk assessment
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
        "traversalComplete": traversal_complete,
    }


# ─── detect_changes ─────────────────────────────────────────────────

def _run_git_diff(repo_root: Path, scope: str, base_ref: str | None = None) -> str | None:
    """Run git diff and return the output, or None on failure."""
    if scope == "staged":
        args = ["git", "diff", "--staged", "-U0"]
    elif scope == "all":
        args = ["git", "diff", "HEAD", "-U0"]
    elif scope == "compare":
        if not base_ref:
            return None
        args = ["git", "diff", base_ref, "-U0"]
    else:  # unstaged
        args = ["git", "diff", "-U0"]

    try:
        result = subprocess.run(
            args, cwd=repo_root, capture_output=True, text=True, timeout=30
        )
        return result.stdout
    except Exception:
        return None


def _parse_diff_files(diff_output: str) -> list[dict]:
    """Parse git diff output to extract changed files and their hunks."""
    import re
    files: list[dict] = []
    current_file = None
    for line in diff_output.splitlines():
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m:
            current_file = {"filePath": m.group(1), "hunks": []}
            files.append(current_file)
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m and current_file is not None:
            start = int(m.group(1))
            count = int(m.group(2) or "1")
            current_file["hunks"].append({"startLine": start, "endLine": start + count - 1})
    return files


# ─── Server ─────────────────────────────────────────────────────────

def create_server() -> FastMCP:
    """Create and configure the MCP server."""
    mcp = FastMCP(
        name="pygitnexus",
        instructions="PyGitNexus MCP server — Java codebase knowledge graph. "
        "Use query() to search, context() for symbol details, cypher() for raw queries, "
        "impact() for blast radius analysis, detect_changes() for git change impact.",
    )

    @mcp.tool()
    def _list_repos() -> str:
        """List all indexed repositories available to PyGitNexus.

        WHEN TO USE: First step when multiple repos are indexed, or to discover available repos.
        AFTER THIS: Use query(), context(), or cypher() with the repo parameter.
        """
        repos = __list_repos()
        if not repos:
            return "No indexed repositories found. Run 'pygitnexus analyze' in a Java project."
        lines = []
        for r in repos:
            stats = getattr(r, "stats", {}) or {}
            line = f"- {r.name}\n  Path: {r.path}\n  Indexed: {r.indexed_at}\n"
            if stats:
                for k, v in stats.items():
                    line += f"  {k}: {v}\n"
            lines.append(line.rstrip())
        return f"Indexed repositories ({len(repos)}):\n\n" + "\n".join(lines)

    @mcp.tool()
    def query(query: str, limit: int = 20, repo: str | None = None) -> str:
        """Query the code knowledge graph for symbols related to a concept.

        WHEN TO USE: Understanding how code works together. Use this when you need to find
        symbols (classes, methods, files) matching a keyword. Complements grep/IDE search.
        AFTER THIS: Use context() on a specific symbol for 360-degree view.

        Args:
            query: Keyword to search for in symbol names
            limit: Max results to return (default: 20)
            repo: Repository name or path. Omit if only one repo is indexed.
        """
        store, repo_root = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            results = graph_query(store, query, limit=limit)

            # ── Structural analysis on the SAME store (no extra connection) ──
            method_ids = [
                r.get("id", "") for r in results
                if r.get("id") and isinstance(r.get("types"), dict) and "Method" in r.get("types", {})
            ]
            methods_with_calls = graph_query_methods_with_calls(store, method_ids) if method_ids else set()

            # ── Classify methods ──
            import re as _re
            _ACCESSOR_RE = _re.compile(
                r"^(get|set|is)(?!Or|And)[A-Z][a-zA-Z0-9]*$"
            )

            def _looks_like_accessor(name: str) -> bool:
                return bool(_ACCESSOR_RE.match(name))

            accessors: list[dict] = []
            other_methods: list[dict] = []
            non_methods: list[dict] = []
            for row in results:
                types_raw = row.get("types", "")
                # labels(n) returns a string (e.g., 'Method') or dict depending on Kuzu version
                is_method = "Method" in str(types_raw)
                if not is_method:
                    non_methods.append(row)
                    continue

                sym_id = row.get("id", "")
                name = row.get("name", "")

                # Rule 1: has outgoing CALLS → not a pure accessor
                if sym_id in methods_with_calls:
                    other_methods.append(row)
                    continue

                # Rule 2: naming doesn't match → not an accessor
                if not _looks_like_accessor(name):
                    other_methods.append(row)
                    continue

                # Rule 3: parameterCount > 1 → not a simple getter/setter
                param_count = row.get("parameterCount")
                if param_count is not None and int(param_count) > 1:
                    other_methods.append(row)
                    continue

                accessors.append(row)
        finally:
            store.close()

        if not results:
            return f"No symbols found matching '{query}'."

        # ── Format output ──
        lines = [f"Found {len(results)} symbol(s) matching '{query}':\n"]

        # Non-method nodes first
        for row in non_methods:
            types = row.get("types", "unknown")
            name = row.get("name", "")
            path = row.get("filePath", "")
            line_num = row.get("startLine", "")
            line_str = f":{line_num}" if line_num else ""
            lines.append(f"  [{types}] {name}")
            lines.append(f"    {path}{line_str}")

        # Non-accessor methods
        for row in other_methods:
            types = row.get("types", "unknown")
            name = row.get("name", "")
            path = row.get("filePath", "")
            line_num = row.get("startLine", "")
            line_str = f":{line_num}" if line_num else ""
            lines.append(f"  [{types}] {name}")
            lines.append(f"    {path}{line_str}")

        # Summarize pure accessor methods
        if accessors:
            accessor_names = [r.get("name", "") for r in accessors]
            lines.append(f"\n  Accessor methods ({len(accessors)}): {', '.join(accessor_names)}")

        return "\n".join(lines)

    @mcp.tool()
    def cypher(query: str, repo: str | None = None) -> str:
        """Execute a raw Cypher query against the knowledge graph.

        WHEN TO USE: Complex structural queries that search/explore can't answer.
        AFTER THIS: Use context() on result symbols for deeper context.

        SCHEMA:
        - Nodes: File, Folder, Class, Interface, Method, Field, Constructor, Variable, Annotation
        - Relations via CodeRelation table with 'type' property
        - Edge types: CONTAINS, DEFINES, CALLS, IMPORTS, EXTENDS, IMPLEMENTS,
          HAS_METHOD, HAS_PROPERTY, HAS_CONSTRUCTOR, HAS_ANNOTATION, ACCESSES

        EXAMPLES:
        - Find callers of a method:
          MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(target:Method {name: "validateUser"})
          RETURN caller.name, caller.className
        - Find all methods of a class:
          MATCH (c:Class {name: "UserService"})-[r:CodeRelation {type: 'HAS_METHOD'}]->(m:Method)
          RETURN m.name, m.parameterCount

        Args:
            query: Cypher query to execute
            repo: Repository name or path. Omit if only one repo is indexed.
        """
        store, _ = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            results = run_cypher(store, query)
        finally:
            store.close()
        return _format_results(results)

    @mcp.tool()
    def context(name: str, depth: int = 30, repo: str | None = None) -> str:
        """360-degree view of a single code symbol.

        Shows callers, callees, and imports related to the symbol.
        Traverses up to `depth` hops upstream and downstream.

        WHEN TO USE: After query() to understand a specific symbol in depth.
        Shows all callers, callees, and what execution flows a symbol participates in.
        AFTER THIS: Use impact() if planning changes.

        Args:
            name: Symbol name (e.g., "validateUser", "AuthService")
            depth: Max BFS traversal depth for callers/callees (default: 30)
            repo: Repository name or path. Omit if only one repo is indexed.
        """
        store, _ = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            ctx = symbol_context(store, name, max_depth=max(1, min(30, depth)))
        finally:
            store.close()

        lines = [f"Context for '{name}':\n"]

        # Callers
        callers = ctx.get("callers", [])
        lines.append(f"Callers ({len(callers)}):")
        if callers:
            for c in callers:
                caller = c.get("caller", "")
                cls = c.get("callerClass", "")
                conf = c.get("confidence", "")
                full = f"{cls}.{caller}" if cls else caller
                lines.append(f"  - {full} (confidence: {conf:.2f})")
        else:
            lines.append("  (none)")

        # Callees
        callees = ctx.get("callees", [])
        lines.append(f"\nCallees ({len(callees)}):")
        if callees:
            for c in callees:
                callee = c.get("callee", "")
                cls = c.get("calleeClass", "")
                conf = c.get("confidence", "")
                full = f"{cls}.{callee}" if cls else callee
                lines.append(f"  -> {full} (confidence: {conf:.2f})")
        else:
            lines.append("  (none)")

        return "\n".join(lines)

    @mcp.tool()
    def impact(
        target: str = "",
        target_uid: str = "",
        direction: str = "upstream",
        file_path: str = "",
        kind: str = "",
        maxDepth: int = 30,
        relationTypes: list[str] | None = None,
        includeTests: bool = False,
        minConfidence: float = 0,
        repo: str | None = None,
    ) -> str:
        """Analyze the blast radius of changing a code symbol.

        Returns affected symbols grouped by depth, plus risk assessment.

        WHEN TO USE: Before making code changes — especially refactoring, renaming,
        or modifying shared code. Shows what would break.
        AFTER THIS: Review d=1 items (WILL BREAK). Use context() on high-risk symbols.

        Depth groups:
        - d=1: WILL BREAK (direct callers/importers)
        - d=2: LIKELY AFFECTED (indirect)
        - d=3: MAY NEED TESTING (transitive)

        Args:
            target: Name of function, class, or file to analyze
            target_uid: Direct symbol UID from prior tool results (zero-ambiguity)
            direction: "upstream" (what depends on this) or "downstream" (what this depends on)
            file_path: File path hint to disambiguate common names
            kind: Kind filter to disambiguate (e.g. "Class", "Method")
            maxDepth: Max relationship depth (default: 30, range 1-30)
            relationTypes: Filter: CALLS, IMPORTS, EXTENDS, IMPLEMENTS, HAS_METHOD, HAS_PROPERTY, ACCESSES
            includeTests: Include test files (default: false)
            minConfidence: Minimum edge confidence 0-1 (default: 0)
            repo: Repository name or path. Omit if only one repo is indexed.
        """
        store, _ = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."

        max_depth = max(1, min(30, maxDepth))
        rel_types = relation_types or _DEFAULT_REL_TYPES
        rel_types = [t for t in rel_types if t in _VALID_REL_TYPES]
        if not rel_types:
            rel_types = _DEFAULT_REL_TYPES

        try:
            if target_uid:
                # Direct UID lookup
                rows = store.query(
                    "MATCH (n) WHERE n.id = $uid RETURN n.id AS id, n.name AS name, "
                    "labels(n) AS nodeType",
                    {"uid": target_uid},
                )
                if not rows:
                    store.close()
                    return f"Error: Target UID '{target_uid}' not found."
                sym = rows[0]
            else:
                # Name-based lookup with disambiguation
                q = "MATCH (n) WHERE n.name = $name RETURN n.id AS id, n.name AS name, labels(n) AS nodeType, n.filePath AS filePath"
                params: dict = {"name": target}
                if file_path:
                    q += " AND n.filePath ENDS WITH $fp"
                    params["fp"] = file_path
                if kind:
                    q += " AND labels(n)[$kind] IS NOT NULL"
                    params["kind"] = kind

                rows = store.query(q, params)
                if not rows:
                    store.close()
                    return (
                        f"Error: Target '{target}' not found.\n\n"
                        f"Try query('{target}') first to find the symbol, "
                        f"then use impact() with target_uid for precision."
                    )
                if len(rows) > 1:
                    candidates = []
                    for r in rows:
                        nt = r.get("nodeType", {})
                        type_str = next(iter(nt.values()), "") if isinstance(nt, dict) else ""
                        candidates.append(
                            f"  - {r.get('name', '')} ({type_str}) "
                            f"at {r.get('filePath', '')}"
                        )
                    store.close()
                    lines = [
                        f"Ambiguous: Found {len(rows)} symbols matching '{target}'. "
                        f"Use target_uid, file_path, or kind to disambiguate.\n",
                        "Candidates:",
                    ]
                    lines.extend(candidates)
                    return "\n".join(lines)
                sym = rows[0]

            sym_id = sym.get("id", "")
            nt = sym.get("nodeType", {})
            sym_type = next(iter(nt.values()), "") if isinstance(nt, dict) else ""
            result = _impact_bfs(
                store, sym_id, sym_type, direction,
                max_depth, rel_types, includeTests, minConfidence,
            )
        finally:
            store.close()

        # Format output
        lines = [
            f"Impact Analysis for '{target or target_uid}' (direction: {direction})",
            f"Risk: {result['risk']}",
            f"Summary: {result['summary']['total_affected']} symbols affected, "
            f"{result['summary']['direct_dependents']} direct dependents",
            f"Traversal: {'complete' if result['traversalComplete'] else 'partial (max depth reached)'}",
            "",
        ]
        for depth_key, items in result["byDepth"].items():
            label = {
                "d=1": "WILL BREAK",
                "d=2": "LIKELY AFFECTED",
                "d=3": "MAY NEED TESTING",
            }.get(depth_key, depth_key)
            lines.append(f"{depth_key} ({label}, {len(items)} symbols):")
            for item in items[:20]:
                full = f"{item.get('type', '')}:{item.get('name', '')}"
                rel = item.get("relationType", "")
                conf = item.get("confidence", 0)
                fp = item.get("filePath", "")
                lines.append(f"  - {full} via {rel} (confidence: {conf:.2f}) at {fp}")
            if len(items) > 20:
                lines.append(f"  ... and {len(items) - 20} more")
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    def detect_changes(
        scope: str = "unstaged",
        base_ref: str = "",
        repo: str | None = None,
    ) -> str:
        """Analyze uncommitted git changes and find affected execution flows.

        Maps git diff hunks to indexed symbols, then traces which processes are impacted.

        WHEN TO USE: Before committing — to understand what your changes affect.
        Pre-commit review, PR preparation.
        AFTER THIS: Review affected processes. Use context() on high-risk symbols.

        Args:
            scope: What to analyze: "unstaged" (default), "staged", "all", or "compare"
            base_ref: Branch/commit for "compare" scope (e.g., "main")
            repo: Repository name or path. Omit if only one repo is indexed.
        """
        store, repo_root = _load_store(repo)
        if store is None or repo_root is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."

        diff_output = _run_git_diff(repo_root, scope, base_ref or None)
        if diff_output is None:
            store.close()
            return "Error: Git diff failed. Make sure you are in a git repository."

        if not diff_output.strip():
            store.close()
            return "No changes detected."

        # Parse diff to get changed files and hunk ranges
        diff_files = _parse_diff_files(diff_output)
        if not diff_files:
            store.close()
            return "No changes detected in the diff."

        # Map diff hunks to symbols via range overlap
        changed_symbols = []
        for file_diff in diff_files:
            if not file_diff["hunks"]:
                continue
            fp = file_diff["filePath"]
            conditions = []
            params: dict = {"filePath": fp}
            for i, hunk in enumerate(file_diff["hunks"]):
                conditions.append(f"(n.startLine <= $hEnd{i} AND n.endLine >= $hStart{i})")
                params[f"hStart{i}"] = hunk["startLine"]
                params[f"hEnd{i}"] = hunk["endLine"]

            query_str = (
                f"MATCH (n) WHERE n.filePath ENDS WITH $filePath "
                f"AND n.startLine IS NOT NULL AND n.endLine IS NOT NULL "
                f"AND ({' OR '.join(conditions)}) "
                f"RETURN n.id AS id, n.name AS name, labels(n) AS nodeType, "
                f"n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine"
            )
            try:
                rows = store.query(query_str, params)
                for sym in rows:
                    nt = sym.get("nodeType", {})
                    type_str = next(iter(nt.values()), "") if isinstance(nt, dict) else ""
                    changed_symbols.append({
                        "id": sym.get("id", ""),
                        "name": sym.get("name", ""),
                        "type": type_str,
                        "filePath": sym.get("filePath", ""),
                        "startLine": sym.get("startLine", ""),
                        "endLine": sym.get("endLine", ""),
                    })
            except Exception:
                pass

        # Find affected processes
        affected_processes: dict[str, dict] = {}
        if changed_symbols:
            sym_ids = [s["id"] for s in changed_symbols if s["id"]]
            if sym_ids:
                id_clause = ", ".join(f"'{sid.replace(chr(39), chr(39)+chr(39))}'" for sid in sym_ids)
                try:
                    proc_rows = store.query(
                        f"MATCH (n)-[r:CodeRelation]->(p:Process) "
                        f"WHERE n.id IN [{id_clause}] "
                        f"RETURN n.id AS nodeId, p.heuristicLabel AS label, "
                        f"p.processType AS processType, r.step AS step"
                    )
                    for pr in proc_rows:
                        label = pr.get("label", "unknown")
                        if label not in affected_processes:
                            affected_processes[label] = {
                                "name": label,
                                "processType": pr.get("processType", ""),
                                "changedSteps": [],
                            }
                        affected_processes[label]["changedSteps"].append({
                            "symbol": pr.get("nodeId", ""),
                            "step": pr.get("step", ""),
                        })
                except Exception:
                    pass

        # Risk level
        n_procs = len(affected_processes)
        if n_procs == 0:
            risk = "LOW"
        elif n_procs <= 5:
            risk = "MEDIUM"
        elif n_procs <= 15:
            risk = "HIGH"
        else:
            risk = "CRITICAL"

        store.close()

        # Format output
        lines = [
            "Change Impact Analysis",
            f"Scope: {scope}" + (f" (base: {base_ref})" if scope == "compare" and base_ref else ""),
            f"Changed files: {len(diff_files)}",
            f"Changed symbols: {len(changed_symbols)}",
            f"Affected processes: {n_procs}",
            f"Risk level: {risk}",
            "",
        ]

        if changed_symbols:
            lines.append("Changed symbols:")
            for s in changed_symbols[:30]:
                lines.append(
                    f"  - {s['type']}:{s['name']} at {s['filePath']}:{s['startLine']}-{s['endLine']}"
                )
            if len(changed_symbols) > 30:
                lines.append(f"  ... and {len(changed_symbols) - 30} more")
            lines.append("")

        if affected_processes:
            lines.append("Affected processes:")
            for name, proc in affected_processes.items():
                steps = proc["changedSteps"]
                lines.append(f"  - {name} ({proc.get('processType', '')}) — {len(steps)} step(s) affected")
                for step in steps[:5]:
                    lines.append(f"    step {step['step']}: {step['symbol']}")
                if len(steps) > 5:
                    lines.append(f"    ... and {len(steps) - 5} more")
            lines.append("")

        return "\n".join(lines)

    # ─── db_chain ────────────────────────────────────────────────────

    @mcp.tool()
    def db_chain(repo: str | None = None) -> str:
        """Show database operation chains: Controller → Service → Repository → Table.

        WHEN TO USE: Understanding how frontend pages trigger backend database operations.
        Shows ORM-detected tables, service→repository call chains, and JdbcTemplate usage.
        AFTER THIS: Use frontend_pages() to see which frontend pages call these APIs.

        Args:
            repo: Repository name or path. Omit if only one repo is indexed.
        """
        store, repo_root = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            from ..web.frontend_mapper import query_db_chain
            result = query_db_chain(str(repo_root), store)
        finally:
            store.close()

        lines = [f"Database Chains for {repo or 'current repo'}:\n"]

        tables = result.get("tables", [])
        chains = result.get("chains", [])
        repo_methods = result.get("repository_methods", [])
        jt_services = result.get("jdbcTemplate_services", [])

        lines.append(f"Tables ({len(tables)}):")
        for t in tables:
            lines.append(f"  - {t.get('name', '?')} (entity: {t.get('className', '?')})")
        if not tables:
            lines.append("  (none detected — may use MyBatis XML or dynamic SQL)")

        lines.append(f"\nFull Chains ({len(chains)}):")
        for c in chains:
            lines.append(
                f"  {c['controller']}.{c.get('controllerMethod', '')} → "
                f"{c['service']}.{c.get('serviceMethod', '')} → "
                f"{c['repository']}.{c.get('repoMethod', '')} → "
                f"{c.get('table', '?') or '(unknown table)'}"
            )
        if not chains:
            lines.append("  (no Controller→Service→Repository chains found)")

        if repo_methods:
            lines.append(f"\nRepository Methods ({len(repo_methods)}):")
            for rm in repo_methods[:20]:
                lines.append(
                    f"  {rm['repository']}.{rm['method']} → "
                    f"[{rm['operation']}] table={rm.get('table', '?')}"
                )
            if len(repo_methods) > 20:
                lines.append(f"  ... and {len(repo_methods) - 20} more")

        if jt_services:
            lines.append(f"\nJdbcTemplate Services ({len(jt_services)}):")
            for svc in jt_services[:10]:
                lines.append(f"  - {svc}")
            if len(jt_services) > 10:
                lines.append(f"  ... and {len(jt_services) - 10} more")

        return "\n".join(lines)

    # ─── frontend_pages ──────────────────────────────────────────────

    @mcp.tool()
    def frontend_pages(repo: str | None = None, group: str | None = None) -> str:
        """Map frontend pages to backend API endpoints and database tables.

        Supports two modes:
        - **Single-repo**: repo has both frontend and backend code
        - **Cross-repo (group)**: frontend and backend are in separate repos linked by a group

        WHEN TO USE: Understanding which frontend pages call which backend APIs.
        AFTER THIS: Use db_chain() for database details, context() for symbol details.

        Args:
            repo: Repository name or path for single-repo mode.
            group: Group name for cross-repo mode (mutually exclusive with repo).
        """
        if group:
            from ..web.frontend_mapper import query_frontend_pages_group
            result = query_frontend_pages_group(group)
            if "error" in result:
                return f"Error: {result['error']}"
        elif repo:
            store, repo_root = _load_store(repo)
            if store is None:
                return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
            try:
                from ..web.frontend_mapper import query_frontend_pages_single_repo
                result = query_frontend_pages_single_repo(str(repo_root), store)
            finally:
                store.close()
        else:
            return "Error: Specify 'repo' for single-repo mode or 'group' for cross-repo mode."

        mode = result.get("mode", "?")
        source = result.get("source", "?")
        api_calls = result.get("apiCalls", [])
        pages = result.get("frontendPages", [])
        matched = result.get("matchedApiCalls", 0)
        total = result.get("totalApiCalls", 0)

        lines = [
            f"Frontend Pages ({mode}, source={source}):",
            f"  {matched}/{total} API calls matched to backend endpoints",
            f"  {len(pages)} unique frontend pages\n",
        ]

        if pages:
            lines.append("Pages:")
            for p in pages:
                lines.append(f"  - {p}")
            lines.append("")

        if api_calls:
            lines.append("API Call Mappings:")
            for a in api_calls[:30]:
                table_info = ""
                tbls = a.get("linkedTables", [])
                if tbls:
                    table_info = f" → tables: {', '.join(tbls)}"
                svc = a.get("linkedService", "")
                if svc:
                    table_info += f" (service: {svc})"
                lines.append(
                    f"  {a['page']}: {a.get('httpMethod', '?')} {a.get('httpPath', '?')} "
                    f"→ {a.get('controller', '?')}.{a.get('controllerMethod', '?')} "
                    f"({a.get('matchType', '?')}){table_info}"
                )
            if len(api_calls) > 30:
                lines.append(f"  ... and {len(api_calls) - 30} more")

        unmatched = result.get("unmatchedCalls", 0)
        if unmatched:
            lines.append(f"\n{unmatched} unmatched API calls (no backend endpoint found)")

        return "\n".join(lines)

    # ─── Group management ────────────────────────────────────────────

    @mcp.tool()
    def list_groups() -> str:
        """List all repo groups (for cross-repo frontend→backend mapping).

        WHEN TO USE: See available groups before using frontend_pages(group=...).
        Groups link separate frontend and backend repos for cross-repo queries.
        """
        from ..storage.repo_manager import list_groups

        groups = list_groups()
        if not groups:
            return "No repo groups found. Create one with create_group() to link frontend and backend repos."

        lines = [f"Repo groups ({len(groups)}):\n"]
        for g in groups:
            lines.append(f"- {g.name} ({g.label})")
            for r in g.repos:
                lines.append(f"    [{r.role}] {r.name} → {r.path}")
        return "\n".join(lines)

    @mcp.tool()
    def create_group(name: str, label: str, repos: list[dict]) -> str:
        """Create a repo group to link frontend and backend repositories.

        WHEN TO USE: Frontend and backend code are in separate repos but you need
        to trace API calls across them. Create a group, then use frontend_pages(group=...).

        Args:
            name: Unique group identifier.
            label: Human-readable label.
            repos: List of {name, path, role} dicts. role must be 'backend' or 'frontend'.

        EXAMPLE:
            create_group(
                name="myapp",
                label="MyApp Full Stack",
                repos=[
                    {"name": "myapp-backend", "path": "/path/to/backend", "role": "backend"},
                    {"name": "myapp-frontend", "path": "/path/to/frontend", "role": "frontend"},
                ]
            )
        """
        from ..storage.repo_manager import create_group

        for r in repos:
            if r.get("role") not in ("backend", "frontend"):
                return f"Error: Each repo must have role 'backend' or 'frontend'. Got: {r.get('role')}"

        group = create_group(name, label, repos)
        lines = [f"Created group '{group.name}' ({group.label}):\n"]
        for r in group.repos:
            lines.append(f"  [{r.role}] {r.name} → {r.path}")
        return "\n".join(lines)

    @mcp.tool()
    def delete_group(name: str) -> str:
        """Delete a repo group.

        Args:
            name: Group name to delete.
        """
        from ..storage.repo_manager import delete_group

        if delete_group(name):
            return f"Deleted group '{name}'."
        return f"Group '{name}' not found."

    # ─── db_field_impact ─────────────────────────────────────────────

    @mcp.tool()
    def db_field_impact(table: str, repo: str | None = None, group: str | None = None) -> str:
        """Show which top-level entry points (frontend pages, scheduled tasks, API endpoints) access a given database table and its fields.

        WHEN TO USE: Before modifying a DB table/schema — to understand which
        features, pages, or cron jobs will be affected. Provides both table-level
        and field-level impact data.
        AFTER THIS: Use frontend_pages() or impact() on specific symbols for detail.

        Args:
            table: Database table name (e.g., "team_members").
            repo: Repository name or path. Omit if only one repo is indexed.
            group: Group name for cross-repo frontend page matching.
        """
        store, repo_root = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            from ..web.frontend_mapper import query_db_field_impact
            result = query_db_field_impact(str(repo_root), store, table, group)
        finally:
            store.close()

        if "error" in result:
            return f"Error: {result['error']}"

        lines = [
            f"DB Field Impact for table '{table}':",
            f"Entity: {result.get('entityClass', '?')}\n",
        ]

        # Table-level impact
        tl = result.get("table_level", {})
        scheduled = tl.get("scheduledTasks", [])
        api_eps = tl.get("apiEndpoints", [])
        repo_methods = tl.get("repositoryMethods", [])

        lines.append(f"Repository Methods ({len(repo_methods)}):")
        for rm in repo_methods:
            fields_str = ", ".join(f["snake_field"] for f in rm.get("fields", []))
            lines.append(f"  {rm['repository']}.{rm['method']} [{rm['operation']}] → {fields_str}")
        lines.append("")

        lines.append(f"Scheduled Tasks ({len(scheduled)}):")
        for st in scheduled:
            lines.append(f"  {st['class']}.{st['method']}")
        if not scheduled:
            lines.append("  (none)")
        lines.append("")

        lines.append(f"API Endpoints ({len(api_eps)}):")
        for ep in api_eps[:20]:
            lines.append(
                f"  {ep['httpMethod']} {ep['httpPath']} → {ep['controller']}.{ep['method']}"
            )
        if not api_eps:
            lines.append("  (none)")
        elif len(api_eps) > 20:
            lines.append(f"  ... and {len(api_eps) - 20} more")

        pages = tl.get("frontendPages", [])
        lines.append(f"\nFrontend Pages ({len(pages)}):")
        for p in pages[:20]:
            lines.append(f"  {p['page']}: {p['httpMethod']} {p['httpPath']} ({p.get('file', '')})")
        if not pages:
            lines.append("  (none — no frontend code or group specified)")
        elif len(pages) > 20:
            lines.append(f"  ... and {len(pages) - 20} more")
        lines.append("")

        # Field-level impact
        fields = result.get("fields", {})
        if fields:
            lines.append(f"Field-level Impact ({len(fields)} fields):\n")
            for fname, finfo in sorted(fields.items()):
                ops = ", ".join(finfo["operations"])
                n_access = len(finfo["accessed_by"])
                lines.append(f"  {fname} [{ops}] — accessed by {n_access} entry point(s)")
                for ab in finfo["accessed_by"][:5]:
                    if ab["type"] == "scheduled_task":
                        lines.append(f"    - [task] {ab['class']}.{ab['method']}")
                    elif ab["type"] == "api_endpoint":
                        pages_str = ""
                        fps = ab.get("frontendPages", [])
                        if fps:
                            pages_str = f" ← pages: {', '.join(fps)}"
                        lines.append(
                            f"    - [api] {ab.get('httpMethod', '?')} {ab.get('httpPath', '?')} → {ab['controller']}.{ab['method']}{pages_str}"
                        )
                if len(finfo["accessed_by"]) > 5:
                    lines.append(f"    ... and {len(finfo['accessed_by']) - 5} more")
        else:
            lines.append("No field-level impact detected (methods may not follow Spring Data naming).")

        return "\n".join(lines)

    return mcp
