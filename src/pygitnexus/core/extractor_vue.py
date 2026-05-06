"""Parse Vue Single File Components (.vue) using TypeScript parser on <script> blocks
and tree-sitter-html AST on <template> blocks.
"""

from __future__ import annotations

import os
import re
from tree_sitter import Language, Parser

import tree_sitter_html

from .extractor_ts import parse as parse_ts
from .models import (
    CallSite,
    MethodDef,
    ParsedFile,
)


def _get_parser() -> Parser:
    """Get a tree-sitter Parser configured for HTML."""
    lang = Language(tree_sitter_html.language())
    return Parser(lang)


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse a Vue SFC file.

    1. Extract <script> block → parse as TypeScript
    2. Extract <template> block → parse with tree-sitter-html AST
    3. Extract event bindings, component refs, expressions from template AST
    4. Regex fallback for edge cases
    """
    source = content.decode("utf-8", errors="replace")

    # Extract <script> block content
    script_content = _extract_script_block(source)
    if script_content is None:
        result = ParsedFile(file_path=file_path)
        return result

    script_bytes = script_content.encode("utf-8")

    # Determine if <script setup> (Composition API) or <script> (Options API)
    is_setup = "<script setup" in source

    # Parse the script content using TypeScript extractor
    result = parse_ts(file_path, script_bytes)

    # Extract template AST
    template_content = _extract_template(source)

    # Extract from template using AST + regex fallback
    if template_content:
        _extract_template_ast(template_content, source, file_path, result)
    else:
        _extract_template_expr_calls(source, file_path, result)

    # Extract component references
    if is_setup:
        _extract_template_refs_setup(source, file_path, result)
    else:
        _extract_template_refs_options(source, file_path, result)

    return result


# ---------------------------------------------------------------------------
# AST-based template extraction
# ---------------------------------------------------------------------------

_EVENT_ATTR_MAP = {
    "@click": "click",
    "@submit": "submit",
    "@input": "input",
    "@change": "change",
    "@focus": "focus",
    "@blur": "blur",
    "@keydown": "keypress",
    "@keyup": "keypress",
    "@mouseover": "hover",
    "@mouseout": "hover",
    "@mouseenter": "hover",
    "@mouseleave": "hover",
    "v-on:click": "click",
    "v-on:submit": "submit",
    "v-on:input": "input",
    "v-on:change": "change",
    "v-on:focus": "focus",
    "v-on:blur": "blur",
    "v-on:keydown": "keypress",
    "v-on:keyup": "keypress",
    "v-on:mouseover": "hover",
    "v-on:mouseout": "hover",
    "v-on:mouseenter": "hover",
    "v-on:mouseleave": "hover",
}


def _extract_template_ast(
    template: str, full_source: str, file_path: str, result: ParsedFile,
) -> None:
    """Extract event bindings, form fields, and expressions from template AST."""
    vue_name = os.path.basename(file_path)

    # Collect available identifiers from script
    available_identifiers: set[str] = set()
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                available_identifiers.add(parts[1])
    for method in result.methods:
        available_identifiers.add(method.name)
    for cls in result.classes:
        available_identifiers.add(cls.name)

    # Parse template with tree-sitter-html
    parser = _get_parser()
    tree = parser.parse(template.encode("utf-8"))

    for el in _walk_elements(tree.root_node):
        tag_name = _get_tag_name(el)
        if not tag_name:
            continue

        attrs = _get_attrs(el)
        line = el.start_point[0] + 1

        # Extract Vue event bindings (@xxx, v-on:xxx)
        for attr_name, op_type in _EVENT_ATTR_MAP.items():
            handler = attrs.get(attr_name, "")
            if handler:
                _add_handler_calls(handler, vue_name, tag_name, attr_name, line, available_identifiers, result)

        # Extract form fields for operation set
        if tag_name == "form":
            _extract_vue_form_fields(el, template, vue_name, result)

        if tag_name in ("input", "select", "textarea"):
            _extract_vue_form_field(tag_name, attrs, vue_name, line, result)

        # Extract v-model bindings
        v_model = attrs.get("v-model", "")
        if v_model:
            result.calls.append(CallSite(
                caller_method=vue_name,
                caller_class="",
                target_name=v_model,
                line=line,
                receiver=None,
                receiver_type=None,
            ))

    # Regex fallback: catch expressions the AST might miss
    _extract_template_expr_calls(full_source, file_path, result)


def _add_handler_calls(
    handler: str, vue_name: str, tag: str, event: str, line: int,
    available: set[str], result: ParsedFile,
) -> None:
    """Parse a Vue event handler expression and add CallSites."""
    # Direct calls: fn()
    for m in re.finditer(r'\b([a-zA-Z_$][\w$]*)\s*\(', handler):
        fn_name = m.group(1)
        if fn_name in available:
            result.calls.append(CallSite(
                caller_method=vue_name,
                caller_class="",
                target_name=fn_name,
                line=line,
                receiver=None,
                receiver_type=None,
            ))

    # Chained: useXxx().methodName()
    for m in re.finditer(r'\b([a-zA-Z_$][\w$]*)\s*\(\s*\)\.\s*([a-zA-Z_$][\w$]*)\s*\(', handler):
        hook_name = m.group(1)
        method_name = m.group(2)
        if hook_name in available:
            result.calls.append(CallSite(
                caller_method=vue_name,
                caller_class="",
                target_name=method_name,
                line=line,
                receiver=hook_name,
                receiver_type=None,
            ))

    # Bare identifier: @change="handler" (no parens)
    bare = handler.strip().split(".")[0].split("(")[0]
    if bare in available:
        # Only add if not already added via call pattern
        existing_targets = {c.target_name for c in result.calls if c.caller_method == vue_name and c.line == line}
        if bare not in existing_targets:
            result.calls.append(CallSite(
                caller_method=vue_name,
                caller_class="",
                target_name=bare,
                line=line,
                receiver=None,
                receiver_type=None,
            ))


def _extract_vue_form_fields(el, template: str, vue_name: str, result: ParsedFile) -> None:
    """Extract form fields from a Vue template form element."""
    for child_el in _walk_elements(el):
        tag = _get_tag_name(child_el)
        if tag in ("input", "select", "textarea"):
            attrs = _get_attrs(child_el)
            field_type = attrs.get("type", "text") if tag == "input" else tag
            field_name = attrs.get("name", "")
            v_model = attrs.get("v-model", "")
            field_id = attrs.get("id", "")
            placeholder = attrs.get("placeholder", "")
            required = "required" in attrs
            line = child_el.start_point[0] + 1
            label = _get_text_content(child_el)

            result.methods.append(MethodDef(
                name=f"FormField({v_model or field_name or field_id})",
                class_name="",
                file_path=result.file_path,
                start_line=line,
                end_line=line,
                return_type="",
                is_static=False,
                is_public=True,
                is_constructor=False,
                content=f"FormField: tag={tag}, type={field_type}, name={field_name}, v-model={v_model}, placeholder={placeholder}, required={required}, label={label}",
            ))


def _extract_vue_form_field(tag: str, attrs: dict[str, str], vue_name: str, line: int, result: ParsedFile) -> None:
    """Extract a single form field (outside of a form element)."""
    field_type = attrs.get("type", "text") if tag == "input" else tag
    field_name = attrs.get("name", "")
    v_model = attrs.get("v-model", "")
    field_id = attrs.get("id", "")
    placeholder = attrs.get("placeholder", "")
    required = "required" in attrs
    label = attrs.get("label", "") or attrs.get("aria-label", "")

    result.methods.append(MethodDef(
        name=f"FormField({v_model or field_name or field_id})",
        class_name="",
        file_path=result.file_path,
        start_line=line,
        end_line=line,
        return_type="",
        is_static=False,
        is_public=True,
        is_constructor=False,
        content=f"FormField: tag={tag}, type={field_type}, name={field_name}, v-model={v_model}, placeholder={placeholder}, required={required}, label={label}",
    ))


# ---------------------------------------------------------------------------
# Regex-based template expression extraction (fallback)
# ---------------------------------------------------------------------------

def _extract_template_expr_calls(
    source: str, file_path: str, result: ParsedFile
) -> None:
    """Extract function calls from Vue template expressions (regex fallback)."""
    vue_name = os.path.basename(file_path)

    template = _extract_template(source)
    if not template:
        return

    available_identifiers: set[str] = set()
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                available_identifiers.add(parts[1])
    for method in result.methods:
        available_identifiers.add(method.name)
    for cls in result.classes:
        available_identifiers.add(cls.name)

    expr_patterns = [
        r'@(?:\w+)(?::\w+)?\s*=\s*"([^"]+)"',
        r'v-on:(?:\w+)(?::\w+)?\s*=\s*"([^"]+)"',
        r':(\w[\w-]*)\s*=\s*"([^"]+)"',
        r'v-bind:(\w[\w-]*)\s*=\s*"([^"]+)"',
        r'v-(?:if|else-if|show|for)\s*=\s*"([^"]+)"',
        r'\{\{(.+?)\}\}',
    ]

    seen: set[tuple[str, int]] = set()
    for pattern in expr_patterns:
        for match in re.finditer(pattern, template):
            if match.lastindex and match.lastindex >= 2:
                expr = match.group(2)
            elif match.lastindex and match.lastindex >= 1:
                expr = match.group(1)
            else:
                expr = match.group(0)

            for fn_match in re.finditer(r'\b([a-zA-Z_$][\w$]*)\s*\(', expr):
                fn_name = fn_match.group(1)
                if fn_name in available_identifiers:
                    key = (fn_name, _line_number_at(source, match.start()))
                    if key not in seen:
                        seen.add(key)
                        result.calls.append(CallSite(
                            caller_method=vue_name,
                            caller_class="",
                            target_name=fn_name,
                            line=key[1],
                            receiver=None,
                            receiver_type=None,
                        ))

            for chain_match in re.finditer(
                r'\b([a-zA-Z_$][\w$]*)\s*\(\s*\)\.\s*([a-zA-Z_$][\w$]*)\s*\(',
                expr
            ):
                hook_name = chain_match.group(1)
                method_name = chain_match.group(2)
                if hook_name in available_identifiers:
                    key = (method_name, _line_number_at(source, match.start()))
                    if key not in seen:
                        seen.add(key)
                        result.calls.append(CallSite(
                            caller_method=vue_name,
                            caller_class="",
                            target_name=method_name,
                            line=key[1],
                            receiver=hook_name,
                            receiver_type=None,
                        ))

            skip = {
                'true', 'false', 'null', 'undefined', 'this', 'new',
                'return', 'if', 'else', 'for', 'while', 'in', 'of',
                'const', 'let', 'var', 'function', 'import', 'export',
                'from', 'default', 'class', 'extends', 'try', 'catch',
                'async', 'await', 'yield', 'typeof', 'instanceof',
                'void', 'delete', 'do', 'switch', 'case', 'break',
                'continue', 'throw', 'finally', 'with', 'debugger',
                '$t', '$event',
            }
            for bare_match in re.finditer(r'\b([a-zA-Z_$][\w$]*)\b', expr):
                bare_name = bare_match.group(1)
                if bare_name in skip:
                    continue
                if bare_name in available_identifiers:
                    key = (bare_name, _line_number_at(source, match.start()))
                    if key not in seen:
                        seen.add(key)
                        result.calls.append(CallSite(
                            caller_method=vue_name,
                            caller_class="",
                            target_name=bare_name,
                            line=key[1],
                            receiver=None,
                            receiver_type=None,
                        ))


# ---------------------------------------------------------------------------
# Component reference extraction
# ---------------------------------------------------------------------------

def _extract_template_refs_setup(
    source: str, file_path: str, result: ParsedFile
) -> None:
    """Extract component references from <template> for <script setup> style."""
    vue_name = os.path.basename(file_path)

    imported_components: dict[str, str] = {}
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                imported_components[parts[1]] = parts[0]

    imported_file_targets: dict[str, str] = {}
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2 and parts[0].endswith(".vue"):
                file_base = parts[0].split("/")[-1]
                imported_file_targets[parts[1]] = file_base

    template = _extract_template(source)
    if not template:
        return

    tag_pattern = r"<(/?)([A-Z][a-zA-Z0-9]*)[\s/>]"
    for match in re.finditer(tag_pattern, template):
        is_closing = match.group(1) == "/"
        tag_name = match.group(2)

        if is_closing:
            continue

        if tag_name in imported_file_targets:
            target_name = imported_file_targets[tag_name]
            result.calls.append(CallSite(
                caller_method=vue_name,
                caller_class="",
                target_name=target_name,
                line=_line_number_at(source, match.start()),
                receiver=None,
                receiver_type=None,
            ))
        elif tag_name in imported_components:
            result.calls.append(CallSite(
                caller_method=vue_name,
                caller_class="",
                target_name=tag_name,
                line=_line_number_at(source, match.start()),
                receiver=None,
                receiver_type=None,
            ))


def _extract_template_refs_options(
    source: str, file_path: str, result: ParsedFile
) -> None:
    """Extract component references from <template> for Options API style."""
    vue_name = os.path.basename(file_path)

    registered_components: set[str] = set()
    components_match = re.search(r'components\s*:\s*\{([^}]+)\}', source)
    if components_match:
        for m in re.finditer(r'(\w+)\s*[,:\n}]', components_match.group(1)):
            registered_components.add(m.group(1))

    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                registered_components.add(parts[1])

    template = _extract_template(source)
    if not template:
        return

    tag_pattern = r"<(/?)([A-Z][a-zA-Z0-9]*)[\s/>]"
    for match in re.finditer(tag_pattern, template):
        is_closing = match.group(1) == "/"
        tag_name = match.group(2)

        if is_closing:
            continue

        if tag_name in registered_components:
            result.calls.append(CallSite(
                caller_method=vue_name,
                caller_class="",
                target_name=tag_name,
                line=_line_number_at(source, match.start()),
                receiver=None,
                receiver_type=None,
            ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _walk_elements(node):
    """Recursively walk all element nodes."""
    if node.type == "element":
        yield node
    for child in node.children:
        yield from _walk_elements(child)


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


def _get_attrs(el) -> dict[str, str]:
    """Extract all attributes from an element."""
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

            value_node = _find_child(attr, "quoted_attribute_value")
            if value_node:
                inner = ""
                for gc in value_node.children:
                    if gc.type == "attribute_value":
                        inner = gc.text.decode("utf-8", errors="replace")
                attrs[name] = inner
            else:
                attrs[name] = "true"

    return attrs


def _get_text_content(el) -> str:
    """Extract text content from an element."""
    parts = []
    for child in el.children:
        if child.type in ("text", "raw_text"):
            t = child.text.decode("utf-8", errors="replace").strip()
            if t:
                parts.append(t)
    return " ".join(parts)


def _extract_script_block(source: str) -> str | None:
    """Extract content between <script> and </script> tags."""
    pattern = r"<script[^>]*>([\s\S]*?)</script>"
    match = re.search(pattern, source)
    if match:
        return match.group(1)
    return None


def _extract_template(source: str) -> str | None:
    """Extract content between <template> and </template> tags."""
    starts = [(m.start(), m.group()) for m in re.finditer(r"<template[^>]*>", source)]
    ends = [(m.start(), m.group()) for m in re.finditer(r"</template>", source)]

    if not starts or not ends:
        return None

    outermost_start = starts[0][0]
    outermost_tag = starts[0][1]

    nesting = 0
    matched_end = None
    search_from = outermost_start + len(outermost_tag)
    for m in re.finditer(r"<template[^>]*>|</template>", source[search_from:]):
        tag = m.group()
        if tag.startswith("</template"):
            nesting -= 1
            if nesting == -1:
                matched_end = search_from + m.start()
                break
        else:
            nesting += 1

    if matched_end is None:
        if ends:
            matched_end = ends[-1][0]
        else:
            return None

    return source[outermost_start + len(outermost_tag):matched_end]


def _line_number_at(source: str, pos: int) -> int:
    """Calculate the 1-based line number at a given byte position."""
    return source[:pos].count("\n") + 1
