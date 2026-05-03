"""Group directory and registry storage management."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config_parser import (
    GroupConfig,
    GroupNotFoundError,
    parse_group_yaml,
    write_group_yaml,
)

# Base directory for all group data: ~/.pygitnexus/groups/
GROUPS_DIR = Path.home() / ".pygitnexus" / "groups"


def _ensure_groups_dir() -> None:
    GROUPS_DIR.mkdir(parents=True, exist_ok=True)


def get_group_dir(name: str) -> str:
    """Get the directory for a named group."""
    return str(GROUPS_DIR / name)


def list_groups() -> list[str]:
    """List all configured group names."""
    _ensure_groups_dir()
    if not GROUPS_DIR.exists():
        return []
    return [
        d.name for d in GROUPS_DIR.iterdir()
        if d.is_dir() and (d / "group.yaml").exists()
    ]


def create_group_dir(name: str, force: bool = False) -> str:
    """Create a new group directory with a template group.yaml."""
    _ensure_groups_dir()
    group_dir = GROUPS_DIR / name
    if group_dir.exists():
        if not force:
            raise ValueError(f'Group "{name}" already exists')
        # Keep existing group.yaml if it exists
    else:
        group_dir.mkdir(parents=True, exist_ok=True)

    config = GroupConfig(name=name, repos={})
    yaml_path = group_dir / "group.yaml"
    if not yaml_path.exists():
        write_group_yaml(str(yaml_path), config)

    return str(group_dir)


def load_group_config(group_dir: str) -> GroupConfig:
    """Load group configuration from directory."""
    yaml_path = os.path.join(group_dir, "group.yaml")
    if not os.path.exists(yaml_path):
        raise GroupNotFoundError(os.path.basename(group_dir))
    return parse_group_yaml(yaml_path)


def get_default_groups_dir() -> str:
    """Return the default groups directory."""
    _ensure_groups_dir()
    return str(GROUPS_DIR)


def remove_group(name: str) -> bool:
    """Remove a group directory entirely."""
    group_dir = GROUPS_DIR / name
    if group_dir.exists():
        import shutil
        shutil.rmtree(group_dir)
        return True
    return False
