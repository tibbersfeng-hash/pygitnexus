"""Clean command: delete index for current repository."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from ..storage.repo_manager import unregister_repo, get_repo


@click.command("clean")
@click.option("--all", "all_repos", is_flag=True, help="Delete all indexes.")
@click.option("--force", is_flag=True, help="Skip confirmation prompt.")
def clean_cmd(all_repos: bool, force: bool) -> None:
    """Delete index for the current repository."""
    if all_repos:
        _clean_all(force)
    else:
        _clean_current(force)


def _clean_current(force: bool) -> None:
    repo_path = Path(".").resolve()

    # Find the repo in registry
    repo = get_repo(repo_path.name)
    if repo is None:
        from ..storage.repo_manager import list_repos
        for r in list_repos():
            if r.path == str(repo_path):
                repo = r
                break

    if repo is None:
        click.echo("Repository not indexed. Nothing to clean.")
        return

    db_path = Path(repo.path) / ".pygitnexus"
    if db_path.exists():
        if not force:
            click.confirm(f"Delete index at {db_path}?", abort=True)
        shutil.rmtree(db_path)
        click.echo(f"Deleted index at {db_path}")

    unregister_repo(repo.name)
    click.echo(f"Unregistered repository: {repo.name}")


def _clean_all(force: bool) -> None:
    from ..storage.repo_manager import list_repos, REGISTRY_DIR

    if not REGISTRY_DIR.exists():
        click.echo("No indexes found. Nothing to clean.")
        return

    repos = list_repos()
    if not repos:
        click.echo("No indexes found. Nothing to clean.")
        return

    if not force:
        click.confirm(f"Delete ALL {len(repos)} indexes?", abort=True)

    for repo in repos:
        db_path = Path(repo.path) / ".pygitnexus"
        if db_path.exists():
            shutil.rmtree(db_path)

    shutil.rmtree(REGISTRY_DIR, ignore_errors=True)
    click.echo(f"Deleted all {len(repos)} indexes.")
