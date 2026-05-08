"""Package version — single source of truth.

Reads from `importlib.metadata` (set by pyproject.toml / uv sync).
Falls back to a VERSION file bundled in the binary, then to
a hardcoded string for PyInstaller bundles where metadata is
unavailable.
"""

from __future__ import annotations

import sys

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("pygitnexus")
except Exception:
    # Fallback 1: VERSION file bundled via PyInstaller datas
    try:
        from pathlib import Path
        _vf = Path(__file__).parent / "VERSION"
        if _vf.exists():
            __version__ = _vf.read_text().strip()
        else:
            raise FileNotFoundError
    except Exception:
        # Fallback 2: frozen builds — try _MEIPASS
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            try:
                from pathlib import Path
                _vf2 = Path(sys._MEIPASS) / "pygitnexus" / "VERSION"
                if _vf2.exists():
                    __version__ = _vf2.read_text().strip()
                else:
                    raise FileNotFoundError
            except Exception:
                __version__ = "0.0.0"
        else:
            __version__ = "0.0.0"
