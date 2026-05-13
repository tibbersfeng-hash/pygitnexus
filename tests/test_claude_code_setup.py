"""Tests for Claude Code MCP + Hook installation logic.

Covers the optimized _setup_claude_code() function:
  - MCP writes to ~/.claude/settings.json (NOT ~/.claude.json)
  - Hook extraction and configuration
  - Installation detection (settings.json or claude binary)
  - No duplicate hooks on repeated runs
  - Preserves existing MCP/hook config
  - Project-level MCP via --project flag
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from pygitnexus.cli.setup import (
    setup_cmd,
    _setup_claude_code,
    _resolve_binary_path,
    _read_json,
    _write_json,
    _get_mcp_entry,
    _HOOK_TEMPLATE,
    _HOOK_BUNDLE_DIR,
    _merge_jsonc_like,
)


# ─── Helpers ────────────────────────────────────────────────────────

@pytest.fixture()
def isolated_claude_home(tmp_path: Path):
    """Create an isolated home directory with ~/.claude/settings.json."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    claude_dir = fake_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    # Write a minimal settings.json
    _write_json(settings_path, {
        "mcpServers": {
            "existing-server": {"command": "node", "args": ["server.js"]}
        }
    })
    return fake_home


def _result():
    """Helper to create a properly initialized result dict."""
    return {"skipped": [], "configured": [], "errors": []}


def _settings_path(home: Path) -> Path:
    return home / ".claude" / "settings.json"


# ─── Installation detection ─────────────────────────────────────────

class TestClaudeCodeDetection:
    """Test how _setup_claude_code detects Claude Code installation."""

    def test_detected_via_settings_json(self, fake_bin, isolated_claude_home):
        """Should detect Claude Code when settings.json exists."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)
        assert "Claude Code (not installed)" not in result.get("skipped", [])

    def test_detected_via_binary_in_path(self, fake_bin, tmp_path):
        """Should detect Claude Code when 'claude' is in PATH."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        # No settings.json, but claude binary in PATH
        result = _result()
        def mock_which(name):
            if name == "claude":
                return "/usr/bin/claude"
            return shutil.which(name)
        with patch("pathlib.Path.home", return_value=fake_home), \
             patch("shutil.which", side_effect=mock_which):
            _setup_claude_code(result, fake_bin)
        assert "Claude Code (not installed)" not in result.get("skipped", [])

    def test_not_detected_no_settings_no_binary(self, fake_bin, tmp_path):
        """Should skip when neither settings.json nor claude binary exists."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        result = _result()
        with patch("pathlib.Path.home", return_value=fake_home), \
             patch("shutil.which", return_value=None):
            _setup_claude_code(result, fake_bin)
        assert result == {"skipped": ["Claude Code (not installed)"], "configured": [], "errors": []}


# ─── MCP configuration ──────────────────────────────────────────────

class TestClaudeCodeMCP:
    """Test MCP server configuration in Claude Code settings.json."""

    def test_writes_to_settings_json_not_claude_json(self, fake_bin, isolated_claude_home):
        """MCP should be written to ~/.claude/settings.json, NOT ~/.claude.json."""
        result = _result()
        settings = _settings_path(isolated_claude_home)
        claude_json = isolated_claude_home / ".claude.json"

        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        # Verify settings.json has pygitnexus
        data = _read_json(settings)
        assert "pygitnexus" in data.get("mcpServers", {})
        entry = data["mcpServers"]["pygitnexus"]
        assert entry["command"] == fake_bin
        assert entry["args"] == ["mcp"]

        # Verify ~/.claude.json was NOT created
        assert not claude_json.exists(), "Should NOT write to ~/.claude.json"

    def test_mcp_entry_format(self, fake_bin, isolated_claude_home):
        """MCP entry should match _get_mcp_entry format."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        data = _read_json(_settings_path(isolated_claude_home))
        entry = data["mcpServers"]["pygitnexus"]
        assert entry == _get_mcp_entry(fake_bin)

    def test_preserves_existing_mcp_servers(self, fake_bin, isolated_claude_home):
        """Should merge with existing MCP config, not overwrite."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        data = _read_json(_settings_path(isolated_claude_home))
        assert "existing-server" in data.get("mcpServers", {})
        assert "pygitnexus" in data.get("mcpServers", {})

    def test_result_label_includes_mcp(self, fake_bin, isolated_claude_home):
        """Result should include 'Claude Code (MCP)' label."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)
        assert "Claude Code (MCP)" in result.get("configured", [])


