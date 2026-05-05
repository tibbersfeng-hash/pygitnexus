#!/usr/bin/env python3
"""PyGitNexus regression test — newbee-mall-vue3-app frontend project.

Run with:
    uv run python tests/regression_newbee.py

Test content:
1. Vue file scanning completeness (>20 .vue files)
2. Page extraction (router + views convention, >10 pages)
3. Component extraction (>15 components)
4. Action + API call extraction (>20 actions, >15 API calls)
5. Module inference (>5 modules)
6. Full pipeline run (fresh analysis end-to-end)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

PYGITNEXUS_ROOT = Path(__file__).parent.parent
SRC_DIR = PYGITNEXUS_ROOT / "src"
NEWBEE_DIR = "/tmp/test-projects/newbee-mall-vue3-app"
OUTPUT_DIR = PYGITNEXUS_ROOT / "comparison_output"

sys.path.insert(0, str(SRC_DIR))

from pygitnexus.testnexus.scanners.vue_scanner import VueScanner
from pygitnexus.testnexus.core.pipeline import run_analysis
from pygitnexus.testnexus.graph.store import GraphStore


def test_vue_scan():
    """Test 1: Vue file scanning completeness."""
    print("\n[TEST 1] Vue file scanning...")
    scanner = VueScanner()
    files = scanner.scan_files(NEWBEE_DIR)
    vue_files = [f for f in files if f.path.suffix == ".vue"]
    js_files = [f for f in files if f.path.suffix == ".js"]
    ts_files = [f for f in files if f.path.suffix == ".ts"]

    assert len(vue_files) >= 20, f"Expected >=20 Vue files, got {len(vue_files)}"
    print(f"  Vue files: {len(vue_files)}")
    print(f"  JS files: {len(js_files)}")
    print(f"  TS files: {len(ts_files)}")
    print(f"  Total: {len(files)}")
    print("  PASS")
    return files, vue_files


def test_page_extraction(files):
    """Test 2: Page extraction from router + views convention."""
    print("\n[TEST 2] Page extraction...")
    scanner = VueScanner()
    pages = scanner.extract_pages(files)

    assert len(pages) >= 10, f"Expected >=10 pages, got {len(pages)}"
    print(f"  Pages: {len(pages)}")
    for p in pages[:5]:
        print(f"    - {p.name} ({p.url})")
    if len(pages) > 5:
        print(f"    ... and {len(pages) - 5} more")
    print("  PASS")
    return pages


def test_component_extraction(files):
    """Test 3: Component extraction."""
    print("\n[TEST 3] Component extraction...")
    scanner = VueScanner()
    components = scanner.extract_components(files)

    assert len(components) >= 15, f"Expected >=15 components, got {len(components)}"
    print(f"  Components: {len(components)}")
    print("  PASS")
    return components


def test_action_and_api_extraction(files):
    """Test 4: Action + API call extraction."""
    print("\n[TEST 4] Action and API call extraction...")
    scanner = VueScanner()
    actions = scanner.extract_actions(files)
    api_calls = scanner.get_api_calls(files)

    assert len(actions) >= 15, f"Expected >=15 actions, got {len(actions)}"
    assert len(api_calls) >= 15, f"Expected >=15 API calls, got {len(api_calls)}"
    print(f"  Actions: {len(actions)}")
    print(f"  API calls: {len(api_calls)}")
    # Show sample API calls
    with_paths = [ac for ac in api_calls if ac.api_path]
    print(f"  API calls with path: {len(with_paths)}")
    for ac in with_paths[:5]:
        print(f"    {ac.http_method} {ac.api_path} ({ac.component})")
    if len(with_paths) > 5:
        print(f"    ... and {len(with_paths) - 5} more")
    print("  PASS")
    return actions, api_calls


def test_module_inference(files, pages, components, api_calls):
    """Test 5: Module inference from route paths.

    newbee has flat views/ (no subdirectories), so module inference falls
    back to route-path grouping (e.g. /cart → "cart", /order → "order").
    """
    print("\n[TEST 5] Module inference...")
    from pygitnexus.testnexus.core.module_infer import infer_modules

    modules, affiliations = infer_modules(
        frontend_path=Path(NEWBEE_DIR),
        pages=pages,
        components=components,
        api_calls=api_calls,
        backend_endpoints=None,
    )

    assert len(modules) >= 8, f"Expected >=8 modules from router config, got {len(modules)}"
    print(f"  Modules: {len(modules)}")
    for name, info in sorted(modules.items(), key=lambda x: -x[1]["page_count"]):
        print(f"    - {name} ({info.get('page_count', 0)} pages) — {info.get('description', '')}")
    print("  PASS")
    return modules, affiliations


def test_full_pipeline():
    """Test 6: Full pipeline — fresh analysis end-to-end."""
    print("\n[TEST 6] Full pipeline (fresh analysis)...")

    # Use a temp DB to avoid touching existing data
    import tempfile
    db_path = Path(tempfile.mkdtemp()) / "kuzu"
    scanner = VueScanner()
    start = time.time()

    stats = run_analysis(
        frontend_path=NEWBEE_DIR,
        db_path=db_path,
        scanner=scanner,
    )

    elapsed = time.time() - start
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Stats:")
    for k, v in stats.items():
        print(f"    {k}: {v}")

    assert stats["files"] >= 30, f"Expected >=30 files, got {stats['files']}"
    assert stats["pages"] >= 10, f"Expected >=10 pages, got {stats['pages']}"
    assert stats["components"] >= 15, f"Expected >=15 components, got {stats['components']}"
    assert stats["actions"] >= 15, f"Expected >=15 actions, got {stats['actions']}"
    assert stats["api_calls"] >= 15, f"Expected >=15 API calls, got {stats['api_calls']}"
    assert stats["modules"] >= 8, f"Expected >=8 modules, got {stats['modules']}"

    # Verify the DB can be queried (open existing DB, don't re-create schema)
    store = GraphStore(db_path)
    result = store._execute("MATCH (n) RETURN count(n) as cnt")
    count = 0
    if result:
        while result.has_next():
            row = result.get_next()
            count = row[0]
    store.close()
    print(f"  Graph nodes: {count}")
    assert count > 50, f"Expected >50 graph nodes, got {count}"

    print("  PASS")


def main():
    print("=" * 60)
    print("  PyGitNexus Regression Test: newbee-mall-vue3-app")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    if not os.path.exists(NEWBEE_DIR):
        print(f"ERROR: newbee directory not found: {NEWBEE_DIR}")
        sys.exit(1)

    passed = 0
    failed = 0
    total = 0

    # Test 1: Vue scanning
    total += 1
    try:
        files, vue_files = test_vue_scan()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1
        sys.exit(1)

    # Test 2: Page extraction
    total += 1
    try:
        pages = test_page_extraction(files)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 3: Component extraction
    total += 1
    try:
        components = test_component_extraction(files)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 4: Action + API extraction
    total += 1
    try:
        actions, api_calls = test_action_and_api_extraction(files)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 5: Module inference
    total += 1
    try:
        modules, affiliations = test_module_inference(files, pages, components, api_calls)
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
