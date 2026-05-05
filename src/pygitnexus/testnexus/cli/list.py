"""List command: show all analyzed projects."""

from __future__ import annotations

import click

from ..storage.meta import list_projects


@click.command("list")
def list_cmd() -> None:
    """List all analyzed projects."""
    projects = list_projects()
    if not projects:
        click.echo("No analyzed projects found.")
        return

    click.echo(f"Analyzed projects ({len(projects)}):")
    for proj in projects:
        click.echo(f"  - {proj.name}")
        click.echo(f"    Frontend: {proj.frontend_path}")
        if proj.base_url:
            click.echo(f"    URL:      {proj.base_url}")
        click.echo(f"    Analyzed: {proj.analyzed_at}")
        if proj.stats:
            for key, value in proj.stats.items():
                click.echo(f"    {key}: {value}")
