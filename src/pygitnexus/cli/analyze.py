"""Analyze command: run the full analysis pipeline."""

from __future__ import annotations

from pathlib import Path

import click

from ..core.pipeline import run_analysis


@click.command("analyze")
@click.argument("path", type=click.Path(exists=True), default=".")
@click.option("--force", is_flag=True, help="Force full re-index even if unchanged.")
def analyze_cmd(path: str, force: bool) -> None:
    """Analyze a Java repository and build the knowledge graph."""
    import shutil

    repo_path = Path(path).resolve()
    db_path = repo_path / ".pygitnexus" / "kuzu"

    # Clean up any leftover .pygitnexus (file or directory)
    if db_path.exists():
        if db_path.is_file():
            db_path.unlink()
        else:
            shutil.rmtree(db_path)
    # Also clean the parent .pygitnexus dir if it's a leftover file
    db_dir = db_path.parent
    if db_dir.is_file():
        db_dir.unlink()
    db_dir.mkdir(parents=True, exist_ok=True)

    def progress(pct: int, msg: str) -> None:
        click.echo(f"  [{pct:3d}%] {msg}")

    click.echo(f"Analyzing {repo_path}...")
    stats = run_analysis(repo_path, db_path, progress_callback=progress)

    click.echo(f"\nAnalysis complete!")
    click.echo(f"  Files:    {stats['files']}")
    click.echo(f"  Classes:  {stats['classes']}")
    click.echo(f"  Methods:  {stats['methods']}")
    click.echo(f"  Fields:   {stats['fields']}")
    click.echo(f"  Calls:    {stats['calls']}")
    click.echo(f"  Imports:  {stats['imports']}")