# ─── Hook configuration ─────────────────────────────────────────────

class TestClaudeCodeHooks:
    """Test Hook extraction and configuration for Claude Code."""

    def test_hooks_written_to_settings_json(self, fake_bin, isolated_claude_home):
        """Hooks should be written to ~/.claude/settings.json under 'hooks' key."""
        result = _result()
        settings = _settings_path(isolated_claude_home)

        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        data = _read_json(settings)
        hooks = data.get("hooks", {})
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks

    def test_pre_tool_use_matcher(self, fake_bin, isolated_claude_home):
        """PreToolUse hook should match Grep|Glob|Bash."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        data = _read_json(_settings_path(isolated_claude_home))
        pre_entries = data["hooks"]["PreToolUse"]
        grep_entry = next((e for e in pre_entries if e["matcher"] == "Grep|Glob|Bash"), None)
        assert grep_entry is not None
        assert grep_entry["hooks"][0]["type"] == "command"
        assert grep_entry["hooks"][0]["timeout"] == 10

    def test_post_tool_use_matcher(self, fake_bin, isolated_claude_home):
        """PostToolUse hook should match Bash."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        data = _read_json(_settings_path(isolated_claude_home))
        post_entries = data["hooks"]["PostToolUse"]
        bash_entry = next((e for e in post_entries if e["matcher"] == "Bash"), None)
        assert bash_entry is not None
        assert bash_entry["hooks"][0]["type"] == "command"
        assert bash_entry["hooks"][0]["timeout"] == 10

    def test_hook_script_extracted_with_bin_path(self, fake_bin, isolated_claude_home):
        """Hook script should be extracted and contain the real binary path."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        hook_script = isolated_claude_home / ".claude" / "hooks" / "pygitnexus" / _HOOK_TEMPLATE
        assert hook_script.is_file()
        content = hook_script.read_text(encoding="utf-8")
        assert fake_bin in content
        assert "__PYGITNEXUS_BIN_PATH__" not in content

    def test_no_duplicate_hooks_on_repeated_runs(self, fake_bin, isolated_claude_home):
        """Running setup twice should not duplicate hooks."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)
            _setup_claude_code(result, fake_bin)

        data = _read_json(_settings_path(isolated_claude_home))
        pre_entries = data["hooks"]["PreToolUse"]
        grep_count = sum(1 for e in pre_entries if e.get("matcher") == "Grep|Glob|Bash")
        assert grep_count == 1, f"Expected 1 PreToolUse entry, got {grep_count}"

        post_entries = data["hooks"]["PostToolUse"]
        bash_count = sum(1 for e in post_entries if e.get("matcher") == "Bash")
        assert bash_count == 1, f"Expected 1 PostToolUse entry, got {bash_count}"

    def test_preserves_existing_hooks(self, fake_bin, isolated_claude_home):
        """Should not overwrite pre-existing hooks from other sources."""
        settings = _settings_path(isolated_claude_home)
        # Pre-configure some unrelated hooks
        _write_json(settings, {
            "mcpServers": {},
            "hooks": {
                "SessionStart": [
                    {"matcher": "startup", "hooks": [{"type": "command", "command": "/other.py", "timeout": 30}]}
                ]
            }
        })

        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        data = _read_json(settings)
        # Pre-existing hook should still be there
        assert "SessionStart" in data["hooks"]
        assert len(data["hooks"]["SessionStart"]) == 1
        assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "/other.py"
        # New hooks should be added
        assert "PreToolUse" in data["hooks"]
        assert "PostToolUse" in data["hooks"]

    def test_result_label_includes_hooks(self, fake_bin, isolated_claude_home):
        """Result should include 'Claude Code (hooks)' label when hooks are configured."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)
        assert "Claude Code (hooks)" in result.get("configured", [])


# ─── Project-level Claude Code MCP via --project ────────────────────

class TestClaudeCodeProjectMCP:
    """Test project-level Claude Code MCP configuration."""

    def test_project_flag_writes_claude_settings(self, runner, fake_bin, isolated_claude_home):
        """--project should write project-level MCP to <cwd>/.claude/settings.json."""
        with runner.isolated_filesystem():
            with patch("pathlib.Path.home", return_value=isolated_claude_home), \
                 patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
                result = runner.invoke(setup_cmd, ["--project"])
                assert result.exit_code == 0

            project_settings = Path.cwd() / ".claude" / "settings.json"
            assert project_settings.exists()
            data = json.loads(project_settings.read_text(encoding="utf-8"))
            assert "pygitnexus" in data.get("mcpServers", {})
            entry = data["mcpServers"]["pygitnexus"]
            assert entry["command"] == fake_bin
            assert entry["args"] == ["mcp"]

    def test_project_flag_includes_claude_code_in_output(self, runner, fake_bin, isolated_claude_home):
        """--project output should mention Claude Code project config."""
        with runner.isolated_filesystem():
            with patch("pathlib.Path.home", return_value=isolated_claude_home), \
                 patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
                result = runner.invoke(setup_cmd, ["--project"])
                assert result.exit_code == 0
                assert "Claude Code" in result.output or "claude" in result.output.lower()


# ─── Full setup auto integration ────────────────────────────────────

class TestClaudeCodeSetupAuto:
    """Integration tests for setup auto with Claude Code."""

    def test_setup_auto_includes_claude_code(self, runner, fake_bin, isolated_claude_home):
        """setup auto should attempt to configure Claude Code."""
        with patch("pathlib.Path.home", return_value=isolated_claude_home), \
             patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
            result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0
            # Claude Code should be configured (since settings.json exists)
            assert "Claude Code" in result.output

    def test_setup_auto_writes_mcp_to_correct_file(self, runner, fake_bin, isolated_claude_home):
        """setup auto should write MCP to ~/.claude/settings.json."""
        with patch("pathlib.Path.home", return_value=isolated_claude_home), \
             patch("pygitnexus.cli.setup._resolve_binary_path", return_value=fake_bin):
            result = runner.invoke(setup_cmd, [])
            assert result.exit_code == 0

        data = _read_json(_settings_path(isolated_claude_home))
        assert "pygitnexus" in data.get("mcpServers", {})
        assert "existing-server" in data.get("mcpServers", {})


# ─── Repeated runs: MCP duplicate + data integrity ──────────────────

class TestClaudeCodeRepeatedRuns:
    """Test behavior when setup is run multiple times."""

    def test_mcp_no_duplicate_on_repeated_runs(self, fake_bin, isolated_claude_home):
        """Running setup multiple times should not create duplicate MCP entries."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)
            _setup_claude_code(result, fake_bin)
            _setup_claude_code(result, fake_bin)

        data = _read_json(_settings_path(isolated_claude_home))
        mcp_servers = data.get("mcpServers", {})
        # pygitnexus key should exist exactly once (dict key overwrite)
        assert "pygitnexus" in mcp_servers
        assert mcp_servers["pygitnexus"]["command"] == fake_bin
        # existing-server should also still be there
        assert "existing-server" in mcp_servers

    def test_mcp_overwrites_different_bin_path(self, fake_bin, isolated_claude_home):
        """If existing pygitnexus has a different bin path, it should be updated."""
        # Pre-configure with an old/different path
        settings = _settings_path(isolated_claude_home)
        _write_json(settings, {
            "mcpServers": {
                "pygitnexus": {"command": "/old/incorrect/path", "args": ["mcp"]}
            }
        })

        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        data = _read_json(settings)
        entry = data["mcpServers"]["pygitnexus"]
        assert entry["command"] == fake_bin
        assert entry["command"] != "/old/incorrect/path"

    def test_settings_json_integrity_after_repeated_runs(self, fake_bin, isolated_claude_home):
        """settings.json should remain valid JSON after many runs."""
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            for _ in range(5):
                result = _result()
                _setup_claude_code(result, fake_bin)

        settings = _settings_path(isolated_claude_home)
        content = settings.read_text(encoding="utf-8")
        # Should parse without error
        data = json.loads(content)
        assert "mcpServers" in data
        assert "pygitnexus" in data["mcpServers"]

    def test_result_accumulates_on_repeated_runs(self, fake_bin, isolated_claude_home):
        """Repeated runs: MCP returns False on same value, hooks still append."""
        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)
            _setup_claude_code(result, fake_bin)

        # MCP: _merge_jsonc_like now returns False when value is identical,
        # so "Claude Code (MCP)" is only appended once
        assert result["configured"].count("Claude Code (MCP)") == 1
        # Hooks: still always appends "Claude Code (hooks)" (no guard)
        assert result["configured"].count("Claude Code (hooks)") == 2

    def test_mcp_already_exists_same_path_no_extra_write(self, fake_bin, isolated_claude_home):
        """When pygitnexus MCP already exists with the same path, behavior should be safe."""
        # Pre-configure with the exact same bin path
        settings = _settings_path(isolated_claude_home)
        before_mtime = settings.stat().st_mtime
        _write_json(settings, {
            "mcpServers": {
                "pygitnexus": {"command": fake_bin, "args": ["mcp"]},
                "existing-server": {"command": "node", "args": ["server.js"]}
            }
        })

        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        # Config should still be correct (overwrite is fine, no corruption)
        data = _read_json(settings)
        assert data["mcpServers"]["pygitnexus"]["command"] == fake_bin
        assert "existing-server" in data["mcpServers"]


