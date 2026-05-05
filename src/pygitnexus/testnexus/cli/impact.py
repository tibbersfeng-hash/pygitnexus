"""Impact command: analyze git diff and find affected tests."""

from __future__ import annotations

import re
from pathlib import Path

import click


@click.command("impact")
@click.option("--diff", required=True, help="Path to git diff file.")
@click.option("--backend", default=None, help="Path to backend project (for pygitnexus impact).")
@click.option("--verbose", is_flag=True, help="Show detailed impact chain.")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def impact_cmd(diff: str, backend: str | None, verbose: bool, json_output: bool) -> None:
    """Analyze git diff and find affected tests."""
    diff_path = Path(diff)
    if not diff_path.exists():
        click.echo(f"Error: {diff} not found.")
        raise SystemExit(1)

    # Parse diff
    changed_files = _parse_diff(diff_path.read_text())
    if not changed_files:
        click.echo("No file changes detected in diff.")
        return

    click.echo(f"Changed files: {len(changed_files)}")
    for f in changed_files:
        click.echo(f"  {f}")

    # Query TestGraph for affected tests
    affected = _find_affected_tests(changed_files, verbose)

    # If backend is provided, query pygitnexus for detailed impact
    backend_impact = []
    if backend:
        backend_impact = _analyze_backend_impact(backend, changed_files, verbose)

    click.echo(f"\nAffected test cases: {len(affected)}")
    for tc in affected:
        click.echo(f"  [{tc['status']}] {tc['name']} -> {tc.get('reason', '')}")

    if backend_impact:
        click.echo(f"\nBackend impact ({len(backend_impact)} symbols):")
        for item in backend_impact:
            depth_label = {1: "WILL BREAK", 2: "LIKELY AFFECTED", 3: "MAY NEED TESTING"}.get(
                item.get("depth", 0), f"d={item.get('depth', 0)}"
            )
            click.echo(
                f"  [{depth_label}] {item.get('type', '')}:{item.get('name', '')} "
                f"via {item.get('relationType', '')} (conf={item.get('confidence', 0):.2f}) "
                f"at {item.get('filePath', '')}"
            )


def _parse_diff(diff_text: str) -> list[str]:
    """Parse git diff and extract changed file paths."""
    files = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)", line)
            if match:
                files.append(match.group(1))
    return files


def _find_affected_tests(changed_files: list[str], verbose: bool) -> list[dict]:
    """Find test cases affected by changed files."""
    # Try to load TestGraph
    try:
        from ..cli._common import find_testnexus_db
        from ..graph.store import GraphStore
        db_path = find_testnexus_db()
        store = GraphStore(db_path)
    except Exception:
        store = None

    affected = []
    if store:
        for cf in changed_files:
            # Check if changed file is a controller
            if "controller" in cf.lower():
                ctrl_name = Path(cf).stem
                endpoints = store.query(
                    f"MATCH (e:Endpoint) WHERE e.controllerName CONTAINS '{ctrl_name}' "
                    "RETURN e.id as id, e.functionName as func, e.path as path, e.method as method"
                )
                for ep in endpoints:
                    func = ep.get("func", "unknown")
                    path = ep.get("path", "")
                    method = ep.get("method", "")
                    affected.append({
                        "name": f"{method} {path}",
                        "status": "DIRTY",
                        "reason": f"Controller changed: {ctrl_name}",
                    })

                    # Find tests linked to this endpoint
                    test_rows = store.query(
                        f"MATCH (tc:TestCase)-[r]->(e:Endpoint {{id: '{ep['id']}'}}) "
                        "WHERE r.type = 'HAS_TEST' "
                        "RETURN tc.name as name, tc.type as type"
                    )
                    for tr in test_rows:
                        affected.append({
                            "name": tr.get("name", "unknown"),
                            "status": "DIRTY",
                            "reason": f"Test for {method} {path}",
                        })

            # Check if changed file is a service
            elif "service" in cf.lower():
                svc_name = Path(cf).stem
                affected.append({
                    "name": f"service:{svc_name}",
                    "status": "DIRTY",
                    "reason": f"Service changed: {svc_name}",
                })

            # Check if changed file is a frontend file
            else:
                rel = cf.replace("\\", "/")
                comp_name = Path(cf).stem
                # Find actions in this component
                action_rows = store.query(
                    f"MATCH (a:Action) WHERE a.componentName CONTAINS '{comp_name}' "
                    "RETURN a.name as name, a.apiPath as apiPath, a.type as type"
                )
                for ar in action_rows:
                    affected.append({
                        "name": ar.get("name", "unknown"),
                        "status": "DIRTY",
                        "reason": f"Frontend action in {rel}",
                    })

        store.close()
    else:
        for cf in changed_files:
            affected.append({
                "name": cf,
                "status": "UNKNOWN",
                "reason": "No TestGraph available, cannot determine impact",
            })

    return affected


