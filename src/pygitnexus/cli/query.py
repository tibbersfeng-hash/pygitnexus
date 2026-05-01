"""Query command: search for symbols in the knowledge graph."""

from __future__ import annotations

import click

from ..search.query import query as graph_query
from ._common import load_store


@click.command("query")
@click.argument("keyword")
@click.option("--limit", default=20, help="Maximum number of results.")
def query_cmd(keyword: str, limit: int) -> None:
    """Search for symbols matching KEYWORD in the indexed repository."""
    store = load_store()
    try:
        results = graph_query(store, keyword, limit=limit)
    finally:
        store.close()

    if not results:
        click.echo(f"No symbols found matching '{keyword}'.")
        return

    click.echo(f"Found {len(results)} symbol(s) matching '{keyword}':\n")
    for row in results:
        types = row.get("types", "")
        name = row.get("name", "")
        path = row.get("filePath", "")
        line = row.get("startLine", "")
        # KuzuDB labels() returns a string for single-label nodes
        type_str = str(types) if types else "unknown"
        line_str = f":{line}" if line else ""
        click.echo(f"  [{type_str}] {name}")
        click.echo(f"    {path}{line_str}")
