"""Configuration parsing for group definitions.

group.yaml format:
    name: my-app
    description: Optional description
    repos:
      app/backend: dashboard-backend
      app/frontend: dashboard-frontend
    detect:
      http: true
      grpc: false
      topics: false
    links: []
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class GroupConfig:
    name: str
    description: str = ""
    repos: dict[str, str] = field(default_factory=dict)  # group_path → registry_name
    detect_http: bool = True
    detect_grpc: bool = False
    detect_topics: bool = False
    links: list[dict] = field(default_factory=list)


class GroupNotFoundError(Exception):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f'Group "{name}" not found')


def parse_group_yaml(path: str) -> GroupConfig:
    """Parse a group.yaml file into a GroupConfig."""
    try:
        import yaml
    except ImportError:
        # Fallback: minimal YAML parser for simple group.yaml
        return _parse_yaml_fallback(path)

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    detect = data.get("detect", {})
    if isinstance(detect, bool):
        detect = {"http": detect}

    return GroupConfig(
        name=data.get("name", os.path.basename(os.path.dirname(path))),
        description=data.get("description", ""),
        repos=data.get("repos", {}),
        detect_http=detect.get("http", True),
        detect_grpc=detect.get("grpc", False),
        detect_topics=detect.get("topics", False),
        links=data.get("links", []),
    )


def _parse_yaml_fallback(path: str) -> GroupConfig:
    """Minimal YAML fallback when PyYAML is not available."""
    import re

    content = open(path, encoding="utf-8").read()
    data: dict = {}
    current_section: str | None = None
    repos: dict = {}

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key: value
        m = re.match(r"^(\w[\w_-]*):\s*(.+)$", stripped)
        if m and not line[0].isspace():
            key, val = m.group(1), m.group(2).strip()
            if key in ("repos", "detect", "links"):
                current_section = key
                continue
            data[key] = val.strip('"').strip("'")
            continue

        # Section items (indented)
        if current_section == "repos":
            m = re.match(r"^\s+([\w/]+):\s*(.+)$", line)
            if m:
                repos[m.group(1)] = m.group(2).strip()

    return GroupConfig(
        name=data.get("name", "unknown"),
        description=data.get("description", ""),
        repos=repos,
    )


def write_group_yaml(path: str, config: GroupConfig) -> None:
    """Write a GroupConfig to group.yaml."""
    try:
        import yaml
    except ImportError:
        _write_yaml_fallback(path, config)
        return

    detect = {}
    if config.detect_http:
        detect["http"] = True
    if config.detect_grpc:
        detect["grpc"] = True
    if config.detect_topics:
        detect["topics"] = True

    data = {
        "name": config.name,
    }
    if config.description:
        data["description"] = config.description
    if config.repos:
        data["repos"] = config.repos
    if detect:
        data["detect"] = detect
    if config.links:
        data["links"] = config.links

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def _write_yaml_fallback(path: str, config: GroupConfig) -> None:
    """Write group.yaml without PyYAML."""
    lines = [f"name: {config.name}"]
    if config.description:
        lines.append(f"description: {config.description}")
    if config.repos:
        lines.append("repos:")
        for gp, rn in config.repos.items():
            lines.append(f"  {gp}: {rn}")
    detect_parts = []
    if config.detect_http:
        detect_parts.append("http: true")
    if config.detect_grpc:
        detect_parts.append("grpc: true")
    if config.detect_topics:
        detect_parts.append("topics: true")
    if detect_parts:
        lines.append("detect:")
        lines.extend(f"  {p}" for p in detect_parts)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
