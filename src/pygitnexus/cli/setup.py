"""Setup command: configure MCP, Hooks, and Skills for AI editors."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click


# ─── Path resolution ───────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".pygitnexus"
USER_CONFIG = CONFIG_DIR / "config.json"
USER_MCP = CONFIG_DIR / "mcp.json"
USER_HOOKS = CONFIG_DIR / "hooks.json"
SKILLS_DIR = CONFIG_DIR / "skills"


def _resolve_binary_path() -> str | None:
    """Resolve the absolute path to the pygitnexus binary."""
    which = shutil.which("pygitnexus")
    if which:
        return os.path.abspath(which)
    # Also check .exe extension (Windows)
    which_exe = shutil.which("pygitnexus.exe")
    if which_exe:
        return os.path.abspath(which_exe)
    # Method 2: sys.argv[0] (works for single-file binary)
    argv0 = os.path.abspath(sys.argv[0])
    base = os.path.basename(argv0)
    if base in ("pygitnexus", "pygitnexus.exe"):
        return argv0
    return None


def _read_json(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            if "//" in line:
                in_string = False
                idx = -1
                for i, ch in enumerate(line):
                    if ch == '"' and (i == 0 or line[i - 1] != '\\'):
                        in_string = not in_string
                    elif ch == '/' and not in_string and i + 1 < len(line) and line[i + 1] == '/':
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


def _ensure_config() -> dict:
    """Load or create user config."""
    config = _read_json(USER_CONFIG)
    if config is None:
        config = {}
    return config


def _save_config(config: dict) -> None:
    _write_json(USER_CONFIG, config)


# ─── MCP subcommand ────────────────────────────────────────────────

@click.group("mcp")
def mcp_cmd() -> None:
    """Manage MCP Server configurations."""


@mcp_cmd.command("add")
@click.argument("name")
@click.option("--command", "-c", required=True, help="Command to run the MCP server")
@click.option("--args", "-a", multiple=True, default=[], help="Command arguments (can repeat)")
@click.option("--env", "-e", multiple=True, default=[], help="Environment vars KEY=VALUE (can repeat)")
@click.option("--description", "-d", default="", help="Description of this MCP server")
def mcp_add(name: str, command: str, args: tuple[str, ...], env: tuple[str, ...], description: str) -> None:
    """Add an MCP Server configuration.

    Example:
        pygitnexus setup mcp add context7 \\
            --command npx \\
            -a -y \\
            -a @anthropic-ai/context7 \\
            -d "Library documentation lookup"
    """
    config = _ensure_config()
    mcp_servers = config.setdefault("mcpServers", {})

    env_dict = {}
    for item in env:
        if "=" in item:
            k, v = item.split("=", 1)
            env_dict[k] = v

    entry: dict = {
        "type": "stdio",
        "command": command,
        "args": list(args),
    }
    if env_dict:
        entry["env"] = env_dict
    if description:
        entry["description"] = description

    mcp_servers[name] = entry
    _save_config(config)
    click.echo(f"  MCP Server '{name}' added successfully.")
    click.echo(f"    command: {command} {' '.join(args)}")
    if env_dict:
        click.echo(f"    env: {env_dict}")
    if description:
        click.echo(f"    description: {description}")


@mcp_cmd.command("list")
def mcp_list() -> None:
    """List all configured MCP Servers."""
    config = _ensure_config()
    mcp_servers = config.get("mcpServers", {})
    if not mcp_servers:
        click.echo("  No MCP Servers configured.")
        return
    click.echo(f"  MCP Servers ({len(mcp_servers)}):")
    for name, entry in mcp_servers.items():
        cmd = entry.get("command", "unknown")
        args_str = " ".join(entry.get("args", []))
        desc = entry.get("description", "")
        click.echo(f"    [{name}] {cmd} {args_str}")
        if desc:
            click.echo(f"           {desc}")


@mcp_cmd.command("remove")
@click.argument("name")
def mcp_remove(name: str) -> None:
    """Remove an MCP Server configuration."""
    config = _ensure_config()
    mcp_servers = config.get("mcpServers", {})
    if name not in mcp_servers:
        click.echo(f"  MCP Server '{name}' not found.")
        return
    del mcp_servers[name]
    _save_config(config)
    click.echo(f"  MCP Server '{name}' removed.")


@mcp_cmd.command("export")
@click.option("--editor", "-e", type=click.Choice(["cursor", "claude-code", "codebuddy", "opencode", "codex", "all"]),
              default="all", help="Target editor for export")
def mcp_export(editor: str) -> None:
    """Export MCP config to editor-specific format."""
    config = _ensure_config()
    mcp_servers = config.get("mcpServers", {})
    if not mcp_servers:
        click.echo("  No MCP Servers configured. Use 'setup mcp add' first.")
        return

    editors = ["cursor", "claude-code", "codebuddy", "opencode", "codex"] if editor == "all" else [editor]

    for ed in editors:
        _export_to_editor(mcp_servers, ed)


def _export_to_editor(mcp_servers: dict, editor: str) -> None:
    """Export MCP servers to a specific editor's config."""
    bin_path = _resolve_binary_path()
    if bin_path is None:
        click.echo(f"  Skip {editor}: cannot resolve pygitnexus binary")
        return

    if editor == "cursor":
        path = Path.home() / ".cursor" / "mcp.json"
        _merge_mcp_json(path, lambda existing: {
            **existing,
            "mcpServers": {**existing.get("mcpServers", {}), **mcp_servers}
        })
    elif editor == "claude-code":
        path = Path.home() / ".claude.json"
        _merge_mcp_json(path, lambda existing: {
            **existing,
            "mcpServers": {**existing.get("mcpServers", {}), **mcp_servers}
        })
    elif editor == "codebuddy":
        path = Path.home() / ".codebuddy" / "settings.json"
        _merge_mcp_json(path, lambda existing: {
            **existing,
            "mcpServers": {**existing.get("mcpServers", {}), **mcp_servers}
        })
    elif editor == "opencode":
        path = Path.home() / ".config" / "opencode" / "opencode.json"
        _merge_mcp_json(path, lambda existing: {
            **existing,
            "mcp": {**existing.get("mcp", {}), **{
                name: {"type": "local", "command": [entry["command"]] + entry.get("args", [])
                       } for name, entry in mcp_servers.items()
            }}
        })
    elif editor == "codex":
        codex_dir = Path.home() / ".codex"
        config_path = codex_dir / "config.toml"
        existing = ""
        try:
            existing = config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        sections = []
        for name, entry in mcp_servers.items():
            section_key = f"[mcp_servers.{name}]"
            if section_key in existing:
                continue
            cmd = entry["command"]
            args = " ".join(f'"{a}"' for a in entry.get("args", []))
            sections.append(f"{section_key}\ncommand = \"{cmd}\"\nargs = [{args}]\n")
        if sections:
            content = f"{existing.strip()}\n\n" + "\n".join(sections) if existing.strip() else "\n".join(sections)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content, encoding="utf-8")
            click.echo(f"  Exported to Codex: {config_path}")

    if editor in ("cursor", "claude-code", "opencode"):
        click.echo(f"  Exported to {editor}")