def _analyze_backend_impact(
    backend_path: str, changed_files: list[str], verbose: bool
) -> list[dict]:
    """Use pygitnexus KuzuDB to trace CALLS chain for changed backend files."""
    try:
        backend_db = Path(backend_path) / ".pygitnexus" / "kuzu"
        if not backend_db.exists():
            return []

        import kuzu
        db = kuzu.Database(str(backend_db))
        conn = kuzu.Connection(db)
    except Exception:
        return []

    all_impacted = []

    for cf in changed_files:
        # Only process Java backend files
        if not cf.endswith((".java",)):
            continue

        # Find all symbols in the changed file
        try:
            sym_rows = conn.execute(
                "MATCH (n) WHERE n.filePath ENDS WITH $fp "
                "AND n.startLine IS NOT NULL AND n.endLine IS NOT NULL "
                "RETURN n.id AS id, n.name AS name, labels(n) AS nodeType, "
                "n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine",
                {"fp": cf},
            )

            changed_symbols = []
            while sym_rows.has_next():
                row = sym_rows.get_next()
                node_type = _extract_node_type(row[2])
                changed_symbols.append({
                    "id": row[0],
                    "name": row[1],
                    "type": node_type,
                    "filePath": row[3],
                    "startLine": row[4],
                    "endLine": row[5],
                })
        except Exception:
            continue

        # For each changed symbol, find upstream callers via BFS
        for sym in changed_symbols:
            if sym["type"] not in ("Method", "Constructor"):
                continue

            impacted = _bfs_upstream(conn, sym["id"], sym["type"], max_depth=3)
            for item in impacted:
                item["sourceFile"] = cf
                all_impacted.append(item)

    conn.close()
    return all_impacted


def _bfs_upstream(conn, sym_id: str, sym_type: str, max_depth: int) -> list[dict]:
    """BFS upstream to find all callers of a method."""
    visited: set[str] = {sym_id}
    frontier = [sym_id]
    impacted = []

    for depth in range(1, max_depth + 1):
        if not frontier:
            break

        id_list = ", ".join(
            f"'{fid.replace(chr(39), chr(39)+chr(39))}'" for fid in frontier
        )

        cypher = (
            f"MATCH (caller)-[r:CodeRelation]->(n) "
            f"WHERE n.id IN [{id_list}] AND r.type = 'CALLS' "
            f"RETURN n.id AS sourceId, caller.id AS id, caller.name AS name, "
            f"labels(caller) AS nodeType, caller.filePath AS filePath, "
            f"r.type AS relType, r.confidence AS confidence"
        )

        try:
            result = conn.execute(cypher)
        except Exception:
            break

        next_frontier = []
        while result.has_next():
            row = result.get_next()
            rel_id = row[1]
            file_path = row[4] or ""

            # Skip test files
            if "test" in file_path.lower() or "Test" in file_path:
                continue

            if rel_id and rel_id not in visited:
                visited.add(rel_id)
                next_frontier.append(rel_id)
                node_type = _extract_node_type(row[3])
                impacted.append({
                    "depth": depth,
                    "id": rel_id,
                    "name": row[2] or "",
                    "type": node_type,
                    "filePath": file_path,
                    "relationType": row[5] or "",
                    "confidence": row[6] if row[6] else 0,
                })

        frontier = next_frontier

    return impacted


def _extract_node_type(labels_val) -> str:
    """Extract node type from Kuzu labels result."""
    if isinstance(labels_val, dict) and labels_val:
        return next(iter(labels_val.values()), "")
    if isinstance(labels_val, str):
        return labels_val
    return ""
