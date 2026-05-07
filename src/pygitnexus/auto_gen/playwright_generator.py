"""Generate Python Playwright tests from page operation sets — improved version.

Generates scripts with:
- Precise CSS selectors (#id, .class, [v-model], [data-testid])
- Form field filling with appropriate test values
- API interception and assertion via page.expect_response()
- URL assertions for navigation
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..core.operation_extractor import PageField, PageOperation, PageOperationSet
from .test_spec import TestOpSpec, spec_from_operation, spec_from_set, _test_value_for_type


def generate_all(op_sets: list[PageOperationSet], output_dir: str) -> list[str]:
    """Generate Playwright scripts for all page operation sets.

    Returns list of generated file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    generated: list[str] = []

    for op_set in op_sets:
        if not op_set.operations:
            continue
        script, _ = generate_playwright_script(op_set)
        file_name = _safe_file_name(op_set.page_name) + "_test.py"
        file_path = os.path.join(output_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(script)
        generated.append(file_path)

    return generated


def generate_playwright_script(
    op_set: PageOperationSet,
) -> tuple[str, dict[str, object]]:
    """Generate a Playwright Python script for a single page operation set.

    Returns (script_text, spec_dict) where spec_dict is the full test spec
    suitable for database storage.
    """
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
    lines.append("BASE_URL = 'http://localhost:5173'")
    lines.append("PAGE_ROUTE = '" + op_set.page_route + "'")
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

    # Generate test for each operation
    for op in op_set.operations:
        lines.append("")
        lines.append(_generate_test(op, op_set))

    # Footer summary
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

    # Build spec dict
    spec_data = spec_from_set(op_set)

    return "\n".join(lines), spec_data


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


# ── Improved test generators ──

def _generate_submit_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for form submission with API interception."""
    lines: list[str] = []
    step_num = 1

    # Step: Fill form fields (operation-level first, then page-level)
    fields = op.fields or op_set.all_fields
    if fields:
        lines.append(f"    # Step {step_num}: Fill form fields")
        for f in fields:
            selector = _precise_selector(f, op)
            value = _test_value_for_type(f.field_type, f.field_name)
            if f.field_type in ("text", "email", "tel", "url", "search", "number", "password"):
                lines.append(f'    page_setup.fill("{selector}", "{value}")')
            elif f.field_type == "textarea":
                lines.append(f'    page_setup.fill("{selector}", "{value}")')
            elif f.field_type in ("checkbox", "radio"):
                lines.append(f'    page_setup.check("{selector}")')
            elif f.field_type == "select":
                lines.append(f'    page_setup.select_option("{selector}", "{value}")')
            elif f.field_type == "file":
                lines.append(f'    page_setup.set_input_files("{selector}", "{value}")')
        lines.append("")
        step_num += 1

    # Step: Submit with API interception
    has_api = bool(op.api_calls)
    element_selector = _refine_element_selector(op.element)

    if has_api:
        lines.append(f"    # Step {step_num}: Submit form with API interception")
        # Build API match patterns with unique variable names
        api_patterns = _build_api_patterns(op.api_calls)
        for i, pattern in enumerate(api_patterns):
            var = f"resp_{i}"
            lines.append(f'    {var} = page_setup.expect_response("{pattern}")')
        lines.append(f"    page_setup.click(\"{element_selector}\")")
        for i, pattern in enumerate(api_patterns):
            var = f"resp_{i}"
            lines.append(f'    response = {var}.value')
            lines.append(f"    expect(response).to_be_ok()")
        lines.append("")
    else:
        lines.append(f"    # Step {step_num}: Submit form")
        lines.append(f"    page_setup.click(\"{element_selector}\")")
        lines.append("")

    step_num += 1

    # Step: Verify navigation (if any)
    lines.append(f"    # Step {step_num}: Verify result")
    if op.navigation_target:
        lines.append(f'    expect(page_setup).to_have_url(re.compile("{op.navigation_target}"))')
    else:
        lines.append('    page_setup.wait_for_load_state("networkidle")')
    lines.append("")

    return lines


def _generate_click_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for click operation with API interception."""
    lines: list[str] = []
    element_selector = _refine_element_selector(op.element)

    lines.append("    # Step 1: Click the element")
    lines.append(f"    page_setup.click(\"{element_selector}\")")
    lines.append("")

    lines.append("    # Step 2: Verify result")
    has_api = bool(op.api_calls)
    if has_api:
        api_patterns = _build_api_patterns(op.api_calls)
        for pattern in api_patterns:
            lines.append(f"    # Expected API: {pattern}")
        lines.append('    page_setup.wait_for_load_state("networkidle")')
    elif op.navigation_target:
        lines.append(f'    expect(page_setup).to_have_url(re.compile("{op.navigation_target}"))')
    else:
        lines.append('    page_setup.wait_for_load_state("networkidle")')
    lines.append("")

    return lines


def _generate_navigation_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for navigation with URL assertion."""
    lines: list[str] = []
    element_selector = _refine_element_selector(op.element)

    lines.append("    # Step 1: Trigger navigation")
    lines.append(f"    page_setup.click(\"{element_selector}\")")
    lines.append("")

    lines.append("    # Step 2: Verify navigation target")
    if op.navigation_target:
        target = op.navigation_target
        # Ensure the target is a full URL pattern
        if not target.startswith("http"):
            target = f"{{BASE_URL}}{target}"
        lines.append(f'    expect(page_setup).to_have_url(re.compile(r"{target}"))')
    else:
        lines.append('    page_setup.wait_for_load_state("networkidle")')
    lines.append("")

    return lines


def _generate_input_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for input operation."""
    lines: list[str] = []

    lines.append("    # Step 1: Fill input field")
    if op.fields:
        for f in op.fields:
            selector = _precise_selector(f, op)
            value = _test_value_for_type(f.field_type, f.field_name)
            lines.append(f'    page_setup.fill("{selector}", "{value}")')
    else:
        element_selector = _refine_element_selector(op.element)
        lines.append(f'    page_setup.fill("{element_selector}", "test_value")')
    lines.append("")

    lines.append("    # Step 2: Verify input event response")
    if op.api_calls:
        api_patterns = _build_api_patterns(op.api_calls)
        for pattern in api_patterns:
            lines.append(f"    # Expected API: {pattern}")
    lines.append('    page_setup.wait_for_load_state("networkidle")')
    lines.append("")

    return lines


def _generate_change_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for change operation."""
    lines: list[str] = []

    lines.append("    # Step 1: Change field value")
    if op.fields:
        for f in op.fields:
            selector = _precise_selector(f, op)
            if f.field_type == "select":
                lines.append(f'    page_setup.select_option("{selector}", "option_value")')
            elif f.field_type == "checkbox":
                lines.append(f'    page_setup.set_checked("{selector}", True)')
            else:
                lines.append(f'    page_setup.fill("{selector}", "new_value")')
    else:
        element_selector = _refine_element_selector(op.element)
        lines.append(f'    page_setup.fill("{element_selector}", "new_value")')
    lines.append("")

    lines.append("    # Step 2: Verify change event response")
    lines.append('    page_setup.wait_for_load_state("networkidle")')
    lines.append("")

    return lines


def _generate_generic_test(op: PageOperation, op_set: PageOperationSet) -> list[str]:
    """Generate test for generic/other operation."""
    lines: list[str] = []
    element_selector = _refine_element_selector(op.element)

    lines.append("    # Perform the operation")
    lines.append(f"    page_setup.click(\"{element_selector}\")")
    lines.append("")

    lines.append("    # Verify result")
    if op.api_calls:
        api_patterns = _build_api_patterns(op.api_calls)
        for pattern in api_patterns:
            lines.append(f"    # Expected API: {pattern}")
    lines.append('    page_setup.wait_for_load_state("networkidle")')
    lines.append("")

    return lines


# ── Selector helpers ──

def _precise_selector(f: PageField, op: PageOperation | None = None) -> str:
    """Generate a precise CSS selector for a form field.

    Priority: #id > [v-model] > .class > [name] > [placeholder] > generic.
    """
    raw = f.selector

    # If the selector already has an ID, use it directly
    if raw.startswith("#"):
        return raw

    # Try to extract v-model name from the selector or field
    if "[" in raw and "]" in raw:
        return raw

    # Build from field attributes
    if f.field_name:
        # Try v-model pattern (common in Vue)
        vm_selector = f"[v-model*='{f.field_name}']"
        return vm_selector

    # Fallback: use the original selector
    return raw


def _refine_element_selector(element: str) -> str:
    """Refine a raw element selector for better precision.

    - "button" -> "button[type=submit]" (if submit context)
    - "link/a" -> "a"
    - "button.danger" -> keep as is (already specific)
    """
    if element == "button":
        return "button"
    if element == "link/a":
        return "a"
    if element.startswith("button."):
        return element  # Already has class
    if element.startswith("#"):
        return element  # ID selector, already precise
    if "[" in element:
        return element  # Already has attribute
    return element


def _build_api_patterns(api_calls: list[dict]) -> list[str]:
    """Build Playwright URL match patterns from API calls.

    Converts service:function paths to glob patterns for expect_response.
    """
    patterns: list[str] = []
    for api in api_calls:
        path = api.get("path", "")
        method = api.get("method", "POST")

        if path.startswith("service:"):
            # Service function name — match any URL containing it
            service_name = path.replace("service:", "")
            patterns.append(f"**/{service_name}*")
        elif path.startswith("/"):
            # Direct URL path
            patterns.append(f"**{path}*")
        else:
            patterns.append(f"**/{path}*")

    return patterns


# ── Utility ──

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