def _merge_mcp_json(path: Path, merge_fn) -> None:
    existing = _read_json(path) or {}
    merged = merge_fn(existing)
    _write_json(path, merged)


# ─── Hook subcommand ───────────────────────────────────────────────

HOOK_EVENTS = [
    "SessionStart", "SessionEnd", "PreToolUse", "PostToolUse",
    "UserPromptSubmit", "Stop", "PreCompact",
]

@click.group("hook")
def hook_cmd() -> None:
    """Manage Hook configurations."""


@hook_cmd.command("add")
@click.argument("event", type=click.Choice(HOOK_EVENTS, case_sensitive=True))
@click.option("--command", "-c", required=True, help="Hook script path")
@click.option("--matcher", "-m", default="", help="Matcher pattern (regex, empty=match all)")
@click.option("--timeout", "-t", default=60, type=int, help="Timeout in seconds (default: 60)")
def hook_add(event: str, command: str, matcher: str, timeout: int) -> None:
    """Add a Hook configuration.

    Example:
        pygitnexus setup hook add SessionStart \\
            --command "$HOME/.pygitnexus/hooks/session_start.py" \\
            --matcher startup --timeout 30

        pygitnexus setup hook add PreToolUse \\
            --command "$HOME/.pygitnexus/hooks/validate.py" \\
            --matcher "Bash" --timeout 10
    """
    config = _ensure_config()
    hooks = config.setdefault("hooks", {})
    event_list = hooks.setdefault(event, [])

    # Check for duplicate
    for entry in event_list:
        if entry.get("matcher") == matcher:
            existing_commands = [h.get("command") for h in entry.get("hooks", [])]
            if command in existing_commands:
                click.echo(f"  Hook already exists: {event} / matcher='{matcher}' / command={command}")
                return

    new_entry = {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": timeout,
            }
        ]
    }
    event_list.append(new_entry)
    _save_config(config)
    click.echo(f"  Hook added: {event}")
    click.echo(f"    matcher: '{matcher}'")
    click.echo(f"    command: {command}")
    click.echo(f"    timeout: {timeout}s")


