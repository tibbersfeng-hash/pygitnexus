"""List command: show all indexed repositories."""

from __future__ import annotations

import click

from ..storage.repo_manager import list_repos


@click.command("list")
def list_cmd() -> None:
    """List all indexed repositories."""
    repos = list_repos()
    if not repos:
        click.echo("No indexed repositories found.")
        return

    click.echo(f"Indexed repositories ({len(repos)}):")
    for repo in repos:
        click.echo(f"  - {repo.name}")
        click.echo(f"    Path:      {repo.path}")
        click.echo(f"    Language:  {repo.language}")
        click.echo(f"    Indexed:   {repo.indexed_at}")
        if repo.stats:
            click.echo(f"    Files:     {repo.stats.get('files', 'N/A')}")
            click.echo(f"    Classes:   {repo.stats.get('classes', 'N/A')}")
            click.echo(f"    Methods:   {repo.stats.get('methods', 'N/A')}")
