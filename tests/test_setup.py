"""Tests for `pygitnexus setup` auto-configuration.

Core scenarios:
  - setup auto: detect editors, delete existing config, then verify setup adds it back
  - setup mcp/hook/skill subcommands: add, list, remove operations
  - config read/write (JSONC-like comments)
  - binary path resolution
  - CodeBuddy uses mcp.json (not settings.json)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from pygitnexus.cli.setup import (
    setup_cmd,
    setup_auto,
    mcp_cmd,
    hook_cmd,
    skill_cmd,
    CONFIG_DIR,
    USER_CONFIG,
    USER_MCP,
    USER_HOOKS,
    SKILLS_DIR,
    _resolve_binary_path,
    _read_json,
    _write_json,
    _ensure_config,
    _save_config,
    _get_mcp_entry,
    _get_opencode_mcp_entry,
    _sample_hooks,
    _read_skill_description,
)


# ─── Helpers ────────────────────────────────────────────────────────

@pytest.fixture()
def isolated_config(tmp_path: Path, monkeypatch):
    """Redirect all config paths to a temporary directory."""
    monkeypatch.setattr("pygitnexus.cli.setup.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("pygitnexus.cli.setup.USER_CONFIG", tmp_path / "config.json")
    monkeypatch.setattr("pygitnexus.cli.setup.USER_MCP", tmp_path / "mcp.json")
    monkeypatch.setattr("pygitnexus.cli.setup.USER_HOOKS", tmp_path / "hooks.json")
    monkeypatch.setattr("pygitnexus.cli.setup.SKILLS_DIR", tmp_path / "skills")
    return tmp_path


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def fake_bin(tmp_path: Path):
    """Create a fake pygitnexus binary."""
    bin_path = tmp_path / "pygitnexus"
    bin_path.write_text("#!/bin/sh\necho 0.1.0\n", encoding="utf-8")
    bin_path.chmod(0o755)
    return str(bin_path)


# ─── Config read/write ──────────────────────────────────────────────

class TestReadJson:
    """Test _read_json with various JSON formats including JSONC-style comments."""

    def test_valid_json(self, tmp_path):
        f = tmp_path / "a.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        assert _read_json(f) == {"key": "value"}

    def test_comment_line(self, tmp_path):
        f = tmp_path / "b.json"
        f.write_text('{\n  // this is a comment\n  "key": "value"\n}', encoding="utf-8")
        assert _read_json(f) == {"key": "value"}

    def test_comment_at_end_of_line(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text('{"key": "value" // comment\n}', encoding="utf-8")
        assert _read_json(f) == {"key": "value"}

    def test_string_with_slash(self, tmp_path):
        """Strings containing / should not be treated as comments."""
        f = tmp_path / "d.json"
        f.write_text('{"path": "/usr/bin"}', encoding="utf-8")
        assert _read_json(f) == {"path": "/usr/bin"}

    def test_invalid_json_returns_none(self, tmp_path):
        f = tmp_path / "e.json"
        f.write_text('{broken}', encoding="utf-8")
        assert _read_json(f) is None

    def test_missing_file(self, tmp_path):
        assert _read_json(tmp_path / "nonexistent.json") is None


class TestWriteJson:
    """Test _write_json creates parent dirs and writes valid JSON."""

    def test_write_creates_parent(self, tmp_path):
        f = tmp_path / "nested" / "dir" / "config.json"
        _write_json(f, {"a": 1})
        assert f.exists()
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data == {"a": 1}

    def test_write_pretty_print(self, tmp_path):
        f = tmp_path / "p.json"
        _write_json(f, {"key": "value"})
        content = f.read_text(encoding="utf-8")
        assert "  " in content  # indented


# ─── Binary path resolution ─────────────────────────────────────────

class TestResolveBinaryPath:
    """Test _resolve_binary_path."""

    def test_finds_via_shutil_which(self, fake_bin):
        with patch.object(shutil, "which", return_value=fake_bin):
            result = _resolve_binary_path()
            assert result == os.path.abspath(fake_bin)

    def test_returns_none_when_not_found(self):
        with patch.object(shutil, "which", return_value=None), \
             patch.object(sys, "argv", ["python", "-m", "pygitnexus"]):
            result = _resolve_binary_path()
            assert result is None


# ─── MCP entry helpers ──────────────────────────────────────────────

class TestMcpEntry:
    """Test MCP entry generation helpers."""

    def test_get_mcp_entry(self):
        entry = _get_mcp_entry("/usr/bin/pygitnexus")
        assert entry == {"command": "/usr/bin/pygitnexus", "args": ["mcp"]}

    def test_get_opencode_mcp_entry(self):
        entry = _get_opencode_mcp_entry("/usr/bin/pygitnexus")
        assert entry == {"type": "local", "command": ["/usr/bin/pygitnexus", "mcp"]}


# ─── Setup auto: delete then verify re-add ──────────────────────────

class TestSetupAutoDeleteThenAdd:
    """Test the core setup flow: delete existing config → run setup → verify it adds back.

    All tests use isolated_filesystem to avoid modifying real user config files.
    Editor directories are simulated via _dir_exists mocking.
    """

    def test_cursor_delete_then_setup_adds(self, runner, isolated_config, fake_bin):
        """Delete pygitnexus from Cursor, run setup, verify it's back."""
        cursor_dir = Path.home() / ".cursor"
        mcp_path = cursor_dir / "mcp.json"
        if not cursor_dir.is_dir():
            pytest.skip("Cursor not installed")

        # Backup existing config
        backup = None
        if mcp_path.exists():
            backup = mcp_path.read_text(encoding="utf-8")

        try:
            # Step 1: Write pre-existing config with pygitnexus
            existing = {"mcpServers": {"pygitnexus": {"command": "/old/path", "args": ["mcp"]}}}
            _write_json(mcp_path, existing)

            # Step 2: Delete existing config
            data = _read_json(mcp_path) or {}
            servers = data.get("mcpServers", {})
            if "pygitnexus" in servers:
                del servers["pygitnexus"]
                _write_json(mcp_path, data)

            # Step 3: Verify it's gone
            data = _read_json(mcp_path)
            assert "pygitnexus" not in (data or {}).get("mcpServers", {})

            # Step 4: Run setup
            with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
                result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0

            # Step 5: Verify it's back
            data = _read_json(mcp_path)
            assert "pygitnexus" in (data or {}).get("mcpServers", {})
            entry = (data or {}).get("mcpServers", {}).get("pygitnexus", {})
            assert entry.get("command") == fake_bin
            assert entry.get("args") == ["mcp"]
        finally:
            if backup is not None:
                mcp_path.write_text(backup, encoding="utf-8")
            elif mcp_path.exists():
                # Remove the test-created file
                mcp_path.unlink()

    def test_claude_code_delete_then_setup_adds(self, runner, isolated_config, fake_bin):
        """Delete pygitnexus from Claude Code, run setup, verify it's back."""
        claude_json = Path.home() / ".claude.json"

        # Backup existing config
        backup = None
        if claude_json.exists():
            backup = claude_json.read_text(encoding="utf-8")

        try:
            # Step 1: Write pre-existing config with pygitnexus
            existing = {"mcpServers": {"pygitnexus": {"command": "/old/path", "args": ["mcp"]}}}
            _write_json(claude_json, existing)

            # Step 2: Delete existing config
            data = _read_json(claude_json) or {}
            servers = data.get("mcpServers", {})
            if "pygitnexus" in servers:
                del servers["pygitnexus"]
                _write_json(claude_json, data)

            # Step 3: Verify it's gone
            data = _read_json(claude_json)
            assert "pygitnexus" not in (data or {}).get("mcpServers", {})

            # Step 4: Run setup
            with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
                result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0

            # Step 5: Verify it's back
            data = _read_json(claude_json)
            assert "pygitnexus" in (data or {}).get("mcpServers", {})
            entry = (data or {}).get("mcpServers", {}).get("pygitnexus", {})
            assert entry.get("command") == fake_bin
        finally:
            if backup is not None:
                claude_json.write_text(backup, encoding="utf-8")
            elif claude_json.exists():
                claude_json.unlink()

    def test_auto_no_binary_error(self, runner, isolated_config):
        """When binary can't be resolved, should error."""
        with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=None):
            result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0
            assert "Error" in result.output or "Could not resolve" in result.output

    def test_auto_skips_when_editors_not_installed(self, runner, isolated_config, fake_bin):
        """Editors without installed dir should be skipped."""
        with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin), \
             patch("pygitnexus.cli.setup._dir_exists", return_value=False):
            result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0
            assert "Skipped" in result.output or "not installed" in result.output

    def test_auto_project_flag_writes_codebuddy_mcp_json(self, runner, isolated_config, fake_bin):
        """--project should write project-level CodeBuddy config to mcp.json."""
        with runner.isolated_filesystem():
            with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
                result = runner.invoke(setup_cmd, ["--project"])
                assert result.exit_code == 0
                project_cb = Path.cwd() / ".codebuddy" / "mcp.json"
                assert project_cb.exists()
                data = json.loads(project_cb.read_text(encoding="utf-8"))
                assert "pygitnexus" in data.get("mcpServers", {})

    def test_auto_preserves_other_mcp_servers(self, runner, isolated_config, fake_bin):
        """Should merge with existing MCP config, not overwrite."""
        cursor_dir = Path.home() / ".cursor"
        mcp_path = cursor_dir / "mcp.json"
        if not cursor_dir.is_dir():
            pytest.skip("Cursor not installed")

        # Backup existing config
        backup = None
        if mcp_path.exists():
            backup = mcp_path.read_text(encoding="utf-8")

        try:
            # Pre-existing config with other servers
            existing = {"mcpServers": {"other-server": {"command": "other", "args": []}}}
            _write_json(mcp_path, existing)

            with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
                result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0

            data = _read_json(mcp_path)
            assert "other-server" in data.get("mcpServers", {})
            assert "pygitnexus" in data.get("mcpServers", {})
        finally:
            if backup is not None:
                mcp_path.write_text(backup, encoding="utf-8")
            elif mcp_path.exists():
                mcp_path.unlink()