@hook_cmd.command("list")
@click.argument("event", type=click.Choice(HOOK_EVENTS, case_sensitive=True), required=False)
def hook_list(event: str | None) -> None:
    """List Hook configurations."""
    config = _ensure_config()
    hooks = config.get("hooks", {})
    if not hooks:
        click.echo("  No Hooks configured.")
        return

    events_to_show = [event] if event else HOOK_EVENTS
    for evt in events_to_show:
        entries = hooks.get(evt, [])
        click.echo(f"  {evt} ({len(entries)}):")
        if not entries:
            click.echo("    (none)")
            continue
        for i, entry in enumerate(entries):
            matcher = entry.get("matcher", "")
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                timeout = h.get("timeout", 60)
                click.echo(f"    [{i}] matcher='{matcher}' | {cmd} | {timeout}s")


@hook_cmd.command("remove")
@click.argument("event", type=click.Choice(HOOK_EVENTS, case_sensitive=True))
@click.option("--index", "-i", required=True, type=int, help="Index of hook to remove (from list)")
def hook_remove(event: str, index: int) -> None:
    """Remove a Hook configuration by index."""
    config = _ensure_config()
    hooks = config.get("hooks", {})
    event_list = hooks.get(event, [])
    if not event_list or index < 0 or index >= len(event_list):
        click.echo(f"  No hook at {event}[{index}].")
        return
    removed = event_list.pop(index)
    if not event_list:
        del hooks[event]
    _save_config(config)
    click.echo(f"  Hook removed: {event}[{index}]")
    click.echo(f"    matcher: '{removed.get('matcher', '')}'")


def _sample_hooks() -> dict:
    return {
        "SessionStart": [
            {
                "matcher": "startup",
                "hooks": [
                    {
                        "type": "command",
                        "command": str(CONFIG_DIR / "hooks" / "session_start.py"),
                        "timeout": 30,
                    }
                ]
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": str(CONFIG_DIR / "hooks" / "validate_command.py"),
                        "timeout": 10,
                    }
                ]
            }
        ],
        "PreCompact": [
            {
                "matcher": "auto",
                "hooks": [
                    {
                        "type": "command",
                        "command": str(CONFIG_DIR / "hooks" / "save_context.py"),
                        "timeout": 20,
                    }
                ]
            }
        ],
    }


@hook_cmd.command("init")
@click.option("--project", is_flag=True, default=False, help="Create project-level config (.pygitnexus/hooks.json)")
def hook_init(project: bool) -> None:
    """Generate a sample hooks.json with common patterns."""
    sample = _sample_hooks()
    if project:
        target = Path(".pygitnexus") / "hooks.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_json(target, sample)
        click.echo(f"  Sample hooks created at: {target}")
    else:
        config = _ensure_config()
        config["hooks"] = sample
        _save_config(config)
        click.echo(f"  Sample hooks written to user config: {USER_CONFIG}")
    click.echo(f"  Run 'pygitnexus setup hook list' to view configured hooks.")


# ─── Skill subcommand ──────────────────────────────────────────────

