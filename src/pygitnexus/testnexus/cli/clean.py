"""Clean command: delete TestGraph for current project."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from ..storage.meta import get_project, unregister_project


@click.command("clean")
@click.option("--all", "all_projects", is_flag=True, help="Delete all TestGraph data.")
@click.option("--force", is_flag=True, help="Skip confirmation prompt.")
def clean_cmd(all_projects: bool, force: bool) -> None:
    """Delete TestGraph data for the current project."""
    if all_projects:
        _clean_all(force)
    else:
        _clean_current(force)


def _clean_current(force: bool) -> None:
    current = Path(".").resolve()
    proj = get_project(current.name)
    if proj is None:
        click.echo("No TestGraph data found for this project.")
        return

    db_path = Path(proj.frontend_path) / ".testnexus"
    if db_path.exists():
        if not force:
            click.confirm(f"Delete TestGraph at {db_path}?", abort=True)
        shutil.rmtree(db_path)
        click.echo(f"Deleted TestGraph at {db_path}")

    unregister_project(proj.name)
    click.echo(f"Unregistered project: {proj.name}")


def _clean_all(force: bool) -> None:
    from ..storage.meta import REGISTRY_DIR, list_projects

    if not REGISTRY_DIR.exists():
        click.echo("No TestGraph data found.")
        return

    projects = list_projects()
    if not projects:
        click.echo("No TestGraph data found.")
        return

    if not force:
        click.confirm(f"Delete ALL {len(projects)} TestGraph datasets?", abort=True)

    for proj in projects:
        db_path = Path(proj.frontend_path) / ".testnexus"
        if db_path.exists():
            shutil.rmtree(db_path)

    shutil.rmtree(REGISTRY_DIR, ignore_errors=True)
    click.echo(f"Deleted all {len(projects)} TestGraph datasets.")
