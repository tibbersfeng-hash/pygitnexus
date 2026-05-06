"""Extract page operation sets from parsed frontend files.

Analyzes parsed Vue/HTML files to build structured operation sets that capture:
- Interactive elements (buttons, form fields, links)
- Event handlers and their target functions
- Form field types and constraints
- API calls triggered by each operation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import CallSite, MethodDef, ParsedFile


@dataclass
class PageField:
    """A form field or input element on a page."""
    field_type: str        # "text", "password", "email", "select", "textarea", "checkbox", "radio", "file", "hidden"
    field_name: str        # name attribute or v-model binding
    label: str             # Display label
    placeholder: str       # Placeholder text
    required: bool         # Whether field is required
    options: list[str]     # For select/radio: list of option labels
    line: int              # Source line
    selector: str          # CSS-like selector for targeting


@dataclass
class PageOperation:
    """A single interactive operation on a page."""
    op_id: str             # Unique ID, e.g. "op_01_onSubmit"
    op_type: str           # "click" | "submit" | "input" | "change" | "hover" | "keypress" | "navigation"
    element: str           # Element description, e.g. "van-button[type=submit]" or "div.link-register"
    event: str             # Event name, e.g. "@submit", "@click"
    handler: str           # Handler function name
    handler_line: int      # Handler definition line in source
    fields: list[PageField]  # Associated form fields (for submit/input operations)
    api_calls: list[dict]  # API calls triggered by this operation: [{method, path}]
    navigation_target: str # For navigation ops: target route/URL
    description: str       # Human-readable description
    file_path: str         # Source file path
    line: int              # Source line of the element


@dataclass
class PageOperationSet:
    """Complete operation set for a single page."""
    page_file: str         # File path
    page_name: str         # Page name (derived from filename)
    page_route: str        # Inferred route, e.g. "/login"
    operations: list[PageOperation] = field(default_factory=list)
    components: list[str] = field(default_factory=list)  # Components used
    all_fields: list[PageField] = field(default_factory=list)  # All form fields on page
    all_api_endpoints: list[dict] = field(default_factory=list)  # All API endpoints referenced


# Event attribute patterns for Vue templates
VUE_EVENT_MAP = {
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
    "v-on:click": "click",
    "v-on:submit": "submit",
    "v-on:input": "input",
    "v-on:change": "change",
}

# Inline HTML event handlers
HTML_EVENT_MAP = {
    "onclick": "click",
    "onsubmit": "submit",
    "oninput": "input",
    "onchange": "change",
    "onkeydown": "keypress",
    "onkeyup": "keypress",
    "onmouseover": "hover",
}


def extract_all_operations(parse_results: list[ParsedFile]) -> list[PageOperationSet]:
    """Extract operation sets from all parsed frontend files."""
    op_sets: list[PageOperationSet] = []
    for parsed in parse_results:
        if not _is_frontend_file(parsed.file_path):
            continue
        op_set = _extract_from_parsed(parsed)
        op_sets.append(op_set)
    return op_sets


def _is_frontend_file(file_path: str) -> bool:
    """Check if a file is a frontend page file."""
    ext = Path(file_path).suffix.lower()
    return ext in (".vue", ".html", ".jsx", ".tsx")


def _extract_from_parsed(parsed: ParsedFile) -> PageOperationSet:
    """Extract operation set from a single parsed frontend file."""
    page_name = Path(parsed.file_path).stem
    page_route = _infer_route(parsed.file_path, page_name)

    op_set = PageOperationSet(
        page_file=parsed.file_path,
        page_name=page_name,
        page_route=page_route,
    )

    # Extract form fields from synthetic MethodDef entries
    for method in parsed.methods:
        if method.name.startswith("FormField("):
            field_info = _parse_form_field_method(method)
            if field_info:
                op_set.all_fields.append(field_info)

    # Also extract v-model bindings and form fields from source directly
    _extract_vue_fields_from_source(parsed, op_set)

    # Extract operations from CallSites and synthetic methods
    op_counter = [0]

    # Filter out framework internals before building operations
    framework_names = {
        "ref", "reactive", "computed", "watch", "watchEffect", "toRef", "toRefs",
        "onMounted", "onUnmounted", "onBeforeMount", "onUpdated", "onBeforeUpdate",
        "onActivated", "onDeactivated", "onBeforeUnmount", "onErrorCaptured",
        "defineProps", "defineEmits", "defineExpose", "defineComponent",
        "provide", "inject", "nextTick",
        "useRouter", "useRoute", "createRouter",
        "useCartStore", "useStore", "useAttrs", "useSlots",
        "$nextTick",
    }

    handler_map: dict[str, list[CallSite]] = {}
    for call in parsed.calls:
        if call.target_name and not call.target_name.startswith("/*"):
            target = call.target_name.split(".")[0]  # Get base name for filtering
            if target in framework_names:
                continue
            key = call.target_name or "unknown"
            handler_map.setdefault(key, []).append(call)

    for handler_name, calls in handler_map.items():
        op = _build_operation(handler_name, calls, parsed, page_route, op_counter)
        if op:
            op_set.operations.append(op)

    # Also extract navigation operations from script content
    _extract_navigation_ops(parsed, op_set, op_counter, page_route)

    # Collect unique API endpoints
    seen_apis: set[tuple[str, str]] = set()
    for call in parsed.calls:
        if call.http_method and call.http_path:
            key = (call.http_method, call.http_path)
            if key not in seen_apis:
                seen_apis.add(key)
                op_set.all_api_endpoints.append({
                    "method": call.http_method,
                    "path": call.http_path,
                })

    # Collect component references
    for call in parsed.calls:
        if call.target_name and call.target_name.endswith(".vue"):
            op_set.components.append(call.target_name)

    return op_set


def _build_operation(
    handler_name: str,
    calls: list[CallSite],
    parsed: ParsedFile,
    page_route: str,
    counter: list[int],
) -> PageOperation | None:
    """Build a PageOperation from a handler and its associated calls."""
    counter[0] += 1
    op_id = f"op_{counter[0]:02d}_{handler_name}"

    # Determine operation type
    op_type = "click"
    event = "@click"
    element = "button"
    line = calls[0].line if calls else 0

    for call in calls:
        if call.http_method:
            op_type = "submit" if call.http_method in ("POST", "PUT", "DELETE") else "click"
            event = "@submit" if call.http_method == "POST" else "@click"
            break

    # Determine element from handler context
    first_call = calls[0] if calls else None
    if first_call:
        # Try to find element info from the call's context
        element = _infer_element_from_call(first_call)

    # Collect API calls
    api_calls = []
    for call in calls:
        if call.http_method and call.http_path:
            api_calls.append({
                "method": call.http_method,
                "path": call.http_path,
            })

    # Find associated fields
    fields = []
    if op_type == "submit":
        # For submit operations, all form fields on the page are associated
        fields = []  # Will be populated separately

    # Build description
    api_desc = ", ".join(f"{c['method']} {c['path']}" for c in api_calls) if api_calls else "no API call"
    description = f"{handler_name} ({event}) → {api_desc}"

    return PageOperation(
        op_id=op_id,
        op_type=op_type,
        element=element,
        event=event,
        handler=handler_name,
        handler_line=first_call.line if first_call else 0,
        fields=fields,
        api_calls=api_calls,
        navigation_target="",
        description=description,
        file_path=parsed.file_path,
        line=line,
    )


def _extract_navigation_ops(
    parsed: ParsedFile,
    op_set: PageOperationSet,
    counter: list[int],
    page_route: str,
) -> None:
    """Extract navigation operations (router.push, window.location, etc.)."""
    for call in parsed.calls:
        target = call.target_name or ""
        if "location" in target.lower() or "router" in target.lower() or "push" in target.lower() or "replace" in target.lower():
            if call.http_path:
                counter[0] += 1
                op = PageOperation(
                    op_id=f"op_{counter[0]:02d}_navigate",
                    op_type="navigation",
                    element="link/router",
                    event="navigation",
                    handler=call.target_name or "navigate",
                    handler_line=call.line,
                    fields=[],
                    api_calls=[],
                    navigation_target=call.http_path,
                    description=f"Navigate to {call.http_path}",
                    file_path=parsed.file_path,
                    line=call.line,
                )
                op_set.operations.append(op)


def _parse_form_field_method(method: MethodDef) -> PageField | None:
    """Parse a synthetic FormField MethodDef into a PageField."""
    content = method.content
    if not content.startswith("FormField:"):
        return None

    # Parse: FormField: tag=input, type=text, name=username, v-model=username, ...
    parts = {}
    for kv in content.replace("FormField:", "").split(","):
        kv = kv.strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.strip()] = v.strip()

    name = parts.get("name", "") or parts.get("v-model", "")
    field_type = parts.get("type", "text")
    tag = parts.get("tag", "input")
    if tag == "select":
        field_type = "select"
    elif tag == "textarea":
        field_type = "textarea"

    placeholder = parts.get("placeholder", "")
    required = parts.get("required", "false").lower() == "true"
    label = parts.get("label", "")

    # Build selector
    if name:
        selector = f"{tag}[name='{name}']" if name else tag
    else:
        selector = tag

    return PageField(
        field_type=field_type,
        field_name=name,
        label=label,
        placeholder=placeholder,
        required=required,
        options=[],
        line=method.start_line,
        selector=selector,
    )


def _infer_element_from_call(call: CallSite) -> str:
    """Infer the element type from a CallSite."""
    if call.http_method:
        if call.http_method == "POST":
            return "form/button[type=submit]"
        elif call.http_method == "GET":
            return "link/a"
    return "button"


def _infer_route(file_path: str, page_name: str) -> str:
    """Infer the route from file path."""
    # Try to extract route from common patterns
    lower = file_path.lower()
    name = page_name.lower()

    if "login" in name:
        return "/login"
    if "home" in name:
        return "/home"
    if "cart" in name:
        return "/cart"
    if "order" in name:
        if "create" in name or "settle" in name:
            return "/create-order"
        return "/orders"
    if "user" in name:
        return "/user"
    if "product" in name:
        if "detail" in name:
            return "/product-detail"
        if "list" in name:
            return "/products"
    if "category" in name:
        return "/category"
    if "setting" in name:
        return "/settings"
    if "address" in name:
        if "edit" in name:
            return "/address-edit"
        return "/address"
    if "about" in name:
        return "/about"

    return f"/{name}"


# ---------------------------------------------------------------------------
# Raw template parsing for operation extraction (used before full pipeline)
# ---------------------------------------------------------------------------

def extract_operations_from_source(file_path: str, content: bytes) -> PageOperationSet:
    """Extract operation set directly from source file (standalone, no pipeline needed).

    This is the entry point for the autogen CLI command. It parses the file
    using the appropriate extractor, then builds the operation set.
    """
    from .extractor_vue import parse as parse_vue
    from .extractor_html import parse as parse_html

    ext = Path(file_path).suffix.lower()
    if ext == ".vue":
        parsed = parse_vue(file_path, content)
    elif ext == ".html":
        parsed = parse_html(file_path, content)
    else:
        parsed = ParsedFile(file_path=file_path)

    # Also do a regex pass on the raw source to find operations the AST might miss
    source = content.decode("utf-8", errors="replace")
    _extract_ops_regex_pass(source, file_path, parsed)

    return _extract_from_parsed(parsed)


def _extract_vue_fields_from_source(parsed: ParsedFile, op_set: PageOperationSet) -> None:
    """Extract Vue form fields (v-model, van-field, etc.) directly from source."""
    try:
        with open(parsed.file_path, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception:
        return

    # Match v-model bindings: v-model="state.fieldName" or v-model="fieldName"
    for m in re.finditer(r'v-model(?:\.\w+)*\s*=\s*"([^"]+)"', source):
        binding = m.group(1).strip()
        line = source[:m.start()].count("\n") + 1

        # Extract nearby label
        context_before = source[max(0, m.start() - 200):m.start()]
        label_match = re.search(r'label\s*=\s*"([^"]+)"', context_before)
        label = label_match.group(1) if label_match else ""

        # Extract type
        type_match = re.search(r'type\s*=\s*"([^"]+)"', context_before)
        field_type = type_match.group(1) if type_match else "text"

        # Extract placeholder
        placeholder_match = re.search(r'placeholder\s*=\s*"([^"]+)"', context_before)
        placeholder = placeholder_match.group(1) if placeholder_match else ""

        # Extract required
        required = "required" in context_before or ":rules" in context_before

        # Clean up binding name (remove state. prefix)
        clean_name = binding.split(".")[-1] if "." in binding else binding

        # Find element context (van-field, input, select, etc.)
        tag_context = source[max(0, m.start() - 300):m.start() + 50]
        element_type = "input"
        if "van-field" in tag_context:
            element_type = "input"
        elif "van-checkbox" in tag_context:
            element_type = "checkbox"
        elif "van-radio" in tag_context:
            element_type = "radio"
        elif "van-picker" in tag_context:
            element_type = "select"
        elif "van-cell" in tag_context:
            element_type = "input"
        elif "<select" in tag_context:
            element_type = "select"
        elif "<textarea" in tag_context:
            element_type = "textarea"

        selector = f"[v-model='{binding}']" if binding else f"{element_type}"

        op_set.all_fields.append(PageField(
            field_type=field_type,
            field_name=clean_name,
            label=label,
            placeholder=placeholder,
            required=required,
            options=[],
            line=line,
            selector=selector,
        ))

    # Also extract standard HTML form fields
    for m in re.finditer(r'<(input|select|textarea)\b([^>]*)>', source, re.DOTALL):
        tag = m.group(1)
        attrs_str = m.group(2)
        line = source[:m.start()].count("\n") + 1

        name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', attrs_str)
        type_match = re.search(r'type\s*=\s*["\']([^"\']+)["\']', attrs_str)
        id_match = re.search(r'id\s*=\s*["\']([^"\']+)["\']', attrs_str)
        placeholder_match = re.search(r'placeholder\s*=\s*["\']([^"\']+)["\']', attrs_str)

        field_type = type_match.group(1) if type_match else ("text" if tag != "select" and tag != "textarea" else tag)
        field_name = name_match.group(1) if name_match else (id_match.group(1) if id_match else "")
        placeholder = placeholder_match.group(1) if placeholder_match else ""
        required = "required" in attrs_str
        selector = f"{tag}[name='{field_name}']" if field_name else tag

        # Avoid duplicate with v-model extraction
        if not any(f.field_name == field_name for f in op_set.all_fields if field_name):
            op_set.all_fields.append(PageField(
                field_type=field_type,
                field_name=field_name,
                label="",
                placeholder=placeholder,
                required=required,
                options=[],
                line=line,
                selector=selector,
            ))


def _extract_ops_regex_pass(source: str, file_path: str, parsed: ParsedFile) -> None:
    """Regex-based operation extraction supplement for Vue/HTML files."""
    file_name = Path(file_path).name

    # Extract event bindings from Vue/HTML source
    event_patterns = [
        # Vue: @event="handler" or @event.modifier="handler"
        (r'@(click|submit|input|change|focus|blur|keydown|keyup|mouseover|mouseout|mouseenter|mouseleave)(?:\.\w+)*\s*=\s*"([^"]+)"', "vue"),
        # Vue: v-on:event="handler"
        (r'v-on:(click|submit|input|change|focus|blur|keydown|keyup|mouseover|mouseout|mouseenter|mouseleave)\s*=\s*"([^"]+)"', "vue"),
        # HTML: onevent="handler"
        (r'\bon(click|submit|input|change|focus|blur|keydown|keyup|mouseover|mouseout)\s*=\s*"([^"]+)"', "html"),
    ]

    for pattern, source_type in event_patterns:
        for match in re.finditer(pattern, source):
            event_type = match.group(1)
            handler = match.group(2).strip()
            line = source[:match.start()].count("\n") + 1

            # Extract handler name from expression
            fn_match = re.search(r'\b([a-zA-Z_$][\w$]*)\s*\(', handler)
            handler_name = fn_match.group(1) if fn_match else handler

            parsed.calls.append(CallSite(
                caller_method=file_name,
                caller_class="",
                target_name=handler_name,
                line=line,
                receiver=None,
                receiver_type=None,
            ))
