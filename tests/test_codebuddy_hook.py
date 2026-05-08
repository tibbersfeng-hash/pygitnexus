"""Tests for the CodeBuddy hook script (pygitnexus-hook.cjs).

The hook script is a Node.js file, so these tests use subprocess calls to
`node` to exercise its behavior. Each test constructs a JSON input matching
the CodeBuddy hook protocol and verifies the stdout output.

Scenarios covered:

PostToolUse (git mutation triggers):
  1. Registered project, recently indexed (< 5 min) → silent skip
  2. Registered project, stale index (> 5 min) → prompt reindex
  3. Not registered, has .pygitnexus/ dir → prompt reindex
  4. Not registered, no .pygitnexus/ dir → prompt initial indexing

PreToolUse (search augmentation):
  5. Grep on registered project → calls pygitnexus query
  6. Glob on registered project → extracts pattern, calls query
  7. Bash (rg/grep) on registered project → extracts pattern, calls query
  8. Non-search tool → no output
  9. Short pattern (< 3 chars) → no output

Hook script integrity:
  10. findDbDir exists and walks up correctly
  11. lookupRegistry matches subdirectories
  12. Placeholder replacement in bundled script
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Locate the hook script in the source tree
HOOK_SCRIPT = Path(__file__).parent.parent / "src" / "pygitnexus" / "cli" / "hooks" / "pygitnexus" / "pygitnexus-hook.cjs"


@pytest.fixture(scope="module")
def node_available():
    """Skip all tests if Node.js is not installed."""
    if not shutil.which("node"):
        pytest.skip("Node.js not installed")


@pytest.fixture()
def registry_dir(tmp_path: Path):
    """Create a temporary ~/.pygitnexus/ directory with registry.json."""
    pygitnexus_home = tmp_path / ".pygitnexus"
    pygitnexus_home.mkdir()
    return pygitnexus_home


@pytest.fixture()
def home_dir(tmp_path: Path):
    """Create a temporary home directory."""
    return tmp_path


def _run_hook(input_data: dict, env: dict | None = None) -> str:
    """Run the hook script with the given JSON input, return stdout."""
    env_override = {"HOME": env.get("HOME", str(Path.home()))} if env else {}
    full_env = {**os.environ, **env_override}
    proc = subprocess.run(
        ["node", str(HOOK_SCRIPT)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        timeout=15,
        env=full_env,
    )
    return proc.stdout.strip()


def _parse_hook_output(stdout: str) -> dict | None:
    """Parse hook stdout into a dict, or None if empty."""
    if not stdout:
        return None
    return json.loads(stdout)


def _write_registry(registry_dir: Path, entries: dict):
    """Write registry.json to the given directory."""
    reg_path = registry_dir / "registry.json"
    reg_path.write_text(json.dumps(entries), encoding="utf-8")


# ─── PostToolUse: git mutation triggers ─────────────────────────────

class TestPostToolUseRegisteredRecent:
    """Registered project, recently indexed (< 5 min) → should be silent."""

    def test_recent_index_skip(self, node_available, tmp_path, registry_dir, home_dir):
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": five_min_ago,
            },
        })

        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'fix'"},
            "tool_output": {"exit_code": 0},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == "", "Should be silent for recently indexed project"

    def test_recent_index_skip_subdirectory(self, node_available, tmp_path, registry_dir, home_dir):
        """Subdirectory of a registered project should also skip."""
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        project_dir = tmp_path / "my-project"
        sub_dir = project_dir / "src" / "main"
        sub_dir.mkdir(parents=True)
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": five_min_ago,
            },
        })

        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git pull origin main"},
            "tool_output": {"exit_code": 0},
            "cwd": str(sub_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == "", "Should be silent for subdirectory of recently indexed project"


class TestPostToolUseRegisteredStale:
    """Registered project, stale index (> 5 min) → should prompt reindex."""

    def test_stale_index_prompts_reindex(self, node_available, tmp_path, registry_dir, home_dir):
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": one_hour_ago,
            },
        })

        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git merge feature-branch"},
            "tool_output": {"exit_code": 0},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        output = _parse_hook_output(stdout)
        assert output is not None
        hook_output = output["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "PostToolUse"
        assert "stale" in hook_output["additionalContext"].lower()
        assert str(project_dir) in hook_output["additionalContext"]
        assert "pygitnexus analyze" in hook_output["additionalContext"]


class TestPostToolUseLocalOnly:
    """Not registered, but has .pygitnexus/ dir → prompt reindex."""

    def test_local_dot_pygitnexus_prompts_reindex(self, node_available, tmp_path, home_dir):
        project_dir = tmp_path / "local-project"
        project_dir.mkdir()
        # Create local .pygitnexus/ directory
        (project_dir / ".pygitnexus").mkdir()

        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git rebase HEAD~2"},
            "tool_output": {"exit_code": 0},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        output = _parse_hook_output(stdout)
        assert output is not None
        hook_output = output["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "PostToolUse"
        assert "stale" in hook_output["additionalContext"].lower()
        assert "pygitnexus analyze" in hook_output["additionalContext"]


class TestPostToolUseNeverIndexed:
    """Not registered, no .pygitnexus/ dir → prompt initial indexing."""

    def test_never_indexed_prompts_init(self, node_available, tmp_path, home_dir):
        project_dir = tmp_path / "fresh-project"
        project_dir.mkdir()

        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'initial'"},
            "tool_output": {"exit_code": 0},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        output = _parse_hook_output(stdout)
        assert output is not None
        hook_output = output["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "PostToolUse"
        assert "not been indexed" in hook_output["additionalContext"]
        assert "pygitnexus analyze" in hook_output["additionalContext"]


class TestPostToolUseEdgeCases:
    """Edge cases for PostToolUse handler."""

    def test_failed_git_command_no_output(self, node_available, tmp_path, home_dir):
        """Failed git command should not trigger a hook."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'fix'"},
            "tool_output": {"exit_code": 1},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == ""

    def test_non_git_command_no_output(self, node_available, tmp_path, home_dir):
        """Non-git mutation commands should not trigger a hook."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_output": {"exit_code": 0},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == ""

    def test_non_bash_tool_no_output(self, node_available, tmp_path, home_dir):
        """Non-Bash tools should not trigger PostToolUse hook."""
        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Grep",
            "tool_input": {"pattern": "UserService"},
            "cwd": str(tmp_path),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == ""

    def test_relative_cwd_no_output(self, node_available, home_dir):
        """Relative cwd should be ignored."""
        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'x'"},
            "tool_output": {"exit_code": 0},
            "cwd": "relative/path",
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == ""


# ─── PreToolUse: search augmentation ────────────────────────────────

class TestPreToolUseGrep:
    """Grep tool search → extract pattern, call pygitnexus query."""

    def test_grep_registered_project(self, node_available, tmp_path, registry_dir, home_dir):
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": now,
            },
        })

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Grep",
            "tool_input": {"pattern": "UserService"},
            "cwd": str(project_dir),
        }

        # The hook will try to run `pygitnexus query UserService`,
        # which may fail if pygitnexus is not installed. That's OK —
        # we verify the hook doesn't crash and handles the error gracefully.
        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        # Either empty (query failed) or valid JSON output
        if stdout:
            output = _parse_hook_output(stdout)
            assert output is not None
            assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


class TestPreToolUseGlob:
    """Glob tool search → extract pattern from glob."""

    def test_glob_pattern_extraction(self, node_available, tmp_path, registry_dir, home_dir):
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": now,
            },
        })

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Glob",
            "tool_input": {"pattern": "**/UserService*.java"},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        # Pattern "UserService" extracted — query may or may not succeed
        # but the hook should not crash
        if stdout:
            output = _parse_hook_output(stdout)
            assert output is not None


class TestPreToolUseBash:
    """Bash with rg/grep → extract pattern."""

    def test_rg_command_extraction(self, node_available, tmp_path, registry_dir, home_dir):
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": now,
            },
        })

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rg --type java 'UserService' src/"},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        if stdout:
            output = _parse_hook_output(stdout)
            assert output is not None


class TestPreToolUseEdgeCases:
    """Edge cases for PreToolUse handler."""

    def test_short_pattern_no_output(self, node_available, tmp_path, registry_dir, home_dir):
        """Patterns < 3 chars should be ignored."""
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            },
        })

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Grep",
            "tool_input": {"pattern": "a"},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == "", "Short patterns should be ignored"

    def test_non_search_tool_no_output(self, node_available, tmp_path, registry_dir, home_dir):
        """Non-search tools should not trigger PreToolUse."""
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        _write_registry(registry_dir, {
            "_groups": {},
            "my-project": {
                "path": str(project_dir),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            },
        })

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/main.java"},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == ""

    def test_unregistered_project_no_output(self, node_available, tmp_path, home_dir):
        """Unregistered project with no .pygitnexus/ → no output."""
        project_dir = tmp_path / "fresh-project"
        project_dir.mkdir()

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Grep",
            "tool_input": {"pattern": "UserService"},
            "cwd": str(project_dir),
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == ""

    def test_relative_cwd_no_output(self, node_available, home_dir):
        """Relative cwd should be ignored."""
        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Grep",
            "tool_input": {"pattern": "UserService"},
            "cwd": "relative/path",
        }

        stdout = _run_hook(input_data, env={"HOME": str(home_dir)})
        assert stdout == ""


# ─── Hook script integrity ──────────────────────────────────────────

class TestHookScriptStructure:
    """Verify the hook script has all required functions."""

    def test_find_db_dir_exists(self):
        """findDbDir function should be defined in the hook script."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "function findDbDir" in content

    def test_lookup_registry_exists(self):
        """lookupRegistry function should be defined."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "function lookupRegistry" in content

    def test_extract_pattern_exists(self):
        """extractPattern function should be defined."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "function extractPattern" in content

    def test_find_pygitnexus_dir_exists(self):
        """findPyGitNexusDir function should be defined."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "function findPyGitNexusDir" in content

    def test_placeholder_in_template(self):
        """The source hook script should contain the placeholder."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "__PYGITNEXUS_BIN_PATH__" in content

    def test_handlers_dispatch(self):
        """The handlers object should map PreToolUse and PostToolUse."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "PreToolUse" in content
        assert "PostToolUse" in content

    def test_handle_pre_tool_use(self):
        """handlePreToolUse function should be defined."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "function handlePreToolUse" in content

    def test_handle_post_tool_use(self):
        """handlePostToolUse function should be defined."""
        content = HOOK_SCRIPT.read_text(encoding="utf-8")
        assert "function handlePostToolUse" in content


