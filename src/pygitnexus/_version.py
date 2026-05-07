"""Package version — single source of truth.

Reads from `importlib.metadata` (set by pyproject.toml / uv sync).
Falls back to a hardcoded string for PyInstaller bundles where
metadata may not be included.
"""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("pygitnexus")
except Exception:
    # Fallback for frozen builds — updated with each release
    __version__ = "9.0.0"
