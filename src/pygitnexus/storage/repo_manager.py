"""Repository registry management."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

REGISTRY_DIR = Path.home() / ".pygitnexus"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"


@dataclass
class RepoInfo:
    name: str
    path: str
    stats: dict = field(default_factory=dict)
    indexed_at: str = ""
    language: str = "java"


_REPO_INFO_FIELDS = {"name", "path", "stats", "indexed_at", "language"}


def _normalize_repo_data(raw: dict) -> dict:
    """Normalize old registry entries to current RepoInfo fields."""
    data = {k: v for k, v in raw.items() if k in _REPO_INFO_FIELDS}
    # Handle camelCase -> snake_case
    if "indexedAt" in raw and "indexed_at" not in data:
        data["indexed_at"] = raw["indexedAt"]
    data.setdefault("indexed_at", "")
    data.setdefault("language", "java")
    data.setdefault("stats", {})
    return data


def _ensure_registry() -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_FILE.exists():
        REGISTRY_FILE.write_text("{}", encoding="utf-8")


def _read_registry() -> dict:
    _ensure_registry()
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    # Support both list (old) and dict (new) formats
    if isinstance(raw, list):
        converted = {}
        for entry in raw:
            converted[entry["name"]] = entry
        _write_registry(converted)
        return converted
    return raw


def _write_registry(data: dict) -> None:
    _ensure_registry()
    REGISTRY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def register_repo(info: RepoInfo) -> None:
    """Register or update a repo in the global registry."""
    from datetime import datetime, timezone
    registry = _read_registry()
    info.indexed_at = datetime.now(timezone.utc).isoformat()
    registry[info.name] = asdict(info)
    _write_registry(registry)


def unregister_repo(name: str) -> bool:
    """Remove a repo from the registry. Returns True if found and removed."""
    registry = _read_registry()
    if name in registry:
        del registry[name]
        _write_registry(registry)
        return True
    return False


def list_repos() -> list[RepoInfo]:
    """List all registered repositories."""
    registry = _read_registry()
    return [RepoInfo(**_normalize_repo_data(v)) for v in registry.values()]


def get_repo(name: str) -> RepoInfo | None:
    """Get a specific repository by name."""
    registry = _read_registry()
    if name in registry:
        return RepoInfo(**_normalize_repo_data(registry[name]))
    return None
