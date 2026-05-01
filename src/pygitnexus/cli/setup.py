"""Setup command: configure MCP for installed AI editors."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import click


def _resolve_binary_path() -> str | None:
    """Resolve the absolute path to the pygitnexus binary."""
    # Method 1: which command
    which = shutil.which("pygitnexus")
    if which:
        return os.path.abspath(which)
    # Method 2: sys.argv[0] (works for single-file binary)
    import sys
    argv0 = os.path.abspath(sys.argv[0])
    if os.path.basename(argv0) in ("pygitnexus", "pygitnexus.exe"):
        return argv0
    return None


def _dir_exists(path: Path) -> bool:
    return path.is_dir()


def _read_json(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
        # Strip JSONC comments (simple line comments)
        lines = text.splitlines()
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            # Remove trailing // comments (not inside strings)
            if "//" in line:
                in_string = False
                idx = -1
                for i, ch in enumerate(line):
                    if ch == '"' and (i == 0 or line[i-1] != '\\'):
                        in_string = not in_string
                    elif ch == '/' and not in_string and i + 1 < len(line) and line[i+1] == '/':
                        idx = i
                        break
                if idx >= 0:
                    line = line[:idx]
            clean_lines.append(line)
        cleaned = "\n".join(clean_lines)
        return json.loads(cleaned)
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _get_mcp_entry(bin_path: str) -> dict:
    return {"command": bin_path, "args": ["mcp"]}


def _get_opencode_mcp_entry(bin_path: str) -> dict:
    return {"type": "local", "command": [bin_path, "mcp"]}


def _merge_jsonc_like(path: Path, key_path: list[str], value: dict) -> bool:
    """Merge into JSON/JSONC file, creating or updating as needed."""
    existing = _read_json(path)
    if existing is None:
        existing = {}
    # Navigate and set
    obj = existing
    for key in key_path[:-1]:
        if key not in obj:
            obj[key] = {}
        obj = obj[key]
    obj[key_path[-1]] = value
    _write_json(path, existing)
    return True


# ─── Editor-specific setup ─────────────────────────────────────────

def _setup_cursor(result: dict, bin_path: str) -> None:
    cursor_dir = Path.home() / ".cursor"
    if not _dir_exists(cursor_dir):
        result["skipped"].append("Cursor (not installed)")
        return
    mcp_path = cursor_dir / "mcp.json"
    try:
        ok = _merge_jsonc_like(mcp_path, ["mcpServers", "pygitnexus"], _get_mcp_entry(bin_path))
        if ok:
            result["configured"].append("Cursor")
    except Exception as e:
        result["errors"].append(f"Cursor: {e}")


def _setup_claude_code(result: dict, bin_path: str) -> None:
    claude_dir = Path.home() / ".claude"
    if not _dir_exists(claude_dir):
        result["skipped"].append("Claude Code (not installed)")
        return
    mcp_path = Path.home() / ".claude.json"
    try:
        ok = _merge_jsonc_like(mcp_path, ["mcpServers", "pygitnexus"], _get_mcp_entry(bin_path))
        if ok:
            result["configured"].append("Claude Code")
    except Exception as e:
        result["errors"].append(f"Claude Code: {e}")


def _setup_opencode(result: dict, bin_path: str) -> None:
    opencode_dir = Path.home() / ".config" / "opencode"
    if not _dir_exists(opencode_dir):
        result["skipped"].append("OpenCode (not installed)")
        return
    config_path = opencode_dir / "opencode.json"
    try:
        ok = _merge_jsonc_like(config_path, ["mcp", "pygitnexus"], _get_opencode_mcp_entry(bin_path))
        if ok:
            result["configured"].append("OpenCode")
    except Exception as e:
        result["errors"].append(f"OpenCode: {e}")


def _setup_codex(result: dict, bin_path: str) -> None:
    codex_dir = Path.home() / ".codex"
    if not _dir_exists(codex_dir):
        result["skipped"].append("Codex (not installed)")
        return
    config_path = codex_dir / "config.toml"
    try:
        existing = ""
        try:
            existing = config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        if "[mcp_servers.pygitnexus]" in existing:
            result["configured"].append("Codex (already configured)")
            return
        section = (
            f'[mcp_servers.pygitnexus]\n'
            f'command = "{bin_path}"\n'
            f'args = ["mcp"]\n'
        )
        content = f"{existing.strip()}\n\n{section}" if existing.strip() else section
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(content, encoding="utf-8")
        result["configured"].append("Codex")
    except Exception as e:
        result["errors"].append(f"Codex: {e}")


@click.command("setup")
def setup_cmd() -> None:
    """One-time setup: configure MCP for Cursor, Claude Code, OpenCode, Codex.

    Detects installed AI editors and writes the appropriate MCP configuration
    so the PyGitNexus MCP server is available in all projects.
    """
    click.echo("")
    click.echo("  PyGitNexus Setup")
    click.echo("  ================")
    click.echo("")

    bin_path = _resolve_binary_path()
    if bin_path is None:
        click.echo("  Error: Could not resolve pygitnexus binary path.")
        click.echo("  Make sure pygitnexus is installed and on your PATH.")
        return

    click.echo(f"  Binary: {bin_path}")
    click.echo("")

    result = {
        "configured": [],
        "skipped": [],
        "errors": [],
    }

    _setup_cursor(result, bin_path)
    _setup_claude_code(result, bin_path)
    _setup_opencode(result, bin_path)
    _setup_codex(result, bin_path)

    if result["configured"]:
        click.echo("  Configured:")
        for name in result["configured"]:
            click.echo(f"    + {name}")

    if result["skipped"]:
        click.echo("")
        click.echo("  Skipped:")
        for name in result["skipped"]:
            click.echo(f"    - {name}")

    if result["errors"]:
        click.echo("")
        click.echo("  Errors:")
        for err in result["errors"]:
            click.echo(f"    ! {err}")

    click.echo("")
    click.echo("  Summary:")
    mcp_editors = [c for c in result["configured"] if "skills" not in c]
    click.echo(f"    MCP configured for: {', '.join(mcp_editors) or 'none'}")
    click.echo("")
    click.echo("  Next steps:")
    click.echo("    1. cd into any Java git repo")
    click.echo("    2. Run: pygitnexus analyze")
    click.echo("    3. Open the repo in your editor — MCP is ready!")
    click.echo("")
