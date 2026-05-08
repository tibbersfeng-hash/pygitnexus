"""Package version — single source of truth.

Reads from `importlib.metadata` (set by pyproject.toml / uv sync).
Falls back to a VERSION file bundled in the binary for PyInstaller builds.
"""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("pygitnexus")
except Exception:
    # Fallback: VERSION file bundled via PyInstaller datas
    try:
        from pathlib import Path
        _vf = Path(__file__).parent / "VERSION"
        if _vf.exists():
            __version__ = _vf.read_text().strip()
        else:
            raise FileNotFoundError
    except Exception:
        __version__ = "0.0.0"
