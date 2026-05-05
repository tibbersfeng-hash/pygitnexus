"""Modules command: list business modules."""

from __future__ import annotations

import click

from ..graph.store import GraphStore


@click.command("modules")
@click.option("--detail", is_flag=True, help="Show pages/endpoints per module.")
def modules_cmd(detail: bool) -> None:
    """List business modules inferred from code analysis."""
    store = _load_store()

    modules = store.query(
        "MATCH (m:Module) RETURN m.name, m.description, m.pageCount, "
        "m.endpointCount, m.apiCount, m.source ORDER BY m.endpointCount DESC"
    )

    if not modules:
        click.echo("No business modules found. Run 'testnexus analyze' first.")
        return

    click.echo(f"Business modules ({len(modules)}):\n")
    click.echo(f"  {'Name':<25s} | {'Description':<40s} | {'Pages':>5s} | {'Endpoints':>9s} | {'APIs':>4s} | Source")
    click.echo(f"  {'-'*25}-+-{'-'*40}-+-{'-'*5}-+-{'-'*9}-+-{'-'*4}-+-{'-'*20}")

    for mod in modules:
        name = mod.get("m.name", "")
        desc = mod.get("m.description", "")[:40]
        pages = mod.get("m.pageCount", 0)
        endpoints = mod.get("m.endpointCount", 0)
        apis = mod.get("m.apiCount", 0)
        source = mod.get("m.source", "")
        click.echo(f"  {name:<25s} | {desc:<40s} | {pages:5d} | {endpoints:9d} | {apis:4d} | {source}")

    if detail:
        click.echo("\nModule details:")
        for mod in modules:
            name = mod.get("m.name", "")
            mod_id = f"Module_{name}"
            click.echo(f"\n  --- {name} ---")

            # Pages
            pages = store.query(
                f"MATCH (m:Module {{id: '{mod_id}'}})-[r]->(p:Page) "
                "WHERE r.type = 'BELONGS_TO' RETURN p.name, p.url ORDER BY p.name"
            )
            if pages:
                click.echo(f"  Pages ({len(pages)}):")
                for p in pages:
                    click.echo(f"    {p.get('p.name', '')} ({p.get('p.url', '')})")

            # Endpoints
            eps = store.query(
                f"MATCH (m:Module {{id: '{mod_id}'}})-[r]->(e:Endpoint) "
                "WHERE r.type = 'BELONGS_TO' RETURN e.method, e.path, e.functionName LIMIT 10"
            )
            if eps:
                click.echo(f"  Endpoints ({mod.get('m.endpointCount', 0)}):")
                for e in eps:
                    click.echo(f"    {e.get('e.method', '')} {e.get('e.path', '')} ({e.get('e.functionName', '')})")

    store.close()


def _load_store() -> GraphStore:
    from ..cli._common import find_testnexus_db
    db_path = find_testnexus_db()
    return GraphStore(db_path)
