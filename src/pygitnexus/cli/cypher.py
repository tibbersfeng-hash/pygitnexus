"""Cypher command: run raw Cypher queries against the knowledge graph."""

from __future__ import annotations

import json

import click

from ..search.query import run_cypher
from ._common import load_store


@click.command("cypher")
@click.argument("query")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def cypher_cmd(query: str, json_output: bool) -> None:
    """Execute a raw Cypher query against the indexed knowledge graph."""
    store = load_store()
    try:
        results = run_cypher(store, query)
    finally:
        store.close()

    if json_output:
        click.echo(json.dumps(results, indent=2, default=str))
    elif not results:
        click.echo("(no results)")
    else:
        click.echo(f"({len(results)} result(s))\n")
        for i, row in enumerate(results):
            click.echo(f"--- Result {i + 1} ---")
            for key, value in row.items():
                click.echo(f"  {key}: {value}")
