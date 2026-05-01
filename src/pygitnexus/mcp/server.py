"""MCP Server for PyGitNexus.

Exposes knowledge graph tools via the Model Context Protocol (stdio).
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..graph.store import GraphStore
from ..search.query import query as graph_query, symbol_context, run_cypher
from ..storage.repo_manager import list_repos


def _resolve_db_path(repo: str | None) -> Path | None:
    """Resolve the KuzuDB path for a given repo name/path or cwd."""
    if repo:
        # Try as absolute path first
        p = Path(repo) / ".pygitnexus" / "kuzu"
        if p.exists():
            return p
        # Try as repo name from registry
        for r in list_repos():
            if r.name == repo:
                p = Path(r.path) / ".pygitnexus" / "kuzu"
                if p.exists():
                    return p
        return None
    # Default: current directory
    cwd = Path(".").resolve()
    p = cwd / ".pygitnexus" / "kuzu"
    if p.exists():
        return p
    # Fallback: check registry for cwd
    for r in list_repos():
        if Path(r.path).resolve() == cwd:
            p = Path(r.path) / ".pygitnexus" / "kuzu"
            if p.exists():
                return p
    return None


def _load_store(repo: str | None) -> GraphStore | None:
    """Load GraphStore for the given repo."""
    db_path = _resolve_db_path(repo)
    if db_path:
        return GraphStore(db_path)
    return None


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


def create_server() -> FastMCP:
    """Create and configure the MCP server."""
    mcp = FastMCP(
        name="pygitnexus",
        instructions="PyGitNexus MCP server — Java codebase knowledge graph. "
        "Use query() to search, context() for symbol details, cypher() for raw queries.",
    )

    @mcp.tool()
    def list_repos() -> str:
        """List all indexed repositories available to PyGitNexus.

        WHEN TO USE: First step when multiple repos are indexed, or to discover available repos.
        AFTER THIS: Use query(), context(), or cypher() with the repo parameter.
        """
        repos = list_repos()
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
        store = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            results = graph_query(store, query, limit=limit)
        finally:
            store.close()
        if not results:
            return f"No symbols found matching '{query}'."
        lines = [f"Found {len(results)} symbol(s) matching '{query}':\n"]
        for row in results:
            types = row.get("types", "unknown")
            name = row.get("name", "")
            path = row.get("filePath", "")
            line_num = row.get("startLine", "")
            line_str = f":{line_num}" if line_num else ""
            lines.append(f"  [{types}] {name}")
            lines.append(f"    {path}{line_str}")
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
        store = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            results = run_cypher(store, query)
        finally:
            store.close()
        return _format_results(results)

    @mcp.tool()
    def context(name: str, repo: str | None = None) -> str:
        """360-degree view of a single code symbol.

        Shows callers, callees, and imports related to the symbol.

        WHEN TO USE: After query() to understand a specific symbol in depth.
        Shows all callers, callees, and what execution flows a symbol participates in.
        AFTER THIS: Use cypher() for more complex queries if needed.

        Args:
            name: Symbol name (e.g., "validateUser", "AuthService")
            repo: Repository name or path. Omit if only one repo is indexed.
        """
        store = _load_store(repo)
        if store is None:
            return f"Error: No indexed repository found{' for ' + repo if repo else ''}."
        try:
            ctx = symbol_context(store, name)
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

        # Imports
        imports = ctx.get("imports", [])
        lines.append(f"\nImports ({len(imports)}):")
        if imports:
            for imp in imports:
                target = imp.get("target", "")
                lines.append(f"  > {target}")
        else:
            lines.append("  (none)")

        return "\n".join(lines)

    return mcp
