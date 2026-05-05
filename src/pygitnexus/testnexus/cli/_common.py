"""Shared utilities for CLI commands."""

from __future__ import annotations

from pathlib import Path

from ..storage.meta import get_project, list_projects


def find_testnexus_db() -> Path:
    """Find the TestGraph KuzuDB path for the current project."""
    # Priority 1: Local .testnexus/kuzu in current directory
    repo_path = Path(".").resolve()
    db_path = repo_path / ".testnexus" / "kuzu"
    if db_path.exists():
        return db_path

    # Priority 2: Check registry
    current = repo_path.name
    proj = get_project(current)
    if proj is not None:
        db_path = Path(proj.frontend_path) / ".testnexus" / "kuzu"
        if db_path.exists():
            return db_path

    # Priority 3: Try any registered project
    for p in list_projects():
        db_path = Path(p.frontend_path) / ".testnexus" / "kuzu"
        if db_path.exists():
            return db_path

    raise SystemExit(
        "No TestGraph database found. Run 'testnexus analyze --frontend <path>' first."
    )
