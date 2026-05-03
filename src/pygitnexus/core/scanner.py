"""Scan a directory for source files, respecting .gitignore rules."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from .models import SourceFile

# Directories and patterns to skip
DEFAULT_SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "target", "build",
    "out", ".idea", ".settings", ".classpath", ".project",
    "__pycache__", ".tox", ".venv", "venv",
    "dist", ".next", ".nuxt", ".vite",
}

# File extensions by language
LANG_EXTENSIONS = {
    ".java": "java",
    ".js": "js", ".jsx": "js",
    ".ts": "ts", ".tsx": "ts",
    ".vue": "vue",
}

SUPPORTED_EXTENSIONS = set(LANG_EXTENSIONS.keys())


def detect_language(path: str | Path) -> str | None:
    """Detect language from file extension."""
    ext = Path(path).suffix.lower()
    return LANG_EXTENSIONS.get(ext)


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


def scan(root: str | Path) -> list[SourceFile]:
    """Scan a directory tree for supported source files.

    Returns a list of SourceFile objects sorted by relative path.
    """
    root = Path(root) if isinstance(root, str) else root
    root = root.resolve()

    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    patterns = _parse_gitignore(root)
    results: list[SourceFile] = []

    for ext in sorted(SUPPORTED_EXTENSIONS):
        for path in sorted(root.rglob(f"*{ext}")):
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

            lang = detect_language(path)
            if lang is None:
                continue

            try:
                content = path.read_bytes()
            except (OSError, PermissionError):
                continue

            results.append(SourceFile(
                path=path,
                relative=rel,
                content=content,
                lang=lang,
            ))

    return results