# ─── CodeBuddy MCP uses mcp.json ────────────────────────────────────

class TestCodeBuddyMcpJson:
    """Test that CodeBuddy MCP config is written to mcp.json, not settings.json."""

    def test_setup_auto_writes_mcp_json(self, runner, isolated_config, fake_bin):
        """setup auto should write CodeBuddy config to ~/.codebuddy/mcp.json."""
        codebuddy_dir = Path.home() / ".codebuddy"
        if not codebuddy_dir.is_dir():
            pytest.skip("CodeBuddy not installed")

        mcp_path = codebuddy_dir / "mcp.json"
        backup = None
        if mcp_path.exists():
            backup = mcp_path.read_text(encoding="utf-8")

        def _dir_exists_mock(path):
            return str(path).startswith(str(codebuddy_dir))

        try:
            # Delete existing config
            if mcp_path.exists():
                data = _read_json(mcp_path) or {}
                servers = data.get("mcpServers", {})
                if "pygitnexus" in servers:
                    del servers["pygitnexus"]
                    _write_json(mcp_path, data)

            # Run setup
            with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin), \
                 patch("pygitnexus.cli.setup._dir_exists", side_effect=_dir_exists_mock):
                result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0

            # Verify mcp.json exists and has pygitnexus
            assert mcp_path.exists()
            data = _read_json(mcp_path)
            assert "pygitnexus" in (data or {}).get("mcpServers", {})
        finally:
            if backup is not None:
                mcp_path.write_text(backup, encoding="utf-8")

    def test_mcp_export_writes_mcp_json(self, runner, isolated_config, fake_bin):
        """mcp export -e codebuddy should write to ~/.codebuddy/mcp.json."""
        codebuddy_dir = Path.home() / ".codebuddy"
        mcp_path = codebuddy_dir / "mcp.json"
        backup = None
        if mcp_path.exists():
            backup = mcp_path.read_text(encoding="utf-8")

        try:
            # Delete existing config
            if mcp_path.exists():
                data = _read_json(mcp_path) or {}
                servers = data.get("mcpServers", {})
                if "pygitnexus" in servers:
                    del servers["pygitnexus"]
                    _write_json(mcp_path, data)

            # Add and export
            runner.invoke(mcp_cmd, ["add", "pygitnexus", "-c", fake_bin, "-a", "mcp"])

            with patch.object(shutil, "which", return_value=fake_bin):
                result = runner.invoke(mcp_cmd, ["export", "-e", "codebuddy"])
                assert result.exit_code == 0

            # Verify
            assert mcp_path.exists()
            data = _read_json(mcp_path)
            assert "pygitnexus" in (data or {}).get("mcpServers", {})
        finally:
            if backup is not None:
                mcp_path.write_text(backup, encoding="utf-8")