@click.group("skill")
def skill_cmd() -> None:
    """Manage Skills (modular capability packages)."""


@skill_cmd.command("init")
@click.argument("name")
@click.option("--description", "-d", required=True, help="Description of when this skill should be used")
@click.option("--project", is_flag=True, default=False, help="Create in project-level .pygitnexus/skills/")
def skill_init(name: str, description: str, project: bool) -> None:
    """Create a new Skill with SKILL.md template.

    Example:
        pygitnexus setup skill init pdf-editor \\
            --description "Handle PDF file operations like rotation, text extraction"
    """
    if project:
        base = Path(".pygitnexus") / "skills" / name
    else:
        base = SKILLS_DIR / name

    base.mkdir(parents=True, exist_ok=True)
    (base / "scripts").mkdir(exist_ok=True)
    (base / "references").mkdir(exist_ok=True)
    (base / "assets").mkdir(exist_ok=True)

    skill_md = f"""---
name: {name}
description: {description}
---

# {name}

<!-- Add your skill instructions here -->

## Overview

{description}

## Usage

Add detailed instructions for using this skill below.
Use imperative language (e.g., "To accomplish X, do Y").

## Scripts

<!-- Reference any scripts in scripts/ directory -->

## References

<!-- Reference any docs in references/ directory -->
"""
    (base / "SKILL.md").write_text(skill_md, encoding="utf-8")
    click.echo(f"  Skill '{name}' created at: {base}")
    click.echo(f"    SKILL.md       — main instructions")
    click.echo(f"    scripts/       — executable scripts")
    click.echo(f"    references/    — reference documentation")
    click.echo(f"    assets/        — output assets/templates")


@skill_cmd.command("list")
@click.option("--project", is_flag=True, default=False, help="List project-level skills (.pygitnexus/skills/)")
def skill_list_cmd(project: bool) -> None:
    """List installed Skills."""
    if project:
        base = Path(".pygitnexus") / "skills"
    else:
        base = SKILLS_DIR

    if not base.is_dir():
        click.echo(f"  No Skills directory at: {base}")
        return

    skills = [d for d in base.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]
    if not skills:
        click.echo(f"  No Skills installed in: {base}")
        return

    click.echo(f"  Skills ({len(skills)}):")
    for skill_dir in sorted(skills):
        name = skill_dir.name
        desc = _read_skill_description(skill_dir / "SKILL.md")
        click.echo(f"    [{name}] {desc}")


@skill_cmd.command("remove")
@click.argument("name")
@click.option("--project", is_flag=True, default=False, help="Remove from project-level skills")
def skill_remove(name: str, project: bool) -> None:
    """Remove an installed Skill."""
    if project:
        base = Path(".pygitnexus") / "skills"
    else:
        base = SKILLS_DIR

    skill_dir = base / name
    if not skill_dir.is_dir():
        click.echo(f"  Skill '{name}' not found in: {base}")
        return

    shutil.rmtree(skill_dir)
    click.echo(f"  Skill '{name}' removed from: {base}")


@skill_cmd.command("show")
@click.argument("name")
@click.option("--project", is_flag=True, default=False, help="Show project-level skill")
def skill_show(name: str, project: bool) -> None:
    """Show details of a Skill."""
    if project:
        base = Path(".pygitnexus") / "skills"
    else:
        base = SKILLS_DIR

    skill_dir = base / name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        click.echo(f"  Skill '{name}' not found.")
        return

    click.echo(f"  Skill: {name}")
    click.echo(f"  Location: {skill_dir}")
    click.echo("")
    click.echo("  SKILL.md content:")
    click.echo("  " + "-" * 40)
    click.echo(f"  {skill_md.read_text(encoding='utf-8')}")
    click.echo("  " + "-" * 40)

    # List resources
    for subdir in ["scripts", "references", "assets"]:
        sd = skill_dir / subdir
        if sd.is_dir():
            files = list(sd.iterdir())
            if files:
                click.echo(f"  {subdir}/: {', '.join(f.name for f in sorted(files))}")


