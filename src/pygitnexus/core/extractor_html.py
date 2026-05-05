"""Parse HTML/Thymeleaf template files.

Strategy:
1. Extract <script> blocks and parse with tree-sitter JavaScript parser
2. Extract form actions (<form action="...">) as HTTP endpoint references
3. Extract inline event handlers (onclick, onsubmit) with URL patterns
4. Extract window.location.href assignments as navigation targets
"""

from __future__ import annotations

import os
import re

from .extractor_js import parse as parse_js
from .models import (
    CallSite,
    MethodDef,
    ParsedFile,
)


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse an HTML template file.

    Extracts JavaScript from <script> blocks and re-parses via the JS extractor,
    then additionally extracts form actions, URL navigations, and inline event handlers
    from the HTML template itself.
    """
    source = content.decode("utf-8", errors="replace")
    html_name = os.path.basename(file_path)

    result = ParsedFile(file_path=file_path)

    # 1. Extract and parse all <script> blocks via JS extractor
    script_blocks = _extract_script_blocks(source)
    if script_blocks:
        combined = "\n".join(script_blocks)
        js_result = parse_js(file_path, combined.encode("utf-8"))
        result.methods.extend(js_result.methods)
        result.classes.extend(js_result.classes)
        result.calls.extend(js_result.calls)
        result.imports.extend(js_result.imports)
        result.annotations.extend(js_result.annotations)

    # 2. Extract form actions → call to backend URL
    _extract_form_actions(source, file_path, html_name, result)

    # 3. Extract window.location.href assignments as navigation calls
    _extract_location_nav(source, file_path, html_name, result)

    # 4. Extract $.ajax / $.get / $.post calls from inline scripts
    _extract_jquery_ajax(source, file_path, html_name, result)

    # 5. Extract fetch() calls
    _extract_fetch_calls(source, file_path, html_name, result)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_script_blocks(source: str) -> list[str]:
    """Extract content of all <script> blocks (not <script src=...>)."""
    blocks: list[str] = []
    # Match <script ...>...</script> — but skip ones that only have src (external)
    pattern = r"<script[^>]*>([\s\S]*?)</script>"
    for match in re.finditer(pattern, source):
        body = match.group(1).strip()
        # Skip empty blocks and external-only scripts
        if body and "src=" not in match.group(0)[:50] or body:
            # Check if the script tag has a src attribute pointing to external file
            tag = match.group(0)[:match.group(0).index(">")]
            if "src=" in tag and not body:
                continue
            if body:
                blocks.append(body)
    return blocks


def _line_number_at(source: str, pos: int) -> int:
    """Calculate 1-based line number at a given position."""
    return source[:pos].count("\n") + 1


# ---------------------------------------------------------------------------
# Form action extraction
# ---------------------------------------------------------------------------

_FORM_ACTION_RE = re.compile(
    r"<form\b[^>]+(?:action)\s*=\s*[\"']([^\"'>]*)[\"']",
    re.IGNORECASE,
)
_TH_FORM_ACTION_RE = re.compile(
    r"<form\b[^>]+th:action\s*=\s*[\"']@{([^}]+)}[\"']",
    re.IGNORECASE,
)


def _extract_form_actions(
    source: str, file_path: str, html_name: str, result: ParsedFile,
) -> None:
    """Extract <form action="..."> as backend URL references.

    Creates a CallSite with target_name derived from the URL path,
    and stores the HTTP path in the CallSite for USES_ENDPOINT matching.
    """
    # Standard HTML form actions
    for match in _FORM_ACTION_RE.finditer(source):
        action_url = match.group(1)
        if not action_url or action_url.startswith("#") or action_url.startswith("javascript:"):
            continue

        # Determine HTTP method (forms with method attribute)
        form_tag = source[match.start():match.start() + 200]
        method_match = re.search(r'\bmethod\s*=\s*["\'](\w+)["\']', form_tag, re.IGNORECASE)
        http_method = (method_match.group(1).upper() if method_match else "GET")

        _add_endpoint_call(
            result, html_name, _normalize_url(action_url), http_method,
            _line_number_at(source, match.start()),
        )

    # Thymeleaf th:action="@{/path}"
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
# window.location.href / assign / replace
# ---------------------------------------------------------------------------

_LOCATION_NAV_RE = re.compile(
    r"(?:window\.location\.(?:href|assign|replace)|location\.(?:href|assign|replace))\s*=\s*[\"']([^\"'>]+)[\"']",
)


def _extract_location_nav(
    source: str, file_path: str, html_name: str, result: ParsedFile,
) -> None:
    """Extract window.location.href = 'url' patterns."""
    for match in _LOCATION_NAV_RE.finditer(source):
        url = match.group(1)
        if not url or url.startswith("#") or url.startswith("javascript:"):
            continue

        _add_endpoint_call(
            result, html_name, _normalize_url(url), "GET",
            _line_number_at(source, match.start()),
        )


# ---------------------------------------------------------------------------
# $.ajax() extraction from HTML
# ---------------------------------------------------------------------------

_JQUERY_AJAX_RE = re.compile(
    r"\$\.(ajax|get|post|put|delete|patch)\s*\(",
)

# Match $.ajax({ ..., type: 'POST', url: '/path', ... })
_AJAX_CONFIG_RE = re.compile(
    r"\$\.ajax\s*\(\s*\{([^}]+)\}",
    re.DOTALL,
)

# Match $.get('/path', ...) / $.post('/path', ...)
_JQUERY_SHORT_RE = re.compile(
    r"\$\.(get|post|put|delete|patch)\s*\(\s*[\"']([^\"'>]+)[\"']",
)


def _extract_jquery_ajax(
    source: str, file_path: str, html_name: str, result: ParsedFile,
) -> None:
    """Extract jQuery AJAX calls from HTML script blocks."""
    # $.ajax({ type: 'POST', url: '/path', ... })
    for match in _AJAX_CONFIG_RE.finditer(source):
        config = match.group(1)
        url_match = re.search(r"""url\s*:\s*["']([^"']+)["']""", config)
        type_match = re.search(r"""(?:type|method)\s*:\s*["'](\w+)["']""", config, re.IGNORECASE)
        if url_match:
            http_method = (type_match.group(1).upper() if type_match else "GET")
            _add_endpoint_call(
                result, html_name, _normalize_url(url_match.group(1)), http_method,
                _line_number_at(source, match.start()),
            )

    # $.get('/path', ...) / $.post('/path', ...)
    for match in _JQUERY_SHORT_RE.finditer(source):
        method = match.group(1).upper()
        url = match.group(2)
        _add_endpoint_call(
            result, html_name, _normalize_url(url), method,
            _line_number_at(source, match.start()),
        )


# ---------------------------------------------------------------------------
# fetch() extraction
# ---------------------------------------------------------------------------

_FETCH_RE = re.compile(
    r"\bfetch\s*\(\s*[\"']([^\"'>]+)[\"']",
)


def _extract_fetch_calls(
    source: str, file_path: str, html_name: str, result: ParsedFile,
) -> None:
    """Extract fetch('/path') calls from HTML script blocks."""
    for match in _FETCH_RE.finditer(source):
        url = match.group(1)
        # Check if this is not in a <script src=...> external reference
        if not url or url.startswith("#") or url.startswith("javascript:"):
            continue

        # Try to determine method from the second argument
        context = source[match.end():match.end() + 200]
        method_match = re.search(r"""method\s*:\s*["'](\w+)["']""", context, re.IGNORECASE)
        http_method = (method_match.group(1).upper() if method_match else "GET")

        _add_endpoint_call(
            result, html_name, _normalize_url(url), http_method,
            _line_number_at(source, match.start()),
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """Normalize a URL path for matching against Spring routes.

    - Remove leading ./ and ../
    - Ensure leading /
    - Strip query parameters
    """
    # Strip query params
    if "?" in url:
        url = url.split("?", 1)[0]

    # Remove leading ./ and ../
    while url.startswith("./") or url.startswith("../"):
        url = url.lstrip(".")
        url = url.lstrip("/")

    # Ensure leading /
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
    """Add a CallSite for an HTTP endpoint reference from HTML.

    The target_name is derived from the path for cross-reference.
    The http_path and http_method are stored for USES_ENDPOINT matching.
    Also creates a synthetic MethodDef for the HTML file so the pipeline
    can resolve the caller_id for USES_ENDPOINT relations.
    """
    # Derive a symbolic target name from the path for display purposes
    # e.g., /saveOrder → saveOrder, /personal/updateInfo → updateInfo
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

    # Create a synthetic MethodDef for this HTML file if not already present.
    # This allows the pipeline to resolve caller_id in CALLS and USES_ENDPOINT.
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
