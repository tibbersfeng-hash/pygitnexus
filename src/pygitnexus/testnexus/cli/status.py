"""Status command: check TestGraph status for current project."""

from __future__ import annotations

from pathlib import Path

import click

from ..graph.store import GraphStore
from ..storage.meta import get_project


@click.command("status")
def status_cmd() -> None:
    """Show TestGraph status for the current project."""
    current = Path(".").resolve().name
    proj = get_project(current)

    if proj is None:
        click.echo(f"Project not analyzed: {current}")
        click.echo("Run 'testnexus analyze --frontend .' to analyze.")
        return

    db_path = Path(proj.frontend_path) / ".testnexus" / "kuzu"
    exists = db_path.exists()

    click.echo(f"Project:    {proj.name}")
    click.echo(f"Frontend:   {proj.frontend_path}")
    click.echo(f"Analyzed:   {proj.analyzed_at}")
    click.echo(f"DB exists:  {'Yes' if exists else 'No'}")

    if exists and proj.stats:
        click.echo(f"\nStatistics:")
        for key, value in proj.stats.items():
            click.echo(f"  {key}: {value}")

    # Count nodes in graph
    if exists:
        try:
            store = GraphStore(db_path)
            for table in ["Page", "Component", "Action", "Endpoint", "TestCase", "TestRecord", "Baseline"]:
                result = store.query(f"MATCH (n:{table}) RETURN count(n) as cnt")
                cnt = result[0].get("cnt", 0) if result else 0
                if cnt > 0:
                    click.echo(f"  {table} nodes: {cnt}")
            store.close()
        except Exception:
            pass