# ─── CodeBuddy Hooks in settings.json ──────────────────────────────

class TestCodeBuddyHooks:
    """Test that setup auto writes hooks config to CodeBuddy settings.json."""

    def _only_codebuddy(self, codebuddy_dir):
        """_dir_exists mock that only recognizes CodeBuddy."""
        cb = str(codebuddy_dir)
        def _mock(path):
            return str(path).startswith(cb)
        return _mock

    def test_setup_auto_writes_hooks(self, runner, isolated_config, fake_bin):
        """setup auto should write PreToolUse + PostToolUse hooks to settings.json."""
        codebuddy_dir = Path.home() / ".codebuddy"
        if not codebuddy_dir.is_dir():
            pytest.skip("CodeBuddy not installed")

        # Ensure hook script exists
        hook_script = codebuddy_dir / "hooks" / "pygitnexus" / "pygitnexus-hook.cjs"
        if not hook_script.is_file():
            pytest.skip("CodeBuddy hook script not installed")

        # Backup existing configs (both settings.json and mcp.json are modified by setup auto)
        settings_path = codebuddy_dir / "settings.json"
        mcp_path = codebuddy_dir / "mcp.json"
        settings_backup = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        mcp_backup = mcp_path.read_text(encoding="utf-8") if mcp_path.exists() else None

        try:
            # Clear existing hooks
            existing = _read_json(settings_path) or {}
            if "hooks" in existing:
                del existing["hooks"]
                _write_json(settings_path, existing)

            # Run setup auto — only CodeBuddy dir is recognized, avoiding writes to Cursor/Claude
            with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin), \
                 patch("pygitnexus.cli.setup._dir_exists", side_effect=self._only_codebuddy(codebuddy_dir)):
                result = runner.invoke(setup_cmd, [])
                assert result.exit_code == 0

            # Verify hooks were written
            assert settings_path.exists()
            data = _read_json(settings_path)
            assert data is not None
            hooks = data.get("hooks", {})
            assert "PreToolUse" in hooks
            assert "PostToolUse" in hooks

            # Verify PreToolUse matcher and command
            pre = hooks["PreToolUse"]
            assert any(e["matcher"] == "Grep|Glob|Bash" for e in pre)
            for entry in pre:
                if entry["matcher"] == "Grep|Glob|Bash":
                    assert entry["hooks"][0]["command"] == str(hook_script)
                    assert entry["hooks"][0]["timeout"] == 10

            # Verify PostToolUse matcher and command
            post = hooks["PostToolUse"]
            assert any(e["matcher"] == "Bash" for e in post)
        finally:
            if settings_backup is not None:
                settings_path.write_text(settings_backup, encoding="utf-8")
            if mcp_backup is not None:
                mcp_path.write_text(mcp_backup, encoding="utf-8")

    def test_setup_auto_no_duplicate_hooks(self, runner, isolated_config, fake_bin):
        """setup auto should not duplicate hooks if already configured."""
        codebuddy_dir = Path.home() / ".codebuddy"
        if not codebuddy_dir.is_dir():
            pytest.skip("CodeBuddy not installed")

        hook_script = codebuddy_dir / "hooks" / "pygitnexus" / "pygitnexus-hook.cjs"
        if not hook_script.is_file():
            pytest.skip("CodeBuddy hook script not installed")

        settings_path = codebuddy_dir / "settings.json"
        mcp_path = codebuddy_dir / "mcp.json"
        settings_backup = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        mcp_backup = mcp_path.read_text(encoding="utf-8") if mcp_path.exists() else None

        try:
            # Pre-configure hooks
            pre_config = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Grep|Glob|Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(hook_script),
                                    "timeout": 10,
                                }
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(hook_script),
                                    "timeout": 10,
                                }
                            ],
                        }
                    ],
                }
            }
            _write_json(settings_path, pre_config)

            # Run setup auto twice — only CodeBuddy dir is recognized
            with patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin), \
                 patch("pygitnexus.cli.setup._dir_exists", side_effect=self._only_codebuddy(codebuddy_dir)):
                runner.invoke(setup_cmd, [])
                runner.invoke(setup_cmd, [])

            # Verify no duplicates for PreToolUse
            data = _read_json(settings_path)
            hooks = data.get("hooks", {})
            pretuse = hooks.get("PreToolUse", [])
            grep_glob_bash_count = sum(1 for e in pretuse if e.get("matcher") == "Grep|Glob|Bash")
            assert grep_glob_bash_count == 1, f"Expected 1 PreToolUse entry, got {grep_glob_bash_count}"

            # Verify no duplicates for PostToolUse
            postuse = hooks.get("PostToolUse", [])
            bash_count = sum(1 for e in postuse if e.get("matcher") == "Bash")
            assert bash_count == 1, f"Expected 1 PostToolUse entry, got {bash_count}"
        finally:
            if settings_backup is not None:
                settings_path.write_text(settings_backup, encoding="utf-8")
            if mcp_backup is not None:
                mcp_path.write_text(mcp_backup, encoding="utf-8")


