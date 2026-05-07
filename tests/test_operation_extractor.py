#!/usr/bin/env python3
"""Tests for operation_extractor and playwright_generator."""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pygitnexus.core.operation_extractor import (
    extract_operations_from_source,
    PageOperation,
    PageOperationSet,
)
from pygitnexus.auto_gen.playwright_generator import generate_playwright_script


def test_vue_login_operations():
    """Test Login.vue operation extraction."""
    file_path = "/tmp/test-projects/newbee-mall-vue3-app/src/views/Login.vue"
    if not os.path.exists(file_path):
        print("SKIP: Login.vue not found")
        return True

    with open(file_path, "rb") as f:
        content = f.read()

    op_set = extract_operations_from_source(file_path, content)

    # Should have page name and route
    assert op_set.page_name == "Login", f"Expected 'Login', got '{op_set.page_name}'"
    assert op_set.page_route == "/login", f"Expected '/login', got '{op_set.page_route}'"

    # Should have operations
    assert len(op_set.operations) > 0, "Expected at least 1 operation"

    # Should have form fields (v-model bindings)
    assert len(op_set.all_fields) >= 3, f"Expected at least 3 form fields, got {len(op_set.all_fields)}"

    # Check form field types
    field_names = {f.field_name for f in op_set.all_fields}
    assert "username" in field_names, f"Expected 'username' field, got {field_names}"
    assert "password" in field_names, f"Expected 'password' field, got {field_names}"

    print(f"PASS: Login.vue — {len(op_set.operations)} ops, {len(op_set.all_fields)} fields")
    return True


def test_vue_createorder_operations():
    """Test CreateOrder.vue operation extraction."""
    file_path = "/tmp/test-projects/newbee-mall-vue3-app/src/views/CreateOrder.vue"
    if not os.path.exists(file_path):
        print("SKIP: CreateOrder.vue not found")
        return True

    with open(file_path, "rb") as f:
        content = f.read()

    op_set = extract_operations_from_source(file_path, content)

    assert op_set.page_name == "CreateOrder"
    assert op_set.page_route == "/create-order"
    assert len(op_set.operations) > 0

    print(f"PASS: CreateOrder.vue — {len(op_set.operations)} ops, {len(op_set.all_fields)} fields")
    return True


def test_playwright_script_generation():
    """Test Playwright script generation."""
    op_set = PageOperationSet(
        page_file="test.vue",
        page_name="TestPage",
        page_route="/test",
        operations=[
            PageOperation(
                op_id="op_01_onSubmit",
                op_type="submit",
                element="button[type=submit]",
                event="@submit",
                handler="onSubmit",
                handler_line=42,
                fields=[],
                api_calls=[{"method": "POST", "path": "/api/login"}],
                navigation_target="",
                description="onSubmit (@submit) → POST /api/login",
                file_path="test.vue",
                line=42,
            ),
            PageOperation(
                op_id="op_02_goToDetail",
                op_type="navigation",
                element="a.detail-link",
                event="navigation",
                handler="goToDetail",
                handler_line=55,
                fields=[],
                api_calls=[],
                navigation_target="/detail",
                description="Navigate to /detail",
                file_path="test.vue",
                line=55,
            ),
        ],
        all_fields=[],
        all_api_endpoints=[{"method": "POST", "path": "/api/login"}],
    )

    script, spec_data = generate_playwright_script(op_set)

    assert "from playwright.sync_api import Page, expect" in script
    assert "PAGE_ROUTE = '/test'" in script
    assert "def test_op_01_onSubmit" in script
    assert "def test_op_02_goToDetail" in script
    assert "POST /api/login" in script
    assert "navigate to page" in script.lower() or "Trigger navigation" in script or "Verify navigation" in script

    # Verify spec data
    assert spec_data["page_name"] == "TestPage"
    assert spec_data["total_ops"] == 2
    assert len(spec_data["operations"]) == 2

    print(f"PASS: Playwright script generated ({len(script)} chars)")
    return True


def test_framework_filtering():
    """Test that framework internals are filtered out."""
    file_path = "/tmp/test-projects/newbee-mall-vue3-app/src/views/Home.vue"
    if not os.path.exists(file_path):
        print("SKIP: Home.vue not found")
        return True

    with open(file_path, "rb") as f:
        content = f.read()

    op_set = extract_operations_from_source(file_path, content)

    # Should NOT have framework internals as operations
    op_names = {op.handler for op in op_set.operations}
    framework_names = {"ref", "reactive", "computed", "onMounted", "useRouter", "useCartStore"}
    found_framework = framework_names & op_names
    assert not found_framework, f"Framework internals found: {found_framework}"

    print(f"PASS: Framework filtering — no framework internals in {len(op_set.operations)} ops")
    return True


def test_html_file_extraction():
    """Test HTML file operation extraction."""
    html_content = b'''<html>
<head><title>Test</title></head>
<body>
    <form action="/api/login" method="POST">
        <input type="text" name="username" placeholder="Username" required>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit">Login</button>
    </form>
    <a href="/register">Register</a>
    <script>
        function onSubmit() {
            fetch('/api/login', { method: 'POST' });
        }
    </script>
</body>
</html>'''

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        f.write(html_content)
        f.flush()
        op_set = extract_operations_from_source(f.name, html_content)

    os.unlink(f.name)

    # Should have form fields
    assert len(op_set.all_fields) >= 2, f"Expected at least 2 form fields, got {len(op_set.all_fields)}"

    # Should have API endpoint
    assert len(op_set.all_api_endpoints) > 0, "Expected API endpoint"

    field_names = {f.field_name for f in op_set.all_fields}
    assert "username" in field_names, f"Expected 'username' field, got {field_names}"
    assert "password" in field_names, f"Expected 'password' field, got {field_names}"

    print(f"PASS: HTML extraction — {len(op_set.all_fields)} fields, {len(op_set.all_api_endpoints)} APIs")
    return True


if __name__ == "__main__":
    tests = [
        test_vue_login_operations,
        test_vue_createorder_operations,
        test_playwright_script_generation,
        test_framework_filtering,
        test_html_file_extraction,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