def _read_skill_description(skill_md_path: Path) -> str:
    """Extract description from SKILL.md frontmatter."""
    try:
        text = skill_md_path.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 2:
                for line in parts[1].splitlines():
                    if line.startswith("description:"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "(no description)"


# ─── Main setup command (legacy: auto-configure all editors) ───────

def _dir_exists(path: Path) -> bool:
    return path.is_dir()


def _get_mcp_entry(bin_path: str) -> dict:
    return {"command": bin_path, "args": ["mcp"]}


def _get_opencode_mcp_entry(bin_path: str) -> dict:
    return {"type": "local", "command": [bin_path, "mcp"]}


def _merge_jsonc_like(path: Path, key_path: list[str], value: dict) -> bool:
    existing = _read_json(path)
    if existing is None:
        existing = {}
    obj = existing
    for key in key_path[:-1]:
        if key not in obj:
            obj[key] = {}
        obj = obj[key]
    obj[key_path[-1]] = value
    _write_json(path, existing)
    return True


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
    # Windows: OpenCode may use APPDATA or user home
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            opencode_dir = Path(appdata) / "opencode"
        else:
            opencode_dir = Path.home() / ".config" / "opencode"
    else:
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


def _setup_codebuddy(result: dict, bin_path: str) -> None:
    """Configure CodeBuddy user-level MCP."""
    codebuddy_dir = Path.home() / ".codebuddy"
    if not _dir_exists(codebuddy_dir):
        result["skipped"].append("CodeBuddy (not installed)")
        return
    config_path = codebuddy_dir / "settings.json"
    try:
        ok = _merge_jsonc_like(config_path, ["mcpServers", "pygitnexus"], _get_mcp_entry(bin_path))
        if ok:
            result["configured"].append("CodeBuddy")
    except Exception as e:
        result["errors"].append(f"CodeBuddy: {e}")


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


@click.group("setup", invoke_without_command=True)
@click.option("--project", is_flag=True, default=False, help="Configure project-level MCP (in cwd)")
@click.pass_context
def setup_cmd(ctx, project: bool) -> None:
    """Setup: configure MCP for AI editors, manage MCP/Hooks/Skills.

    Without subcommand: auto-detect and configure MCP for Cursor / Claude Code / CodeBuddy / OpenCode / Codex.
    With --project: also write <cwd>/.codebuddy/settings.json for project-level CodeBuddy.
    Subcommands: mcp, hook, skill — fine-grained management.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(setup_auto, project=project)


@setup_cmd.command("auto")
@click.option("--project", is_flag=True, default=False, help="Configure project-level MCP (in cwd)")
def setup_auto(project: bool) -> None:
    """Auto-detect and configure MCP for Cursor / Claude Code / CodeBuddy / OpenCode / Codex."""
    click.echo("")
    click.echo("  PyGitNexus Setup")
    click.echo("  ================")
    click.echo("")

    bin_path = _resolve_binary_path()
    if bin_path is None:
        click.echo("  Error: Could not resolve pygitnexus binary path.")
        click.echo("  Make sure pygitnexus is installed and on your PATH.")
        return

    # On Windows, use forward slashes for MCP config (cross-platform compatible)
    if sys.platform == "win32":
        bin_path = bin_path.replace("\\", "/")

    click.echo(f"  Binary: {bin_path}")
    click.echo("")

    result = {
        "configured": [],
        "skipped": [],
        "errors": [],
    }

    _setup_cursor(result, bin_path)
    _setup_claude_code(result, bin_path)
    _setup_codebuddy(result, bin_path)
    _setup_opencode(result, bin_path)
    _setup_codex(result, bin_path)

    # Project-level: write <cwd>/.codebuddy/settings.json for CodeBuddy
    if project:
        project_cb = Path(".codebuddy") / "settings.json"
        try:
            ok = _merge_jsonc_like(project_cb, ["mcpServers", "pygitnexus"], _get_mcp_entry(bin_path))
            if ok:
                result["configured"].append(f"CodeBuddy (project: {project_cb})")
        except Exception as e:
            result["errors"].append(f"CodeBuddy (project): {e}")

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


# Register subcommands
setup_cmd.add_command(mcp_cmd)
setup_cmd.add_command(hook_cmd)
setup_cmd.add_command(skill_cmd)