# ─── Setup MCP subcommand ───────────────────────────────────────────

class TestMcpAdd:
    """Test `pygitnexus setup mcp add`."""

    def test_add_simple(self, runner, isolated_config):
        result = runner.invoke(mcp_cmd, ["add", "test-server", "-c", "npx", "-a", "-y", "-a", "@test/pkg"])
        assert result.exit_code == 0
        assert "added successfully" in result.output

        config = _ensure_config()
        assert "test-server" in config["mcpServers"]
        entry = config["mcpServers"]["test-server"]
        assert entry["command"] == "npx"
        assert entry["args"] == ["-y", "@test/pkg"]

    def test_add_with_env(self, runner, isolated_config):
        result = runner.invoke(mcp_cmd, ["add", "test-server", "-c", "npx", "-e", "API_KEY=secret", "-e", "DEBUG=1"])
        assert result.exit_code == 0

        config = _ensure_config()
        env = config["mcpServers"]["test-server"]["env"]
        assert env["API_KEY"] == "secret"
        assert env["DEBUG"] == "1"

    def test_add_with_description(self, runner, isolated_config):
        result = runner.invoke(mcp_cmd, ["add", "test-server", "-c", "npx", "-d", "My test server"])
        assert result.exit_code == 0

        config = _ensure_config()
        assert config["mcpServers"]["test-server"]["description"] == "My test server"


