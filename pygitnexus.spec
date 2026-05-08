# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PyGitNexus — onefile mode only."""

import sys
import platform
from pathlib import Path

import tree_sitter_html
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_typescript
import kuzu

# Discover native library paths
tshtml_dir = Path(tree_sitter_html.__file__).parent
tsjava_dir = Path(tree_sitter_java.__file__).parent
tsjs_dir = Path(tree_sitter_javascript.__file__).parent
tsts_dir = Path(tree_sitter_typescript.__file__).parent
kuzu_dir = Path(kuzu.__file__).parent

block_cipher = None

# Collect all Python submodules
hiddenimports = [
    "click",
    "tree_sitter",
    "tree_sitter_java",
    "kuzu",
    "mcp",
    "mcp.server",
    "mcp.server.fastmcp",
    "importlib.metadata",
    # CLI commands
    "pygitnexus.cli.main",
    "pygitnexus.cli.analyze",
    "pygitnexus.cli.list",
    "pygitnexus.cli.status",
    "pygitnexus.cli.clean",
    "pygitnexus.cli.query",
    "pygitnexus.cli.context",
    "pygitnexus.cli.cypher",
    "pygitnexus.cli.mcp",
    "pygitnexus.cli.setup",
    "pygitnexus.cli.install",
    "pygitnexus.cli._common",
    # Core
    "pygitnexus.core.pipeline",
    "pygitnexus.core.extractor",
    "pygitnexus.core.extractor_js",
    "pygitnexus.core.extractor_ts",
    "pygitnexus.core.resolver",
    "pygitnexus.core.scanner",
    "pygitnexus.core.models",
    "pygitnexus.core.resolver_js",
    "pygitnexus.core.extractor_html",
    "pygitnexus.core.extractor_vue",
    "pygitnexus.core.operation_extractor",
    "pygitnexus._version",
    "pygitnexus.auto_gen",
    "pygitnexus.auto_gen.playwright_generator",
    "pygitnexus.cli.autogen",
    # Group
    "pygitnexus.core.group",
    "pygitnexus.core.group.config_parser",
    "pygitnexus.core.group.storage",
    "pygitnexus.core.group.contract_extractor",
    "pygitnexus.core.group.matching",
    "pygitnexus.core.group.bridge_db",
    "pygitnexus.core.group.cross_impact",
    "pygitnexus.core.group.sync",
    "pygitnexus.cli.group",
    # Graph
    "pygitnexus.graph.schema",
    "pygitnexus.graph.store",
    # Search
    "pygitnexus.search.query",
    # Storage
    "pygitnexus.storage.repo_manager",
    # MCP
    "pygitnexus.mcp.server",
]

# Data files: tree-sitter grammars + kuzu native libs + web assets
datas = []

# VERSION file for frozen builds (written by CI before building)
version_file = Path("src/pygitnexus/VERSION")
if version_file.exists():
    datas.append((str(version_file), "pygitnexus"))

web_dir = Path("src/pygitnexus/web")
datas.append((str(web_dir), "pygitnexus/web"))

# Hook script for CodeBuddy
hook_script = Path("src/pygitnexus/cli/hooks/pygitnexus/pygitnexus-hook.cjs")
if hook_script.exists():
    datas.append((str(hook_script.parent), "pygitnexus/cli/hooks/pygitnexus"))

datas.append((str(tshtml_dir), "tree_sitter_html"))
datas.append((str(tsjava_dir), "tree_sitter_java"))
datas.append((str(tsjs_dir), "tree_sitter_javascript"))
datas.append((str(tsts_dir), "tree_sitter_typescript"))

# Kuzu native libs vary by platform
for ext in ("*.so", "*.dylib", "*.dll", "*.pyd"):
    for lib in kuzu_dir.glob(f"**/{ext}"):
        datas.append((str(lib), "kuzu"))

a = Analysis(
    ["run_pygitnexus.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile mode only
# Disable UPX on macOS — UPX-compressed Mach-O binaries are killed by kernel (SIGKILL 9)
use_upx = platform.system().lower() != "darwin"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pygitnexus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=use_upx,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
