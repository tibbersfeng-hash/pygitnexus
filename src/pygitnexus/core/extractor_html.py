"""Parse HTML/Thymeleaf template files using tree-sitter-html.

Strategy:
1. tree-sitter-html AST parsing for structure (elements, attributes, text)
2. Extract <script> blocks → parse with tree-sitter JavaScript parser
3. Extract form actions, event handlers, navigation via AST queries
4. Retain regex fallbacks for Thymeleaf-specific syntax (th:action, etc.)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from tree_sitter import Language, Parser

import tree_sitter_html

from .extractor_js import parse as parse_js
from .models import (
    CallSite,
    MethodDef,
    ParsedFile,
)


@dataclass
class HTMLEventBinding:
    """Represents an event binding extracted from HTML/Vue template."""
    element_tag: str          # HTML tag name, e.g. "button", "form", "input"
    element_attrs: dict[str, str]  # All attributes as dict
    event_type: str           # e.g. "click", "submit", "input", "change"
    handler_expr: str         # The expression/handler, e.g. "onSubmit()", "handleClick"
    line: int                 # Source line number
    element_text: str = ""    # Text content of the element (for button labels etc.)


@dataclass
class HTMLFormField:
    """Represents a form field extracted from HTML/Vue template."""
    field_type: str           # "text", "password", "email", "select", "textarea", "checkbox", "radio", "hidden", "file"
    field_name: str           # name attribute
    field_id: str             # id attribute
    placeholder: str          # placeholder text
    required: bool            # whether field is required
    label: str                # associated label text
    line: int                 # Source line number
    parent_form_action: str = ""   # parent form's action URL
    parent_form_method: str = "GET"  # parent form's HTTP method


def _get_language() -> Language:
    """Get tree-sitter-html Language singleton."""
    return Language(tree_sitter_html.language())


def _get_parser() -> Parser:
    """Get a tree-sitter Parser configured for HTML."""
    lang = _get_language()
    return Parser(lang)


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse an HTML template file using tree-sitter-html AST.

    1. Parse full HTML with tree-sitter-html
    2. Extract <script> blocks → parse via JS extractor
    3. Extract form actions, event bindings, navigation via AST queries
    4. Extract form fields for operation set generation
    """
    source = content.decode("utf-8", errors="replace")
    html_name = os.path.basename(file_path)

    result = ParsedFile(file_path=file_path)

    # 1. Extract and parse all <script> blocks via JS extractor
    script_blocks = _extract_script_blocks_ast(source)
    if script_blocks:
        combined = "\n".join(script_blocks)
        js_result = parse_js(file_path, combined.encode("utf-8"))
        result.methods.extend(js_result.methods)
        result.classes.extend(js_result.classes)
        result.calls.extend(js_result.calls)
        result.imports.extend(js_result.imports)
        result.annotations.extend(js_result.annotations)

    # 2. Parse full HTML AST
    parser = _get_parser()
    tree = parser.parse(content)

    # 3. Extract form actions, event bindings, navigation via AST
    _extract_ast_based_info(tree, source, file_path, html_name, result)

    # 4. Fallback: regex-based extraction for Thymeleaf-specific syntax
    _extract_thymeleaf_forms(source, file_path, html_name, result)

    return result


# ---------------------------------------------------------------------------
# Script block extraction via AST
# ---------------------------------------------------------------------------

def _extract_script_blocks_ast(source: str) -> list[str]:
    """Extract <script> block contents using AST traversal."""
    blocks: list[str] = []
    parser = _get_parser()
    tree = parser.parse(source.encode("utf-8"))

    for script_el in _walk_elements(tree.root_node, "element"):
        tag_name = _get_tag_name(script_el)
        if tag_name != "script":
            continue

        start_tag = _find_child(script_el, "start_tag")
        if start_tag:
            # Check if script has src attribute (external)
            has_src = False
            for attr in _walk_elements(start_tag, "attribute"):
                name_node = _find_child(attr, "attribute_name")
                if name_node and name_node.text.decode("utf-8", errors="replace") == "src":
                    has_src = True
                    break
            if has_src:
                continue

        # Get text children (not element children)
        text_parts = []
        for child in script_el.children:
            if child.type == "text":
                text_parts.append(child.text.decode("utf-8", errors="replace"))
            elif child.type == "raw_text":
                text_parts.append(child.text.decode("utf-8", errors="replace"))

        text = "\n".join(text_parts).strip()
        if text:
            blocks.append(text)

    return blocks


# ---------------------------------------------------------------------------
# AST-based extraction
# ---------------------------------------------------------------------------