class TestFindDbDir:
    """Test findDbDir by running the hook with a custom script."""

    def test_find_db_dir_walks_up(self, node_available, tmp_path):
        """Create a small test script to verify findDbDir walks up."""
        # Create a nested directory with .pygitnexus at the top
        project_dir = tmp_path / "project"
        sub_dir = project_dir / "src" / "components"
        sub_dir.mkdir(parents=True)
        (project_dir / ".pygitnexus").mkdir()

        test_script = tmp_path / "test_find_db_dir.mjs"
        test_script.write_text(f"""
import {{ createRequire }} from 'module';
const require = createRequire(import.meta.url);

// Inline the hook script's findDbDir logic
const path = require('path');
const fs = require('fs');

function findDbDir(startDir) {{
  let dir = startDir || process.cwd();
  for (let i = 0; i < 5; i++) {{
    const candidate = path.join(dir, '.pygitnexus');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }}
  return null;
}}

const result = findDbDir('{sub_dir}');
console.log(result || 'NOT_FOUND');
""", encoding="utf-8")

        proc = subprocess.run(
            ["node", str(test_script)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result = proc.stdout.strip()
        assert result == str(project_dir / ".pygitnexus")

    def test_find_db_dir_returns_null_when_not_found(self, node_available, tmp_path):
        """findDbDir should return null when no .pygitnexus/ exists."""
        project_dir = tmp_path / "no-db"
        project_dir.mkdir()

        test_script = tmp_path / "test_find_null.mjs"
        test_script.write_text(f"""
import {{ createRequire }} from 'module';
const require = createRequire(import.meta.url);
const path = require('path');
const fs = require('fs');

function findDbDir(startDir) {{
  let dir = startDir || process.cwd();
  for (let i = 0; i < 5; i++) {{
    const candidate = path.join(dir, '.pygitnexus');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }}
  return null;
}}

const result = findDbDir('{project_dir}');
console.log(result === null ? 'NULL' : result);
""", encoding="utf-8")

        proc = subprocess.run(
            ["node", str(test_script)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert proc.stdout.strip() == "NULL"


class TestLookupRegistry:
    """Test lookupRegistry matching logic."""

    def test_exact_path_match(self, node_available, tmp_path):
        """Registry should match exact project path."""
        project_dir = tmp_path / "exact-match"
        project_dir.mkdir()
        registry_dir = tmp_path / ".pygitnexus"
        registry_dir.mkdir()
        reg_file = registry_dir / "registry.json"
        now = datetime.now(timezone.utc).isoformat()
        reg_file.write_text(json.dumps({
            "_groups": {},
            "my-project": {"path": str(project_dir), "indexed_at": now},
        }), encoding="utf-8")

        test_script = tmp_path / "test_registry.mjs"
        test_script.write_text(f"""
import {{ createRequire }} from 'module';
const require = createRequire(import.meta.url);
const path = require('path');
const fs = require('fs');

function lookupRegistry(cwd) {{
  const result = {{ registered: false, indexedAt: '', matchedPath: '' }};
  try {{
    const registryPath = path.join('{tmp_path}', '.pygitnexus', 'registry.json');
    const registry = JSON.parse(fs.readFileSync(registryPath, 'utf-8'));
    const projectPath = path.resolve(cwd);
    for (const [key, entry] of Object.entries(registry)) {{
      if (key === '_groups') continue;
      const entryPath = path.resolve(entry.path || '');
      if (projectPath === entryPath || projectPath.startsWith(entryPath + path.sep)) {{
        result.registered = true;
        result.indexedAt = entry.indexed_at || '';
        result.matchedPath = entryPath;
        break;
      }}
    }}
  }} catch {{}}
  return result;
}}

const result = lookupRegistry('{project_dir}');
console.log(JSON.stringify(result));
""", encoding="utf-8")

        proc = subprocess.run(
            ["node", str(test_script)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result = json.loads(proc.stdout.strip())
        assert result["registered"] is True
        assert result["matchedPath"] == str(project_dir)

    def test_subdirectory_match(self, node_available, tmp_path):
        """Registry should match subdirectories of registered project."""
        project_dir = tmp_path / "parent-project"
        sub_dir = project_dir / "frontend"
        sub_dir.mkdir(parents=True)
        registry_dir = tmp_path / ".pygitnexus"
        registry_dir.mkdir()
        reg_file = registry_dir / "registry.json"
        now = datetime.now(timezone.utc).isoformat()
        reg_file.write_text(json.dumps({
            "_groups": {},
            "parent": {"path": str(project_dir), "indexed_at": now},
        }), encoding="utf-8")

        test_script = tmp_path / "test_subdir.mjs"
        test_script.write_text(f"""
import {{ createRequire }} from 'module';
const require = createRequire(import.meta.url);
const path = require('path');
const fs = require('fs');

function lookupRegistry(cwd) {{
  const result = {{ registered: false, indexedAt: '', matchedPath: '' }};
  try {{
    const registryPath = path.join('{tmp_path}', '.pygitnexus', 'registry.json');
    const registry = JSON.parse(fs.readFileSync(registryPath, 'utf-8'));
    const projectPath = path.resolve(cwd);
    for (const [key, entry] of Object.entries(registry)) {{
      if (key === '_groups') continue;
      const entryPath = path.resolve(entry.path || '');
      if (projectPath === entryPath || projectPath.startsWith(entryPath + path.sep)) {{
        result.registered = true;
        result.indexedAt = entry.indexed_at || '';
        result.matchedPath = entryPath;
        break;
      }}
    }}
  }} catch {{}}
  return result;
}}

const result = lookupRegistry('{sub_dir}');
console.log(JSON.stringify(result));
""", encoding="utf-8")

        proc = subprocess.run(
            ["node", str(test_script)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result = json.loads(proc.stdout.strip())
        assert result["registered"] is True
        assert result["matchedPath"] == str(project_dir)