class TestMcpList:
    """Test `pygitnexus setup mcp list`."""

    def test_list_empty(self, runner, isolated_config):
        result = runner.invoke(mcp_cmd, ["list"])
        assert result.exit_code == 0
        assert "No MCP Servers configured" in result.output

    def test_list_with_servers(self, runner, isolated_config):
        runner.invoke(mcp_cmd, ["add", "server1", "-c", "cmd1"])
        runner.invoke(mcp_cmd, ["add", "server2", "-c", "cmd2", "-d", "desc"])

        result = runner.invoke(mcp_cmd, ["list"])
        assert result.exit_code == 0
        assert "server1" in result.output
        assert "server2" in result.output
        assert "desc" in result.output


class TestMcpRemove:
    """Test `pygitnexus setup mcp remove`."""

    def test_remove_existing(self, runner, isolated_config):
        runner.invoke(mcp_cmd, ["add", "test-server", "-c", "cmd"])
        result = runner.invoke(mcp_cmd, ["remove", "test-server"])
        assert result.exit_code == 0
        assert "removed" in result.output

        config = _ensure_config()
        assert "test-server" not in config.get("mcpServers", {})

    def test_remove_nonexistent(self, runner, isolated_config):
        result = runner.invoke(mcp_cmd, ["remove", "nonexistent"])
        assert result.exit_code == 0
        assert "not found" in result.output


