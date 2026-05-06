"""Generate Python Playwright automation scripts from page operation sets."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..core.operation_extractor import PageField, PageOperation, PageOperationSet


def generate_all(op_sets: list[PageOperationSet], output_dir: str) -> list[str]:
    """Generate Playwright scripts for all page operation sets.

    Returns list of generated file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    generated: list[str] = []

    for op_set in op_sets:
        if not op_set.operations:
            continue
        script = generate_playwright_script(op_set)
        file_name = _safe_file_name(op_set.page_name) + "_test.py"
        file_path = os.path.join(output_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(script)
        generated.append(file_path)

    return generated


def generate_playwright_script(op_set: PageOperationSet) -> str:
    """Generate a Playwright Python script for a single page operation set."""
    lines: list[str] = []

    # Header
    lines.append('"""Auto-generated Playwright tests for ' + op_set.page_name + '"""')
    lines.append("# Source: " + op_set.page_file)
    lines.append("# Operations: " + str(len(op_set.operations)))
    lines.append("")
    lines.append("import re")
    lines.append("import pytest")
    lines.append("from playwright.sync_api import Page, expect")
    lines.append("")

    # Constants
    base_url_var = f"BASE_URL = 'http://localhost:5173'"
    route_var = f"PAGE_ROUTE = '{op_set.page_route}'"
    lines.append(base_url_var)
    lines.append(route_var)
    lines.append("")

    # Form field constants
    if op_set.all_fields:
        lines.append("# ── Form Field Constants ──")
        for f in op_set.all_fields:
            if f.field_name:
                var_name = _to_snake_case(f.field_name).upper()
                lines.append(f"FIELD_{var_name} = \"{f.selector}\"")
        lines.append("")

    # Fixture
    lines.append("@pytest.fixture")
    lines.append("def page_setup(page: Page):")
    lines.append('    """Fixture: navigate to page and wait for load."""')
    lines.append("    page.goto(f\"{BASE_URL}{PAGE_ROUTE}\")")
    lines.append("    page.wait_for_load_state(\"networkidle\")")
    lines.append("    return page")
    lines.append("")

    # Generate test for each operation
    for op in op_set.operations:
        lines.append("")
        lines.append(_generate_test(op, op_set))

    # Footer
    lines.append("")
    lines.append("")
    lines.append("# ── Summary ──")
    lines.append(f"# Total operations: {len(op_set.operations)}")
    for op in op_set.operations:
        lines.append(f"#   {op.op_id}: {op.description}")

    if op_set.all_api_endpoints:
        lines.append(f"# API endpoints: {len(op_set.all_api_endpoints)}")
        for api in op_set.all_api_endpoints:
            lines.append(f"#   {api['method']} {api['path']}")

    lines.append("")
    return "\n".join(lines)


def _generate_test(op: PageOperation, op_set: PageOperationSet) -> str:
    """Generate a single test function for an operation."""
    lines: list[str] = []
    test_name = f"def test_{op.op_id}(page_setup: Page):"
    doc = f'    """{op.description}"""'
    lines.append(test_name)
    lines.append(doc)
    lines.append("")

    if op.op_type == "submit":
        lines.extend(_generate_submit_test(op, op_set))
    elif op.op_type == "click":
        lines.extend(_generate_click_test(op, op_set))
    elif op.op_type == "navigation":
        lines.extend(_generate_navigation_test(op, op_set))
    elif op.op_type == "input":
        lines.extend(_generate_input_test(op, op_set))
    elif op.op_type == "change":
        lines.extend(_generate_change_test(op, op_set))
    else:
        lines.extend(_generate_generic_test(op, op_set))

    return "\n".join(lines)


def _generate_submit_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for form submission."""
    lines: list[str] = []

    # Step 1: Fill form fields
    fields = op_set.all_fields
    if fields:
        lines.append("    # Step 1: Fill form fields")
        for f in fields:
            if f.field_type in ("text", "email", "tel", "url", "search", "number"):
                lines.append(f'    page_setup.fill("{f.selector}", "test_value")')
            elif f.field_type == "password":
                lines.append(f'    page_setup.fill("{f.selector}", "test_password123")')
            elif f.field_type == "textarea":
                lines.append(f'    page_setup.fill("{f.selector}", "test text content")')
            elif f.field_type == "checkbox":
                lines.append(f'    page_setup.check("{f.selector}")')
            elif f.field_type == "radio":
                lines.append(f'    page_setup.check("{f.selector}")')
            elif f.field_type == "select":
                lines.append(f'    page_setup.select_option("{f.selector}", "test_value")')
            elif f.field_type == "file":
                lines.append(f'    page_setup.set_input_files("{f.selector}", "test_file.txt")')
        lines.append("")

    # Step 2: Submit form
    lines.append("    # Step 2: Submit form")
    lines.append(f"    page_setup.click(\"{op.element}\")")
    lines.append("")

    # Step 3: Verify response
    lines.append("    # Step 3: Verify response")
    if op.api_calls:
        for api in op.api_calls:
            method = api["method"]
            path = api["path"]
            lines.append(f"    # Expected API call: {method} {path}")

    # Wait for navigation or response
    lines.append("    page_setup.wait_for_load_state(\"networkidle\")")
    lines.append("")

    return lines


def _generate_click_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for click operation."""
    lines: list[str] = []

    lines.append("    # Step 1: Locate and click the element")
    lines.append(f"    page_setup.click(\"{op.element}\")")
    lines.append("")

    # Step 2: Verify action
    lines.append("    # Step 2: Verify action result")
    if op.api_calls:
        for api in op.api_calls:
            lines.append(f"    # Expected API call: {api['method']} {api['path']}")
    if op.navigation_target:
        lines.append(f"    expect(page_setup).to_have_url(re.compile(\"{op.navigation_target}\"))")
    lines.append("    page_setup.wait_for_load_state(\"networkidle\")")
    lines.append("")

    return lines


def _generate_navigation_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for navigation operation."""
    lines: list[str] = []

    lines.append("    # Step 1: Trigger navigation")
    lines.append(f"    page_setup.click(\"{op.element}\")")
    lines.append("")

    # Step 2: Verify navigation
    lines.append("    # Step 2: Verify navigation target")
    if op.navigation_target:
        target = op.navigation_target
        lines.append(f"    expect(page_setup).to_have_url(re.compile(\"{target}\"))")
    lines.append("    page_setup.wait_for_load_state(\"networkidle\")")
    lines.append("")

    return lines


def _generate_input_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for input operation."""
    lines: list[str] = []

    lines.append("    # Step 1: Fill input field")
    if op.fields:
        for f in op.fields:
            lines.append(f'    page_setup.fill("{f.selector}", "test_value")')
    else:
        lines.append(f'    page_setup.fill("{op.element}", "test_value")')
    lines.append("")

    lines.append("    # Step 2: Verify input event response")
    lines.append("    page_setup.wait_for_timeout(500)")
    lines.append("")

    return lines


def _generate_change_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for change operation."""
    lines: list[str] = []

    lines.append("    # Step 1: Change field value")
    if op.fields:
        for f in op.fields:
            if f.field_type == "select":
                lines.append(f'    page_setup.select_option("{f.selector}", "option_value")')
            elif f.field_type == "checkbox":
                lines.append(f'    page_setup.set_checked("{f.selector}", True)')
            else:
                lines.append(f'    page_setup.fill("{f.selector}", "new_value")')
    else:
        lines.append(f'    page_setup.fill("{op.element}", "new_value")')
    lines.append("")

    lines.append("    # Step 2: Verify change event response")
    lines.append("    page_setup.wait_for_timeout(500)")
    lines.append("")

    return lines


def _generate_generic_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for generic/other operation."""
    lines: list[str] = []

    lines.append("    # Perform the operation")
    lines.append(f"    page_setup.click(\"{op.element}\")")
    lines.append("")

    lines.append("    # Verify result")
    if op.api_calls:
        for api in op.api_calls:
            lines.append(f"    # Expected API: {api['method']} {api['path']}")
    lines.append("    page_setup.wait_for_load_state(\"networkidle\")")
    lines.append("")

    return lines


def _safe_file_name(name: str) -> str:
    """Convert page name to a safe Python file name."""
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    if not name:
        return "page"
    return name


def _to_snake_case(s: str) -> str:
    """Convert a string to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    s = s.replace("-", "_").replace(" ", "_").lower()
    return s
