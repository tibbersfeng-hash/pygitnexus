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

# JavaScript built-in methods and common library functions that should NOT
# become standalone operations — they are internal to handler functions.
BUILTIN_JS_NAMES = frozenset({
    # Array methods
    "map", "filter", "forEach", "reduce", "find", "findIndex", "some", "every",
    "flatMap", "concat", "slice", "splice", "push", "pop", "shift", "unshift",
    "sort", "reverse", "join", "includes", "indexOf", "lastIndexOf", "entries",
    "keys", "values", "copyWithin", "fill", "flat",
    # String methods
    "toLowerCase", "toUpperCase", "trim", "split", "replace", "match",
    "substring", "substr", "charAt", "startsWith", "endsWith",
    "padStart", "padEnd", "repeat",
    # Object methods
    "assign", "keys", "values", "entries", "freeze", "seal",
    # Number / Math
    "parseInt", "parseFloat", "floor", "ceil", "round", "abs", "max", "min",
    # Timing
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    # JSON
    "stringify", "parse",
    # Promise / async
    "then", "catch", "finally",
    # DOM / browser
    "getElementById", "querySelector", "querySelectorAll", "addEventListener",
    "preventDefault", "stopPropagation",
    # Console
    "log", "warn", "error", "info", "debug",
    # Common crypto / hash (often imported as bare names)
    "md5", "sha1", "sha256", "hash",
})


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
    """Extract operation set from a single parsed frontend file.

    Strategy:
    1. Template event bindings (caller_method == file_name) are entry-point handlers
    2. Script-body calls (caller_method == handler_name) are internal to each handler
    3. API calls inside handler bodies are associated with the handler operation
    """
    page_name = Path(parsed.file_path).stem
    page_route = _infer_route(parsed.file_path, page_name)
    file_name = Path(parsed.file_path).name  # e.g. "Login.vue"

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

    # ── Step 1: Identify template-bound event handlers ──
    # These are calls where caller_method == file_name (e.g., "Login.vue").
    # They represent @click, @submit, etc. in the template.
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

    # Collect service function imports (from @/service/ or /api/ paths)
    # These are API wrapper functions that should be recognized as API calls.
    service_functions: set[str] = set()
    for imp in parsed.imports:
        qual = imp.qualified_name.lower()
        if "/service/" in qual or "/api/" in qual:
            # The imported name is the last part of the qualified name
            name = imp.qualified_name.rsplit(".", 1)[-1]
            service_functions.add(name)

    # Map: handler_name -> {event, op_type, element, line}
    handler_info: dict[str, dict] = {}

    for call in parsed.calls:
        if call.caller_method != file_name:
            continue
        handler = (call.target_name or "").split(".")[0]
        if not handler or handler in framework_names or handler in BUILTIN_JS_NAMES:
            continue

        # Skip v-model data bindings (e.g., "state.verify") — these are not event handlers
        if "." in (call.target_name or ""):
            continue

        # Skip component references (e.g., "Login.vue" → component)
        if handler.endswith(".vue"):
            op_set.components.append(handler)
            continue

        # Determine operation type and element from the event context
        op_type, event_attr = _infer_op_type_from_context(call)
        element = _infer_element_from_context(handler, parsed)

        if handler not in handler_info:
            # If the call itself has HTTP info, use it as an API call for this handler
            initial_api: list[dict] = []
            if call.http_method and call.http_path:
                initial_api.append({"method": call.http_method, "path": call.http_path})
            handler_info[handler] = {
                "event": event_attr,
                "op_type": op_type,
                "element": element,
                "line": call.line,
                "initial_api": initial_api,
            }
        else:
            # Merge API calls if multiple calls map to same handler
            if call.http_method and call.http_path:
                existing_apis = handler_info[handler].get("initial_api", [])
                new_api = {"method": call.http_method, "path": call.http_path}
                if new_api not in existing_apis:
                    existing_apis.append(new_api)

    # ── Step 2: For each handler, find API calls inside its body ──
    op_counter = [0]

    for handler_name, info in handler_info.items():
        # Find all calls where caller_method == handler_name (script body calls)
        body_calls = [
            c for c in parsed.calls
            if c.caller_method == handler_name
            # Also include calls where caller_method is a method that this handler calls
            # (transitive — we handle 1-level depth)
        ]

        # Also gather transitive calls: if handler A calls function B,
        # include calls where caller_method == B
        transitive_targets: set[str] = {handler_name}
        for c in parsed.calls:
            if c.caller_method == handler_name:
                target = (c.target_name or "").split(".")[0]
                if target and target not in framework_names and target not in BUILTIN_JS_NAMES:
                    transitive_targets.add(target)

        # Expand body_calls to include transitive
        all_body_calls = [
            c for c in parsed.calls
            if c.caller_method in transitive_targets
        ]

        # Extract API calls from body
        api_calls = []
        seen_api: set[tuple[str, str]] = set()

        # Include initial API calls from the template binding itself (e.g., form action)
        for api in info.get("initial_api", []):
            key = (api["method"], api["path"])
            if key not in seen_api:
                seen_api.add(key)
                api_calls.append(api)

        for c in all_body_calls:
            # Direct HTTP calls (fetch, axios, etc.)
            if c.http_method and c.http_path:
                key = (c.http_method, c.http_path)
                if key not in seen_api:
                    seen_api.add(key)
                    api_calls.append({
                        "method": c.http_method,
                        "path": c.http_path,
                    })
            # Indirect API calls: functions imported from service/api modules
            elif c.target_name and c.target_name in service_functions:
                # Infer method from function name patterns
                method = "POST"  # default for service calls
                key = ("SERVICE", c.target_name)
                if key not in seen_api:
                    seen_api.add(key)
                    api_calls.append({
                        "method": method,
                        "path": f"service:{c.target_name}",
                    })

        # Build the operation
        op = _build_enhanced_operation(
            handler_name=handler_name,
            info=info,
            api_calls=api_calls,
            page_route=page_route,
            parsed=parsed,
            counter=op_counter,
        )
        if op:
            op_set.operations.append(op)

    # Also extract navigation operations from script content
    _extract_navigation_ops(parsed, op_set, op_counter, page_route)

    # Collect unique API endpoints from all operations
    seen_apis: set[tuple[str, str]] = set()
    for op in op_set.operations:
        for api in op.api_calls:
            key = (api["method"], api["path"])
            if key not in seen_apis:
                seen_apis.add(key)
                op_set.all_api_endpoints.append(api)

    return op_set