def _extract_ast_based_info(
    tree, source: str, file_path: str, html_name: str, result: ParsedFile,
) -> None:
    """Extract HTTP endpoints, event bindings, and form fields from HTML AST."""
    for el in _walk_elements(tree.root_node, "element"):
        tag_name = _get_tag_name(el)
        if not tag_name:
            continue

        attrs = _get_attributes(el)
        line = _node_line(el, source)

        # Form actions
        if tag_name == "form":
            action = attrs.get("action", "")
            method = attrs.get("method", "GET").upper()
            if action and not action.startswith("#") and not action.startswith("javascript:"):
                _add_endpoint_call(result, html_name, _normalize_url(action), method, line)

            # Extract form fields
            _extract_form_fields(el, source, action, method, result)

        # Inline event handlers (onclick, onsubmit, oninput, onchange, etc.)
        _extract_inline_handlers(el, tag_name, attrs, source, html_name, result)

        # window.location in script blocks (already handled by JS parser)
        # <a href> navigation
        if tag_name == "a":
            href = attrs.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript:") and not href.startswith("mailto:"):
                _add_endpoint_call(result, html_name, _normalize_url(href), "GET", line)


def _extract_inline_handlers(
    el, tag_name: str, attrs: dict[str, str], source: str, html_name: str, result: ParsedFile,
) -> None:
    """Extract inline event handlers like onclick, onsubmit, etc."""
    event_map = {
        "onclick": "click",
        "onsubmit": "submit",
        "oninput": "input",
        "onchange": "change",
        "onmouseover": "hover",
        "onkeydown": "keypress",
        "onkeyup": "keypress",
        "onfocus": "focus",
        "onblur": "blur",
    }

    for attr_name, op_type in event_map.items():
        handler = attrs.get(attr_name, "")
        if handler:
            line = _node_line(el, source)
            # Extract function name from handler expression
            fn_match = re.search(r'\b([a-zA-Z_$][\w$]*)\s*\(', handler)
            handler_name = fn_match.group(1) if fn_match else handler.strip()

            # For navigation handlers
            if "location" in handler.lower():
                loc_match = re.search(r'''['"]([^'"]+)['"]''', handler)
                if loc_match:
                    _add_endpoint_call(result, html_name, _normalize_url(loc_match.group(1)), "GET", line)
            else:
                result.calls.append(CallSite(
                    caller_method=html_name,
                    caller_class="",
                    target_name=handler_name,
                    line=line,
                    receiver=None,
                    receiver_type=None,
                ))


def _extract_form_fields(
    form_el, source: str, form_action: str, form_method: str, result: ParsedFile,
) -> None:
    """Extract all form fields from a form element."""
    for el in _walk_elements(form_el, "element"):
        tag = _get_tag_name(el)
        if tag in ("input", "select", "textarea"):
            attrs = _get_attributes(el)
            field_type = attrs.get("type", "text") if tag == "input" else tag
            field_name = attrs.get("name", "")
            field_id = attrs.get("id", "")
            placeholder = attrs.get("placeholder", "")
            required = "required" in attrs
            line = _node_line(el, source)

            # Find associated label
            label = ""
            if field_id:
                # Look for <label for="field_id">
                for lbl in _walk_elements(form_el, "element"):
                    if _get_tag_name(lbl) == "label":
                        lbl_attrs = _get_attributes(lbl)
                        if lbl_attrs.get("for") == field_id:
                            label = _get_text_content(lbl, source)
                            break
            if not label:
                # Fallback: look for text in preceding sibling
                label = _get_text_content(el, source)[:50]

            result.methods.append(MethodDef(
                name=f"FormField({field_name or field_id})",
                class_name="",
                file_path=result.file_path,
                start_line=line,
                end_line=line,
                return_type="",
                is_static=False,
                is_public=True,
                is_constructor=False,
                content=f"FormField: type={field_type}, name={field_name}, id={field_id}, placeholder={placeholder}, required={required}, label={label}",
            ))


# ---------------------------------------------------------------------------
# Thymeleaf fallback
# ---------------------------------------------------------------------------

_TH_FORM_ACTION_RE = re.compile(
    r"<form\b[^>]+th:action\s*=\s*[\"']@{([^}]+)}[\"']",
    re.IGNORECASE,
)