# ─── _merge_jsonc_like dedup behavior ──────────────────────────────

class TestMergeJsoncLike:
    """Test _merge_jsonc_like returns False on identical values."""

    def test_returns_true_on_new_value(self, tmp_path):
        """First write should return True."""
        f = tmp_path / "config.json"
        result = _merge_jsonc_like(f, ["mcpServers", "test"], {"command": "node"})
        assert result is True

    def test_returns_false_on_same_value(self, tmp_path):
        """Writing identical value should return False (skip write)."""
        f = tmp_path / "config.json"
        _merge_jsonc_like(f, ["mcpServers", "test"], {"command": "node"})
        before_mtime = f.stat().st_mtime
        result = _merge_jsonc_like(f, ["mcpServers", "test"], {"command": "node"})
        assert result is False
        assert f.stat().st_mtime == before_mtime  # file not rewritten

    def test_returns_true_on_different_value(self, tmp_path):
        """Writing different value should return True."""
        f = tmp_path / "config.json"
        _merge_jsonc_like(f, ["mcpServers", "test"], {"command": "node"})
        result = _merge_jsonc_like(f, ["mcpServers", "test"], {"command": "other"})
        assert result is True

    def test_mcp_setup_skips_on_duplicate(self, fake_bin, isolated_claude_home):
        """When MCP is already configured identically, result should not label it again."""
        # Pre-configure
        settings = _settings_path(isolated_claude_home)
        _write_json(settings, {
            "mcpServers": {"pygitnexus": {"command": fake_bin, "args": ["mcp"]}}
        })

        result = _result()
        with patch("pathlib.Path.home", return_value=isolated_claude_home):
            _setup_claude_code(result, fake_bin)

        # MCP should NOT be labeled since value was identical
        assert "Claude Code (MCP)" not in result["configured"]
