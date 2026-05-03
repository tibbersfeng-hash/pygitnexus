"""Parse Vue Single File Components (.vue) using TypeScript parser on <script> blocks."""

from __future__ import annotations

import os
import re

from .extractor_ts import parse as parse_ts
from .models import (
    CallSite,
    ParsedFile,
)


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse a Vue SFC file by extracting <script> block and parsing as TypeScript.

    Vue SFC files have three sections:
    - <template> - HTML-like template with component usage
    - <script> - TypeScript/JavaScript code (setup or options API)
    - <style> - CSS styles

    We extract and parse the <script> block, then also extract template
    component references.
    """
    source = content.decode("utf-8", errors="replace")

    # Extract <script> block content
    script_content = _extract_script_block(source)
    if script_content is None:
        # No script block — return empty result
        result = ParsedFile(file_path=file_path)
        return result

    script_bytes = script_content.encode("utf-8")

    # Determine if <script setup> (Composition API) or <script> (Options API)
    is_setup = "<script setup" in source

    # Parse the script content using TypeScript extractor
    result = parse_ts(file_path, script_bytes)

    # Extract function calls from template expressions as module-level calls
    _extract_template_expr_calls(source, file_path, result)

    # Also extract template component references as calls
    if is_setup:
        _extract_template_refs_setup(source, file_path, result)
    else:
        _extract_template_refs_options(source, file_path, result)

    return result


def _extract_template_expr_calls(
    source: str, file_path: str, result: ParsedFile
) -> None:
    """Extract function calls from Vue template expressions as module-level calls.

    In <script setup>, all top-level declarations are available in the template.
    GitNexus treats function calls from template expressions (e.g. @click="handler()")
    as module-level calls with caller_method = filename.

    Patterns matched:
    - @event="fn()" / v-on:event="fn()"
    - :prop="fn()" / v-bind:prop="fn()"
    - v-if="fn()" / v-show="fn()"
    - {{ fn() }}
    """
    vue_name = os.path.basename(file_path)

    template = _extract_template(source)
    if not template:
        return

    # Collect identifiers defined in the script (imports + top-level declarations)
    available_identifiers: set[str] = set()
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                available_identifiers.add(parts[1])
    # Also add method names from the parsed script
    for method in result.methods:
        available_identifiers.add(method.name)
    for cls in result.classes:
        available_identifiers.add(cls.name)

    # Extract expressions from template attributes and interpolation
    # Match: @xxx="...", :xxx="...", v-xxx="...", {{ ... }}
    expr_patterns = [
        r'@(?:\w+)(?::\w+)?\s*=\s*"([^"]+)"',  # @click="..."
        r'v-on:(?:\w+)(?::\w+)?\s*=\s*"([^"]+)"',  # v-on:click="..."
        r':(\w[\w-]*)\s*=\s*"([^"]+)"',  # :prop="..."
        r'v-bind:(\w[\w-]*)\s*=\s*"([^"]+)"',  # v-bind:prop="..."
        r'v-(?:if|else-if|show|for)\s*=\s*"([^"]+)"',  # v-if="..."
        r'\{\{(.+?)\}\}',  # {{ ... }}
    ]

    for pattern in expr_patterns:
        for match in re.finditer(pattern, template):
            # For patterns with a named directive (:prop, v-bind), the expression
            # is in group(2). For others (@event, v-on, v-if, {{ }}) it's group(1).
            if match.lastindex and match.lastindex >= 2:
                expr = match.group(2)
            elif match.lastindex and match.lastindex >= 1:
                expr = match.group(1)
            else:
                expr = match.group(0)
            # Find function call patterns: identifier(...) or identifier.value
            for fn_match in re.finditer(r'\b([a-zA-Z_$][\w$]*)\s*\(', expr):
                fn_name = fn_match.group(1)
                if fn_name in available_identifiers:
                    result.calls.append(CallSite(
                        caller_method=vue_name,
                        caller_class="",
                        target_name=fn_name,
                        line=_line_number_at(source, match.start()),
                        receiver=None,
                        receiver_type=None,
                    ))
            # Also match chained calls: funcName().methodName(...)
            # e.g. useUserStoreHook().SET_CURRENTPAGE(4)
            for chain_match in re.finditer(
                r'\b([a-zA-Z_$][\w$]*)\s*\(\s*\)\.\s*([a-zA-Z_$][\w$]*)\s*\(',
                expr
            ):
                hook_name = chain_match.group(1)
                method_name = chain_match.group(2)
                if hook_name in available_identifiers:
                    result.calls.append(CallSite(
                        caller_method=vue_name,
                        caller_class="",
                        target_name=method_name,
                        line=_line_number_at(source, match.start()),
                        receiver=hook_name,
                        receiver_type=None,
                    ))
            # Also match bare identifiers (not followed by paren): @change="handler"
            for bare_match in re.finditer(r'\b([a-zA-Z_$][\w$]*)\b', expr):
                bare_name = bare_match.group(1)
                # Skip if it looks like a keyword, variable, or is already matched
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
                if bare_name in skip:
                    continue
                if bare_name in available_identifiers:
                    result.calls.append(CallSite(
                        caller_method=vue_name,
                        caller_class="",
                        target_name=bare_name,
                        line=_line_number_at(source, match.start()),
                        receiver=None,
                        receiver_type=None,
                    ))


def _extract_script_block(source: str) -> str | None:
    """Extract content between <script> and </script> tags."""
    # Match <script> or <script setup> or <script lang="ts"> etc.
    pattern = r"<script[^>]*>([\s\S]*?)</script>"
    match = re.search(pattern, source)
    if match:
        return match.group(1)
    return None


def _extract_template_refs_setup(
    source: str, file_path: str, result: ParsedFile
) -> None:
    """Extract component references from <template> for <script setup> style.

    In Composition API, components are typically imported and used directly
    in the template as tags. We find tag names that match imported components.
    """
    vue_name = os.path.basename(file_path)

    # Get imported component names mapped to their .vue file names
    imported_components: dict[str, str] = {}  # symbol_name -> target_file
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                imported_components[parts[1]] = parts[0]  # LoginPhone -> ./components/LoginPhone.vue

    # For matching against GitNexus behavior: use the file basename as target
    imported_file_targets: dict[str, str] = {}  # symbol_name -> basename.vue
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2 and parts[0].endswith(".vue"):
                file_base = parts[0].split("/")[-1]  # e.g., LoginPhone.vue
                imported_file_targets[parts[1]] = file_base

    # Find component tags in template
    template = _extract_template(source)
    if not template:
        return

    # Find all HTML-like tags: <ComponentName>, <component-name>
    tag_pattern = r"<(/?)([A-Z][a-zA-Z0-9]*)[\s/>]"
    for match in re.finditer(tag_pattern, template):
        is_closing = match.group(1) == "/"
        tag_name = match.group(2)

        if is_closing:
            continue

        if tag_name in imported_file_targets:
            # Use .vue file basename as target to match GitNexus
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
            # Fallback: non-Vue imported components
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
    """Extract component references from <template> for Options API style.

    In Options API, components are registered in the `components` option
    and used as tags in the template.
    """
    vue_name = os.path.basename(file_path)

    # Get registered component names from `components: { ... }`
    registered_components: set[str] = set()
    components_match = re.search(r'components\s*:\s*\{([^}]+)\}', source)
    if components_match:
        # Find identifiers in the components object
        for m in re.finditer(r'(\w+)\s*[,:\n}]', components_match.group(1)):
            registered_components.add(m.group(1))

    # Also add imported components
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                registered_components.add(parts[1])

    # Find component tags in template
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


def _extract_template(source: str) -> str | None:
    """Extract content between <template> and </template> tags.

    Handles nested <template> slots (e.g., <template #dropdown>) by
    finding the outermost template block and its matching closing tag.
    """
    # Find all template start and end positions
    starts = [(m.start(), m.group()) for m in re.finditer(r"<template[^>]*>", source)]
    ends = [(m.start(), m.group()) for m in re.finditer(r"</template>", source)]

    if not starts or not ends:
        return None

    # Find the outermost template: the one whose start is earliest
    # and whose matching end is the last </template> that balances the nesting
    outermost_start = starts[0][0]
    outermost_tag = starts[0][1]

    # Count nesting: find the </template> that closes the outermost one
    nesting = 0
    matched_end = None
    # Search from after the opening tag
    search_from = outermost_start + len(outermost_tag)
    for m in re.finditer(r"<template[^>]*>|</template>", source[search_from:]):
        tag = m.group()
        if tag.startswith("</template"):
            nesting -= 1
            if nesting == -1:
                # This closes the outermost template
                matched_end = search_from + m.start()
                break
        else:
            nesting += 1

    if matched_end is None:
        # Fallback: use last </template>
        if ends:
            matched_end = ends[-1][0]
        else:
            return None

    return source[outermost_start + len(outermost_tag):matched_end]


def _line_number_at(source: str, pos: int) -> int:
    """Calculate the 1-based line number at a given byte position."""
    return source[:pos].count("\n") + 1