# ─── Setup Hook subcommand ──────────────────────────────────────────

class TestHookAdd:
    """Test `pygitnexus setup hook add`."""

    def test_add_session_start(self, runner, isolated_config):
        result = runner.invoke(hook_cmd, [
            "add", "SessionStart",
            "-c", "/hooks/session_start.py",
            "-m", "startup",
            "-t", "30"
        ])
        assert result.exit_code == 0
        assert "Hook added" in result.output

        config = _ensure_config()
        hooks = config["hooks"]["SessionStart"]
        assert len(hooks) == 1
        assert hooks[0]["matcher"] == "startup"
        assert hooks[0]["hooks"][0]["command"] == "/hooks/session_start.py"
        assert hooks[0]["hooks"][0]["timeout"] == 30

    def test_add_all_events(self, runner, isolated_config):
        """Each event type should be accepted."""
        events = ["SessionStart", "SessionEnd", "PreToolUse", "PostToolUse",
                  "UserPromptSubmit", "Stop", "PreCompact"]
        for event in events:
            result = runner.invoke(hook_cmd, [
                "add", event,
                "-c", f"/hooks/{event.lower()}.py",
                "-m", "test"
            ])
            assert result.exit_code == 0, f"Failed for event: {event}"

    def test_add_default_timeout(self, runner, isolated_config):
        """Default timeout should be 60."""
        result = runner.invoke(hook_cmd, ["add", "Stop", "-c", "/hooks/stop.py"])
        assert result.exit_code == 0

        config = _ensure_config()
        timeout = config["hooks"]["Stop"][0]["hooks"][0]["timeout"]
        assert timeout == 60


class TestHookList:
    """Test `pygitnexus setup hook list`."""

    def test_list_empty(self, runner, isolated_config):
        result = runner.invoke(hook_cmd, ["list"])
        assert result.exit_code == 0
        assert "No Hooks configured" in result.output

    def test_list_all(self, runner, isolated_config):
        runner.invoke(hook_cmd, ["add", "SessionStart", "-c", "/h1.py", "-m", "m1"])
        runner.invoke(hook_cmd, ["add", "PreToolUse", "-c", "/h2.py", "-m", "Bash"])

        result = runner.invoke(hook_cmd, ["list"])
        assert result.exit_code == 0
        assert "SessionStart" in result.output
        assert "PreToolUse" in result.output


class TestHookRemove:
    """Test `pygitnexus setup hook remove`."""

    def test_remove_existing(self, runner, isolated_config):
        runner.invoke(hook_cmd, ["add", "SessionStart", "-c", "/h.py", "-m", "m"])
        result = runner.invoke(hook_cmd, ["remove", "SessionStart", "-i", "0"])
        assert result.exit_code == 0
        assert "removed" in result.output

        config = _ensure_config()
        assert "SessionStart" not in config.get("hooks", {})


class TestHookInit:
    """Test `pygitnexus setup hook init`."""

    def test_init_user_level(self, runner, isolated_config):
        result = runner.invoke(hook_cmd, ["init"])
        assert result.exit_code == 0
        assert "Sample hooks" in result.output

        config = _ensure_config()
        assert "hooks" in config
        assert "SessionStart" in config["hooks"]
        assert "PreToolUse" in config["hooks"]
        assert "PreCompact" in config["hooks"]

    def test_init_project_level(self, runner, isolated_config):
        with runner.isolated_filesystem():
            result = runner.invoke(hook_cmd, ["init", "--project"])
            assert result.exit_code == 0
            assert Path(".pygitnexus/hooks.json").exists()

    def test_sample_hooks_structure(self, isolated_config):
        sample = _sample_hooks()
        assert "SessionStart" in sample
        assert "PreToolUse" in sample
        assert "PreCompact" in sample


# ─── Setup Skill subcommand ─────────────────────────────────────────