def _extract_thymeleaf_forms(
    source: str, file_path: str, html_name: str, result: ParsedFile,
) -> None:
    """Extract Thymeleaf th:action="@{/path}" forms (not handled by tree-sitter)."""
    for match in _TH_FORM_ACTION_RE.finditer(source):
        action_url = match.group(1)
        if not action_url:
            continue
        form_tag = source[match.start():match.start() + 200]
        method_match = re.search(r'\bmethod\s*=\s*["\'](\w+)["\']', form_tag, re.IGNORECASE)
        http_method = (method_match.group(1).upper() if method_match else "GET")
        _add_endpoint_call(
            result, html_name, _normalize_url(action_url), http_method,
            _line_number_at(source, match.start()),
        )


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _walk_elements(node, tag_type: str = "element"):
    """Recursively walk all element nodes in the tree."""
    if node.type == tag_type:
        yield node
    for child in node.children:
        yield from _walk_elements(child, tag_type)


def _find_child(node, child_type: str):
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _get_tag_name(el) -> str:
    """Extract tag name from an element node."""
    start_tag = _find_child(el, "start_tag")
    if not start_tag:
        return ""
    tag_name_node = _find_child(start_tag, "tag_name")
    if tag_name_node:
        return tag_name_node.text.decode("utf-8", errors="replace")
    return ""


def _get_attributes(el) -> dict[str, str]:
    """Extract all attributes from an element as a dict."""
    attrs: dict[str, str] = {}
    start_tag = _find_child(el, "start_tag")
    if not start_tag:
        return attrs

    for attr in start_tag.children:
        if attr.type == "attribute":
            name_node = _find_child(attr, "attribute_name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8", errors="replace")

            # Vue directives: @click, :prop, v-on:click, v-bind:prop
            if name.startswith(("@", ":", "v-")):
                value_node = _find_child(attr, "quoted_attribute_value")
                if value_node:
                    # quoted_attribute_value has "..."  — get inner text
                    inner = ""
                    for gc in value_node.children:
                        if gc.type == "attribute_value":
                            inner = gc.text.decode("utf-8", errors="replace")
                    attrs[name] = inner
                else:
                    attrs[name] = ""
                continue

            value_node = _find_child(attr, "quoted_attribute_value")
            if value_node:
                inner = ""
                for gc in value_node.children:
                    if gc.type == "attribute_value":
                        inner = gc.text.decode("utf-8", errors="replace")
                attrs[name.lower()] = inner
            else:
                # Boolean attribute (no value)
                attrs[name.lower()] = "true"

    return attrs


def _get_text_content(el, source: str) -> str:
    """Extract text content from an element (for labels, button text, etc.)."""
    parts = []
    for child in el.children:
        if child.type in ("text", "raw_text"):
            parts.append(child.text.decode("utf-8", errors="replace").strip())
    return " ".join(p for p in parts if p)


def _node_line(node, source: str) -> int:
    """Get 1-based line number for a tree-sitter node."""
    return node.start_point[0] + 1


def _line_number_at(source: str, pos: int) -> int:
    """Calculate 1-based line number at a given position."""
    return source[:pos].count("\n") + 1


def _extract_script_blocks(source: str) -> list[str]:
    """Extract content of all <script> blocks (not <script src=...>).

    Regex fallback for cases where AST parsing might miss something.
    """
    blocks: list[str] = []
    pattern = r"<script[^>]*>([\s\S]*?)</script>"
    for match in re.finditer(pattern, source):
        body = match.group(1).strip()
        if body:
            tag = match.group(0)[:match.group(0).index(">")]
            if "src=" in tag and not body:
                continue
            blocks.append(body)
    return blocks


# ---------------------------------------------------------------------------
# URL normalization and endpoint call creation
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """Normalize a URL path for matching against Spring routes."""
    if "?" in url:
        url = url.split("?", 1)[0]
    while url.startswith("./") or url.startswith("../"):
        url = url.lstrip(".")
        url = url.lstrip("/")
    if not url.startswith("/"):
        url = "/" + url
    return url


def _add_endpoint_call(
    result: ParsedFile,
    html_name: str,
    http_path: str,
    http_method: str,
    line: int,
) -> None:
    """Add a CallSite for an HTTP endpoint reference from HTML."""
    target_name = http_path.rstrip("/").split("/")[-1] or "index"

    result.calls.append(CallSite(
        caller_method=html_name,
        caller_class="",
        target_name=target_name,
        line=line,
        receiver=None,
        receiver_type=None,
        http_method=http_method,
        http_path=http_path,
    ))

    # Create synthetic MethodDef for this HTML file
    if not any(m.name == html_name and m.file_path == result.file_path for m in result.methods):
        result.methods.append(MethodDef(
            name=html_name,
            class_name="",
            file_path=result.file_path,
            start_line=line,
            end_line=line,
            return_type="",
            is_static=False,
            is_public=True,
            is_constructor=False,
            content=f"/* Synthetic method for {html_name} — HTTP endpoint references */",
        ))
