"""Project metadata and registry management."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REGISTRY_DIR = Path.home() / ".testnexus"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"


@dataclass
class ProjectInfo:
    """Information about an analyzed project."""
    name: str
    frontend_path: str
    backend_path: str = ""
    base_url: str = ""
    analyzed_at: str = ""
    stats: dict = field(default_factory=dict)


def _ensure_registry() -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_FILE.exists():
        REGISTRY_FILE.write_text("[]")


def register_project(info: ProjectInfo) -> None:
    """Register or update a project."""
    _ensure_registry()
    data = json.loads(REGISTRY_FILE.read_text())
    # Update if exists
    for i, item in enumerate(data):
        if item["name"] == info.name:
            data[i] = _project_to_dict(info)
            REGISTRY_FILE.write_text(json.dumps(data, indent=2))
            return
    data.append(_project_to_dict(info))
    REGISTRY_FILE.write_text(json.dumps(data, indent=2))


def get_project(name: str) -> ProjectInfo | None:
    """Get project info by name."""
    _ensure_registry()
    data = json.loads(REGISTRY_FILE.read_text())
    for item in data:
        if item["name"] == name:
            return _dict_to_project(item)
    return None


def list_projects() -> list[ProjectInfo]:
    """List all registered projects."""
    _ensure_registry()
    data = json.loads(REGISTRY_FILE.read_text())
    return [_dict_to_project(item) for item in data]


def unregister_project(name: str) -> bool:
    """Remove a project from registry."""
    _ensure_registry()
    data = json.loads(REGISTRY_FILE.read_text())
    new_data = [item for item in data if item["name"] != name]
    if len(new_data) == len(data):
        return False
    REGISTRY_FILE.write_text(json.dumps(new_data, indent=2))
    return True


def _project_to_dict(info: ProjectInfo) -> dict:
    return {
        "name": info.name,
        "frontend_path": info.frontend_path,
        "backend_path": info.backend_path,
        "base_url": info.base_url,
        "analyzed_at": info.analyzed_at,
        "stats": info.stats,
    }


def _dict_to_project(data: dict) -> ProjectInfo:
    return ProjectInfo(
        name=data["name"],
        frontend_path=data.get("frontend_path", ""),
        backend_path=data.get("backend_path", ""),
        base_url=data.get("base_url", ""),
        analyzed_at=data.get("analyzed_at", ""),
        stats=data.get("stats", {}),
    )
