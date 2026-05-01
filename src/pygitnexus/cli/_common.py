"""Shared utilities for CLI commands that need the graph store."""

from __future__ import annotations

from pathlib import Path

import click

from ..graph.store import GraphStore
from ..storage.repo_manager import get_repo, list_repos


def find_current_repo() -> object | None:
    """Find the registered repo for the current working directory."""
    repo_path = Path(".").resolve()
    repo = get_repo(repo_path.name)
    if repo is None:
        for r in list_repos():
            if r.path == str(repo_path):
                repo = r
                break
    return repo


def load_store() -> GraphStore:
    """Load the graph store for the current directory's repo.

    Exits with an error if no repo is found or the DB doesn't exist.
    """
    repo = find_current_repo()
    if repo is None:
        # Fallback: look for .pygitnexus/kuzu in current dir tree
        repo_path = Path(".").resolve()
        db_path = repo_path / ".pygitnexus" / "kuzu"
        if not db_path.exists():
            click.echo("No indexed repository found in the current directory.")
            click.echo("Run 'pygitnexus analyze' to index this repository.")
            raise SystemExit(1)
    else:
        db_path = Path(getattr(repo, "path", "")) / ".pygitnexus" / "kuzu"

    if not db_path.exists():
        click.echo(f"Database not found at {db_path}")
        click.echo("Run 'pygitnexus analyze --force' to rebuild the index.")
        raise SystemExit(1)

    store = GraphStore(db_path)
    return store
