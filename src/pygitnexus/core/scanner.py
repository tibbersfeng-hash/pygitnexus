"""Scan a directory for Java source files, respecting .gitignore rules."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from .models import JavaFile

# Directories and patterns to skip
DEFAULT_SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "target", "build",
    "out", ".idea", ".settings", ".classpath", ".project",
    "__pycache__", ".tox", ".venv", "venv",
}

JAVA_EXTENSIONS = {".java"}


def _parse_gitignore(root: Path) -> list[str]:
    """Parse .gitignore and return list of patterns."""
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return []
    patterns: list[str] = []
    for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            # Remove leading ! (negation) for simplicity
            patterns.append(stripped.lstrip("!"))
    return patterns


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Check if a relative path matches any gitignore pattern."""
    for pattern in patterns:
        # Handle directory-only patterns (ending with /)
        if pattern.endswith("/"):
            dir_pattern = pattern.rstrip("/")
            parts = rel_path.split("/")
            if dir_pattern in parts:
                return True
        elif fnmatch.fnmatch(rel_path, pattern):
            return True
        elif fnmatch.fnmatch(Path(rel_path).name, pattern):
            return True
    return False


def scan(root: str | Path) -> list[JavaFile]:
    """Scan a directory tree for Java source files.

    Returns a list of JavaFile objects sorted by relative path.
    """
    root = Path(root) if isinstance(root, str) else root
    root = root.resolve()

    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    patterns = _parse_gitignore(root)
    results: list[JavaFile] = []

    for path in sorted(root.rglob("*.java")):
        if not path.is_file():
            continue

        # Skip directories in DEFAULT_SKIP_DIRS
        rel = str(path.relative_to(root))
        rel_parts = path.relative_to(root).parts
        if any(part in DEFAULT_SKIP_DIRS for part in rel_parts):
            continue

        # Check .gitignore patterns
        if _is_ignored(rel, patterns):
            continue

        try:
            content = path.read_bytes()
        except (OSError, PermissionError):
            continue

        results.append(JavaFile(
            path=path,
            relative=rel,
            content=content,
        ))

    return results