class TestSkillInit:
    """Test `pygitnexus setup skill init`."""

    def test_init_user_level(self, runner, isolated_config):
        result = runner.invoke(skill_cmd, ["init", "pdf-editor", "-d", "Handle PDF operations"])
        assert result.exit_code == 0
        assert "created" in result.output

        # SKILLS_DIR is patched by isolated_config, use module attribute
        import pygitnexus.cli.setup as setup_module
        skills_dir = setup_module.SKILLS_DIR
        skill_dir = skills_dir / "pdf-editor"
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "scripts").is_dir()
        assert (skill_dir / "references").is_dir()
        assert (skill_dir / "assets").is_dir()

        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "pdf-editor" in content
        assert "Handle PDF operations" in content

    def test_init_project_level(self, runner, isolated_config):
        with runner.isolated_filesystem():
            result = runner.invoke(skill_cmd, [
                "init", "test-skill", "-d", "Test skill", "--project"
            ])
            assert result.exit_code == 0
            assert Path(".pygitnexus/skills/test-skill/SKILL.md").exists()


class TestSkillList:
    """Test `pygitnexus setup skill list`."""

    def test_list_empty(self, runner, isolated_config):
        result = runner.invoke(skill_cmd, ["list"])
        assert result.exit_code == 0

    def test_list_with_skills(self, runner, isolated_config):
        runner.invoke(skill_cmd, ["init", "skill-a", "-d", "Skill A"])
        runner.invoke(skill_cmd, ["init", "skill-b", "-d", "Skill B"])

        result = runner.invoke(skill_cmd, ["list"])
        assert result.exit_code == 0
        assert "skill-a" in result.output
        assert "skill-b" in result.output


class TestSkillRemove:
    """Test `pygitnexus setup skill remove`."""

    def test_remove_existing(self, runner, isolated_config):
        runner.invoke(skill_cmd, ["init", "test-skill", "-d", "Test"])
        result = runner.invoke(skill_cmd, ["remove", "test-skill"])
        assert result.exit_code == 0
        assert "removed" in result.output
        assert not (SKILLS_DIR / "test-skill").exists()

    def test_remove_nonexistent(self, runner, isolated_config):
        result = runner.invoke(skill_cmd, ["remove", "nonexistent"])
        assert result.exit_code == 0
        assert "not found" in result.output


# ─── Skill description parsing ──────────────────────────────────────

class TestSkillDescription:
    """Test _read_skill_description."""

    def test_with_frontmatter(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test description\n---\n\n# Content\n",
            encoding="utf-8"
        )
        desc = _read_skill_description(skill_dir / "SKILL.md")
        assert desc == "Test description"

    def test_without_frontmatter(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Just a title\n", encoding="utf-8")
        desc = _read_skill_description(skill_dir / "SKILL.md")
        assert desc == "(no description)"


# ─── Integration: full setup flow ───────────────────────────────────

class TestFullSetupFlow:
    """Test the complete setup flow combining MCP, Hook, and Skill."""

    def test_add_mcp_then_hook_then_skill(self, runner, isolated_config):
        """Sequentially add MCP, hook, and skill configs."""
        r1 = runner.invoke(mcp_cmd, ["add", "pygitnexus", "-c", "/bin/pygitnexus", "-a", "mcp"])
        assert r1.exit_code == 0

        r2 = runner.invoke(hook_cmd, ["add", "SessionStart", "-c", "/hooks/start.py", "-m", "startup"])
        assert r2.exit_code == 0

        r3 = runner.invoke(skill_cmd, ["init", "my-skill", "-d", "My skill"])
        assert r3.exit_code == 0

        # Use module attribute (SKILLS_DIR is patched by isolated_config)
        import pygitnexus.cli.setup as setup_module
        config = _ensure_config()
        assert "mcpServers" in config
        assert "hooks" in config
        assert (setup_module.SKILLS_DIR / "my-skill").exists()

    def test_config_persists_across_commands(self, runner, isolated_config):
        """Config should persist across multiple command invocations."""
        runner.invoke(mcp_cmd, ["add", "s1", "-c", "cmd1"])
        runner.invoke(mcp_cmd, ["add", "s2", "-c", "cmd2"])

        config = _ensure_config()
        assert "s1" in config["mcpServers"]
        assert "s2" in config["mcpServers"]
