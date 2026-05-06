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


@dataclass
class GroupRepo:
    """A repo within a group, with an assigned role."""
    name: str
    path: str
    role: str  # "backend", "frontend", etc.


@dataclass
class GroupInfo:
    name: str
    label: str
    repos: list[GroupRepo] = field(default_factory=list)


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
    # Clean up legacy list-format keys (e.g. "repositories": [])
    if isinstance(raw.get("repositories"), list):
        del raw["repositories"]
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
    return [RepoInfo(**_normalize_repo_data(v)) for k, v in registry.items() if k != "_groups" and isinstance(v, dict)]


def get_repo(name: str) -> RepoInfo | None:
    """Get a specific repository by name."""
    if name == "_groups":
        return None
    registry = _read_registry()
    if name in registry and isinstance(registry[name], dict):
        return RepoInfo(**_normalize_repo_data(registry[name]))
    return None


def get_repo_by_path(path: str) -> RepoInfo | None:
    """Get a repository by its path."""
    registry = _read_registry()
    for k, v in registry.items():
        if k == "_groups":
            continue
        normalized = _normalize_repo_data(v)
        if normalized.get("path") == path:
            return RepoInfo(**normalized)
    return None


# ─── Group Management ─────────────────────────────────────────────────

def _read_groups() -> dict[str, dict]:
    """Read groups from registry. Returns {group_name: group_data}."""
    registry = _read_registry()
    return registry.get("_groups", {})


def _write_groups(groups: dict[str, dict]) -> None:
    """Write groups back to registry."""
    registry = _read_registry()
    registry["_groups"] = groups
    _write_registry(registry)


def create_group(name: str, label: str, repos: list[dict]) -> GroupInfo:
    """Create a new group linking multiple repos.

    Args:
        name: Unique group identifier.
        label: Human-readable label.
        repos: List of {"name", "path", "role"} dicts.
    """
    groups = _read_groups()
    group_data = {
        "name": name,
        "label": label,
        "repos": repos,
    }
    groups[name] = group_data
    _write_groups(groups)
    return GroupInfo(name=name, label=label, repos=[GroupRepo(**r) for r in repos])


def delete_group(name: str) -> bool:
    """Delete a group. Returns True if found and removed."""
    groups = _read_groups()
    if name in groups:
        del groups[name]
        _write_groups(groups)
        return True
    return False


def list_groups() -> list[GroupInfo]:
    """List all registered groups."""
    groups = _read_groups()
    return [
        GroupInfo(
            name=g["name"],
            label=g.get("label", g["name"]),
            repos=[GroupRepo(**r) for r in g.get("repos", [])],
        )
        for g in groups.values()
    ]


def get_group(name: str) -> GroupInfo | None:
    """Get a group by name."""
    groups = _read_groups()
    if name in groups:
        g = groups[name]
        return GroupInfo(
            name=g["name"],
            label=g.get("label", g["name"]),
            repos=[GroupRepo(**r) for r in g.get("repos", [])],
        )
    return None


def find_group_by_repo(repo_path: str) -> GroupInfo | None:
    """Find the first group that contains a repo with the given path."""
    groups = _read_groups()
    for g in groups.values():
        for r in g.get("repos", []):
            if r.get("path") == repo_path:
                return GroupInfo(
                    name=g["name"],
                    label=g.get("label", g["name"]),
                    repos=[GroupRepo(**rr) for rr in g.get("repos", [])],
                )
    return None