def _infer_op_type_from_context(call: CallSite) -> tuple[str, str]:
    """Infer operation type and event attribute from call context."""
    # Check if there's any HTTP method info on the call itself
    if call.http_method:
        if call.http_method in ("POST", "PUT", "DELETE", "PATCH"):
            return "submit", "@submit"
        else:
            return "click", "@click"

    # Default: click event
    return "click", "@click"


def _build_enhanced_operation(
    handler_name: str,
    info: dict,
    api_calls: list[dict],
    page_route: str,
    parsed: ParsedFile,
    counter: list[int],
) -> PageOperation | None:
    """Build a PageOperation from handler info and body API calls."""
    counter[0] += 1
    op_id = f"op_{counter[0]:02d}_{handler_name}"

    op_type = info["op_type"]
    event = info["event"]
    element = info["element"]
    line = info["line"]

    # If there are API calls, refine the operation type
    if api_calls and op_type == "click":
        has_post = any(c["method"] in ("POST", "PUT", "DELETE", "PATCH") for c in api_calls)
        if has_post:
            op_type = "submit"
            event = "@submit"

    # Check if handler does navigation (router.push, location.href, etc.)
    body_calls = [c for c in parsed.calls if c.caller_method == handler_name]
    for c in body_calls:
        target = (c.target_name or "").lower()
        if "push" in target or "replace" in target or "location" in target:
            # If this handler navigates and has no API calls, mark as navigation
            if not api_calls:
                nav_path = c.http_path or ""
                # Try to extract path from target_name patterns
                op = PageOperation(
                    op_id=op_id,
                    op_type="navigation",
                    element=element,
                    event="navigation",
                    handler=handler_name,
                    handler_line=line,
                    fields=[],
                    api_calls=[],
                    navigation_target=nav_path,
                    description=f"{handler_name} ({event}) → navigate to {nav_path or 'another page'}",
                    file_path=parsed.file_path,
                    line=line,
                )
                return op

    # Build description
    api_desc = ", ".join(f"{c['method']} {c['path']}" for c in api_calls) if api_calls else "no API call"
    description = f"{handler_name} ({event}) → {api_desc}"

    return PageOperation(
        op_id=op_id,
        op_type=op_type,
        element=element,
        event=event,
        handler=handler_name,
        handler_line=line,
        fields=[],
        api_calls=api_calls,
        navigation_target="",
        description=description,
        file_path=parsed.file_path,
        line=line,
    )


def _infer_element_from_context(handler_name: str, parsed: ParsedFile) -> str:
    """Infer the element type from handler body and name patterns."""
    body_calls = [c for c in parsed.calls if c.caller_method == handler_name]

    # Check for API call patterns
    for c in body_calls:
        if c.http_method == "POST":
            return "form/button[type=submit]"
        if c.http_method == "GET":
            return "link/a"

    # Check name patterns
    lower = handler_name.lower()
    if "submit" in lower or "save" in lower or "login" in lower or "register" in lower:
        return "form/button[type=submit]"
    if "nav" in lower or "go" in lower or "back" in lower or "to" in lower:
        return "link/a"
    if "toggle" in lower or "switch" in lower:
        return "button/switch"
    if "delete" in lower or "remove" in lower:
        return "button.danger"
    if "edit" in lower or "update" in lower:
        return "button.edit"

    return "button"


def _extract_navigation_ops(
    parsed: ParsedFile,
    op_set: PageOperationSet,
    counter: list[int],
    page_route: str,
) -> None:
    """Extract navigation operations (router.push, window.location, etc.) that are
    not already captured as template-bound handlers."""
    existing_handlers = {op.handler for op in op_set.operations}

    for call in parsed.calls:
        # Skip calls that are already captured as template-bound handlers
        if call.caller_method in existing_handlers:
            continue
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

            # Skip JS built-ins and framework internals
            if handler_name in BUILTIN_JS_NAMES:
                continue

            parsed.calls.append(CallSite(
                caller_method=file_name,
                caller_class="",
                target_name=handler_name,
                line=line,
                receiver=None,
                receiver_type=None,
            ))
