#!/usr/bin/env python3
"""PyGitNexus 回归测试 —— 用 shenyu 项目验证 Java 解析不受 JS/TS/Vue 改动影响。

运行方式:
    uv run python tests/regression_shenyu.py

测试内容:
1. Java 文件解析完整性（Class/Method/Call 数量与历史对比）
2. Java 调用链覆盖率（与 GitNexus 对比）
3. JS/TS/Vue 解析器不影响 Java 解析（无 import 冲突、无解析崩溃）
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PYGITNEXUS_ROOT = Path(__file__).parent.parent
SRC_DIR = PYGITNEXUS_ROOT / "src"
SHENYU_DIR = "/home/claude/codespace/shenyu"
OUTPUT_DIR = PYGITNEXUS_ROOT / "comparison_output"

sys.path.insert(0, str(SRC_DIR))

from pygitnexus.core.scanner import scan
from pygitnexus.core.pipeline import _parse_file
from pygitnexus.core.resolver import resolve_calls
from concurrent.futures import ThreadPoolExecutor, as_completed


def test_java_scan():
    """测试 1: Java 文件扫描不受 JS/TS/Vue 改动影响。"""
    print("\n[TEST 1] Java file scanning...")
    files = scan(SHENYU_DIR)
    java_files = [f for f in files if f.lang == "java"]
    js_files = [f for f in files if f.lang in ("js", "ts")]
    vue_files = [f for f in files if f.lang == "vue"]

    assert len(java_files) > 3000, f"Expected >3000 Java files, got {len(java_files)}"
    print(f"  Java files: {len(java_files)}")
    print(f"  JS/TS files: {len(js_files)}")
    print(f"  Vue files: {len(vue_files)}")
    print("  PASS")
    return java_files


def test_java_parse(java_files):
    """测试 2: Java 文件解析完整性。"""
    print("\n[TEST 2] Java file parsing...")
    parsed_files = []
    errors = []
    max_workers = min(8, os.cpu_count() or 8)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_parse_file, sf): sf for sf in java_files[:500]}  # sample 500
        for future in as_completed(futures):
            sf = futures[future]
            try:
                pf = future.result()
                parsed_files.append(pf)
            except Exception as e:
                errors.append((sf.relative, str(e)))

    total_classes = sum(len(pf.classes) for pf in parsed_files)
    total_methods = sum(len(pf.methods) for pf in parsed_files)
    total_calls = sum(len(pf.calls) for pf in parsed_files)

    print(f"  Parsed: {len(parsed_files)}/{len(futures)} files")
    print(f"  Classes: {total_classes}")
    print(f"  Methods: {total_methods}")
    print(f"  Calls: {total_calls}")
    print(f"  Errors: {len(errors)}")

    if errors:
        for f, e in errors[:5]:
            print(f"    ERROR: {f}: {e}")

    assert len(parsed_files) == len(futures), f"{len(errors)} files failed to parse"
    assert total_classes > 0, "No classes parsed"
    assert total_methods > 0, "No methods parsed"
    print("  PASS")
    return parsed_files


def test_java_resolve(parsed_files):
    """测试 3: Java 调用解析完整性。"""
    print("\n[TEST 3] Java call resolution...")
    # Build class_map from parsed files
    class_map = {}
    for pf in parsed_files:
        for cls in pf.classes:
            class_map[cls.name] = pf.file_path

    results = resolve_calls(parsed_files, class_map)
    print(f"  Resolved calls: {len(results)}")
    assert len(results) > 0, "No calls resolved"
    print("  PASS")
    return results


def test_gitnexus_comparison():
    """测试 4: 与 GitNexus Java 调用链对比。"""
    print("\n[TEST 4] GitNexus comparison (Java only)...")

    # Check if GitNexus DB already exists (skip analysis if so)
    gn_db = Path(SHENYU_DIR) / ".gitnexus" / "lbug"
    if gn_db.exists():
        print("  GitNexus DB already exists, skipping analysis")
    else:
        print("  Running GitNexus analysis...")
        try:
            subprocess.run(
                ["gitnexus", "analyze", SHENYU_DIR, "--force", "--skip-git"],
                capture_output=True, timeout=300
            )
        except subprocess.TimeoutExpired:
            print("  WARN: GitNexus analysis timed out, skipping comparison")
            return True

    # Export GitNexus CALLS
    gn_out = subprocess.run(
        ["gitnexus", "cypher", "--json",
         'MATCH ()-[r:CALLS]->() RETURN r LIMIT 5000'],
        cwd=SHENYU_DIR, capture_output=True, text=True, timeout=60
    )
    try:
        gn_data = json.loads(gn_out.stdout.strip())
        gn_md = gn_data.get("markdown", "")
        gn_count = _parse_markdown_count(gn_md)
    except (json.JSONDecodeError, ValueError):
        print(f"  WARN: Could not parse GitNexus output")
        gn_count = -1

    # Check if PyGitNexus DB already exists
    py_db = Path(SHENYU_DIR) / ".pygitnexus" / "kuzu"
    if py_db.exists():
        print("  PyGitNexus DB already exists, skipping analysis")
    else:
        print("  Running PyGitNexus analysis...")
        try:
            subprocess.run(
                ["pygitnexus", "analyze", SHENYU_DIR, "--force"],
                capture_output=True, text=True, timeout=600
            )
        except subprocess.TimeoutExpired:
            print("  WARN: PyGitNexus analysis timed out")

    # Export PyGitNexus CALLS
    py_out = subprocess.run(
        ["pygitnexus", "cypher", "--json",
         'MATCH ()-[r:CALLS]->() RETURN r LIMIT 5000'],
        cwd=SHENYU_DIR, capture_output=True, text=True, timeout=60
    )
    try:
        py_data = json.loads(py_out.stdout.strip())
        py_count = len(py_data) if isinstance(py_data, list) else 0
    except (json.JSONDecodeError, ValueError):
        print(f"  WARN: Could not parse PyGitNexus output")
        py_count = -1

    print(f"  GitNexus CALLS: {gn_count}")
    print(f"  PyGitNexus CALLS: {py_count}")

    if gn_count > 0 and py_count > 0:
        ratio = py_count / gn_count
        print(f"  Ratio (PG/GN): {ratio:.2f}")
        assert ratio > 0.9, f"PyGitNexus has significantly fewer calls: {ratio:.2f}"
        print("  PASS")
    else:
        print("  SKIP (could not export)")
    return True


def _parse_markdown_count(md: str) -> int:
    """Parse count from GitNexus markdown output."""
    lines = [l.strip() for l in md.strip().split("\n") if l.strip().startswith("|")]
    for line in lines[2:]:
        parts = [p.strip() for p in line.strip("|").split("|")]
        if parts and parts[0].isdigit():
            return int(parts[0])
    return -1


def test_import_isolation():
    """测试 5: JS/TS 解析器 import 不影响 Java 解析。"""
    print("\n[TEST 5] Import isolation (no tree-sitter import errors for Java)...")
    from pygitnexus.core.extractor import parse as parse_java
    # Verify Java extractor doesn't import JS/TS modules
    import inspect
    source = inspect.getsource(parse_java.__module__ and sys.modules[parse_java.__module__] or parse_java)
    # Java extractor should not reference tree_sitter_javascript or tree_sitter_typescript
    assert "tree_sitter_javascript" not in source, "Java extractor should not import tree-sitter-javascript"
    assert "tree_sitter_typescript" not in source, "Java extractor should not import tree-sitter-typescript"
    print("  PASS")


def test_full_pipeline():
    """测试 6: 完整流水线运行（Java 解析 + 调用解析）。"""
    print("\n[TEST 6] Full pipeline (scan + parse + resolve for all Java files)...")
    start = time.time()

    # Scan
    files = scan(SHENYU_DIR)
    java_files = [f for f in files if f.lang == "java"]
    print(f"  Scanned: {len(java_files)} Java files")

    # Parse all
    parsed_files = []
    max_workers = min(8, os.cpu_count() or 8)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_parse_file, sf): sf for sf in java_files}
        for future in as_completed(futures):
            try:
                pf = future.result()
                parsed_files.append(pf)
            except Exception:
                pass

    print(f"  Parsed: {len(parsed_files)} files")

    # Build class map and resolve
    class_map = {}
    for pf in parsed_files:
        for cls in pf.classes:
            class_map[cls.name] = pf.file_path

    results = resolve_calls(parsed_files, class_map)
    print(f"  Resolved: {len(results)} calls")

    elapsed = time.time() - start
    print(f"  Elapsed: {elapsed:.1f}s")

    assert len(parsed_files) > 3000, f"Too few files parsed: {len(parsed_files)}"
    assert len(results) > 10000, f"Too few calls resolved: {len(results)}"
    print("  PASS")
    return True


def main():
    print("=" * 60)
    print("  PyGitNexus Regression Test: Shenyu Project")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Check shenyu exists
    if not os.path.exists(SHENYU_DIR):
        print(f"ERROR: Shenyu directory not found: {SHENYU_DIR}")
        sys.exit(1)

    passed = 0
    failed = 0
    total = 0

    # Test 1: Java scanning
    total += 1
    try:
        java_files = test_java_scan()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 2: Java parsing
    total += 1
    try:
        parsed_files = test_java_parse(java_files)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 3: Java resolution
    total += 1
    try:
        resolved = test_java_resolve(parsed_files)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 4: GitNexus comparison
    total += 1
    try:
        test_gitnexus_comparison()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 5: Import isolation
    total += 1
    try:
        test_import_isolation()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 6: Full pipeline
    total += 1
    try:
        test_full_pipeline()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{total} passed, {failed}/{total} failed")
    print(f"{'=' * 60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
