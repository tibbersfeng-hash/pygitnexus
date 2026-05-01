"""Status command: check index status for current repository."""

from __future__ import annotations

from pathlib import Path

import click

from ..storage.repo_manager import get_repo


@click.command("status")
def status_cmd() -> None:
    """Show index status for the current repository."""
    repo_path = Path(".").resolve()
    repo = get_repo(repo_path.name)

    if repo is None:
        # Try matching by path
        from ..storage.repo_manager import list_repos
        for r in list_repos():
            if r.path == str(repo_path):
                repo = r
                break

    if repo is None:
        click.echo(f"Repository not indexed: {repo_path}")
        click.echo("Run 'pygitnexus analyze' to index this repository.")
        return

    db_path = Path(repo.path) / ".pygitnexus" / "kuzu"
    exists = db_path.exists()

    click.echo(f"Repository: {repo.name}")
    click.echo(f"Path:       {repo.path}")
    click.echo(f"Language:   {repo.language}")
    click.echo(f"Indexed:    {repo.indexed_at}")
    click.echo(f"DB exists:  {'Yes' if exists else 'No'}")

    if repo.stats:
        click.echo(f"\nStatistics:")
        for key, value in repo.stats.items():
            click.echo(f"  {key}: {value}")
