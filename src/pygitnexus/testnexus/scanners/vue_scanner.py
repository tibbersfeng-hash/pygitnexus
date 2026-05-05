"""Vue SFC scanner: extracts pages, components, actions, and API calls."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import Action, APICall, Component, Page
from .base import BaseScanner, SourceFile


@dataclass
class ApiParam:
    """A single parameter of an API."""
    name: str
    param_type: str = "body"  # body / query / path
    value_type: str = "string"  # string / number / array
    required: bool = True
    sample_value: str = ""


class VueScanner(BaseScanner):
    """Scanner for Vue Single File Components (.vue)."""

    file_extensions = (".vue", ".ts", ".js")

    def extract_pages(self, files: list[SourceFile]) -> list[Page]:
        """Extract pages from router definitions and file-based conventions."""
        pages = []
        seen_ids: set[str] = set()

        # 1. Parse router definitions (Vue Router)
        for sf in files:
            if "router" not in sf.relative.lower():
                continue
            # Match route definitions: { path: '/xxx', component: Xxx }
            for m in re.finditer(r"path:\s*['\"]([^'\"]+)['\"]\s*,\s*(?:component|name)\s*:\s*(\w+)", sf.content):
                path, name = m.group(1), m.group(2)
                page_id = f"Page_{name}"
                if page_id not in seen_ids:
                    seen_ids.add(page_id)
                    pages.append(Page(
                        id=page_id,
                        name=name,
                        file_path=sf.relative,
                        url=path,
                        platform="vue",
                    ))

        # 2. File-based: files under src/views/ or src/pages/ are pages
        for sf in files:
            rel = sf.relative.replace("\\", "/")
            if "/views/" in rel or "/pages/" in rel:
                name = sf.path.stem
                page_id = f"Page_{name}"
                if page_id not in seen_ids:
                    seen_ids.add(page_id)
                    # Try to extract route from file content
                    url = f"/{name.lower()}"
                    url_match = re.search(r"path:\s*['\"]([^'\"]+)['\"]", sf.content)
                    if url_match:
                        url = url_match.group(1)
                    pages.append(Page(
                        id=page_id,
                        name=name,
                        file_path=sf.relative,
                        url=url,
                        platform="vue",
                    ))

        return pages

    def extract_components(self, files: list[SourceFile]) -> list[Component]:
        """Extract Vue components."""
        components = []
        seen_ids: set[str] = set()

        for sf in files:
            if not sf.relative.endswith(".vue"):
                continue
            name = sf.path.stem
            comp_id = f"Comp_{name}_{sf.relative.replace('/', '_').replace('.', '_')}"
            if comp_id in seen_ids:
                continue
            seen_ids.add(comp_id)

            # Determine type: page component vs reusable component
            rel = sf.relative.replace("\\", "/")
            comp_type = "page" if ("/views/" in rel or "/pages/" in rel) else "component"

            components.append(Component(
                id=comp_id,
                name=name,
                file_path=sf.relative,
                type=comp_type,
                platform="vue",
            ))

        return components

    def extract_actions(self, files: list[SourceFile]) -> list[Action]:
        """Extract user actions and API call sites from Vue components."""
        actions = []
        seen_ids: set[str] = set()

        for sf in files:
            if not sf.relative.endswith(".vue"):
                continue
            content = sf.content
            comp_name = sf.path.stem

            # 1. API calls: axios.get/post/put/delete, fetch()
            for m in re.finditer(
                r'(?:axios|api)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
                content,
                re.IGNORECASE,
            ):
                method = m.group(1).upper()
                api_path = m.group(2)
                line = content[:m.start()].count('\n') + 1
                action_name = f"{method.lower()}_{_path_to_name(api_path)}"
                action_id = f"Action_{comp_name}_{action_name}_{line}"
                if action_id not in seen_ids:
                    seen_ids.add(action_id)
                    actions.append(Action(
                        id=action_id,
                        name=action_name,
                        type="fetch",
                        component_name=comp_name,
                        http_method=method,
                        api_path=api_path,
                        line_number=line,
                    ))

            # 2. Click handlers in template: @click="xxx"
            for m in re.finditer(r'@(?:click|submit)\s*=\s*["\'](\w+)["\']', content):
                handler = m.group(1)
                line = content[:m.start()].count('\n') + 1
                action_id = f"Action_{comp_name}_{handler}_{line}"
                if action_id not in seen_ids:
                    seen_ids.add(action_id)
                    action_type = "submit" if "submit" in m.group(0) else "click"
                    actions.append(Action(
                        id=action_id,
                        name=handler,
                        type=action_type,
                        component_name=comp_name,
                        line_number=line,
                    ))

            # 3. Navigation: router.push / $router.push
            for m in re.finditer(r'(?:router|\$router)\.push\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content):
                target = m.group(1)
                line = content[:m.start()].count('\n') + 1
                action_name = f"navigate_to_{_path_to_name(target)}"
                action_id = f"Action_{comp_name}_{action_name}_{line}"
                if action_id not in seen_ids:
                    seen_ids.add(action_id)
                    actions.append(Action(
                        id=action_id,
                        name=action_name,
                        type="navigate",
                        component_name=comp_name,
                        line_number=line,
                    ))

        return actions

    def get_api_calls(self, files: list[SourceFile]) -> list[APICall]:
        """Extract raw API call sites from Vue code."""
        calls = []

        for sf in files:
            if not sf.relative.endswith((".vue", ".ts", ".js")):
                continue
            content = sf.content
            comp_name = sf.path.stem

            # Pattern 1: axios/api.this.get/post/put/delete/patch (direct axios)
            for m in re.finditer(
                r'(?:(await\s+)?(?:axios|api|this\.api)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"])',
                content,
                re.IGNORECASE,
            ):
                has_await = m.group(1) is not None
                method = m.group(2).upper()
                api_path = m.group(3)
                line = content[:m.start()].count('\n') + 1

                receiver = "axios"
                receiver_match = re.search(r'(axios|api|this\.\w+)\.' + method.lower(), content[max(0, m.start()-50):m.start()])
                if receiver_match:
                    receiver = receiver_match.group(1)

                calls.append(APICall(
                    http_method=method,
                    api_path=api_path,
                    line_number=line,
                    file_path=sf.relative,
                    component=comp_name,
                    receiver=receiver,
                    has_await=has_await,
                ))

            # Pattern 2: http.request("METHOD", "/path", ...) — custom http wrapper
            for m in re.finditer(
                r'http\.request\s*<[^>]*>\s*\(\s*["\'](\w+)["\']\s*,\s*["\']([^"\']+)["\']',
                content,
            ):
                method = m.group(1).upper()
                api_path = m.group(2)
                line = content[:m.start()].count('\n') + 1
                calls.append(APICall(
                    http_method=method,
                    api_path=api_path,
                    line_number=line,
                    file_path=sf.relative,
                    component=comp_name,
                    receiver="http",
                    has_await=False,
                ))

            # Pattern 3: http.get/post/put/delete("/path", ...) — shorthand methods
            for m in re.finditer(
                r'http\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
                content,
                re.IGNORECASE,
            ):
                method = m.group(1).upper()
                api_path = m.group(2)
                line = content[:m.start()].count('\n') + 1
                calls.append(APICall(
                    http_method=method,
                    api_path=api_path,
                    line_number=line,
                    file_path=sf.relative,
                    component=comp_name,
                    receiver="http",
                    has_await=False,
                ))

        return calls


    # ------------------------------------------------------------------
    # API parameter extraction
    # ------------------------------------------------------------------

    def extract_api_params(self, files: list[SourceFile]) -> list[dict]:
        """Extract API parameter definitions from service files and Vue components.

        Returns list of dicts:
        [{
            "http_method": "POST",
            "api_path": "/address",
            "params": [ApiParam, ...],
            "path_params": ["id"],
            "source_file": "src/service/address.js",
        }]
        """
        api_map: dict[tuple[str, str], dict] = {}

        # Build service function → (method, path) map from service files
        service_funcs: dict[str, tuple[str, str]] = {}
        for sf in files:
            rel = sf.relative.replace("\\", "/")
            if rel.startswith("src/service/") or "service/" in rel:
                self._parse_service_funcs(sf, service_funcs)

        # 1. Parse service/*.js files — axios calls
        for sf in files:
            rel = sf.relative.replace("\\", "/")
            if rel.startswith("src/service/") or "service/" in rel:
                self._parse_service_file(sf, api_map)

        # 2. Parse Vue components — service call usage + direct axios
        for sf in files:
            if not sf.relative.endswith(".vue"):
                continue
            self._parse_vue_calls(sf, api_map, service_funcs)

        # Deduplicate and finalize
        result = []
        for (method, path), info in api_map.items():
            params = list(info.get("params", {}).values())
            path_params = list(info.get("path_params", set()))
            result.append({
                "http_method": method,
                "api_path": path,
                "params": params,
                "path_params": path_params,
                "source_file": info.get("source_file", ""),
            })

        return sorted(result, key=lambda x: (x["http_method"], x["api_path"]))

    def _parse_service_funcs(self, sf: SourceFile, service_funcs: dict[str, tuple[str, str]]) -> None:
        """Parse service file to map exported function names to (method, path)."""
        content = sf.content
        # Pattern: export function name(params) { return axios.post('/path', ...) }
        for m in re.finditer(
            r'export\s+function\s+(\w+)\s*\([^)]*\)\s*\{[^}]*?'
            r'axios\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
            content, re.IGNORECASE | re.DOTALL,
        ):
            func_name = m.group(1)
            method = m.group(2).upper()
            path = m.group(3)
            service_funcs[func_name] = (method, path)

    def _parse_service_file(self, sf: SourceFile, api_map: dict) -> None:
        """Parse a service JS file to extract API function definitions.

        Patterns:
        1. axios.post('/path', params) — variable ref → look at Vue usage later
        2. axios.post('/path', { key: val }) — inline body
        3. axios.get('/path', { params }) — query param shorthand
        4. axios.get('/path', { params: { key: val } }) — inline query
        """
        content = sf.content
        rel = sf.relative.replace("\\", "/")

        for m in re.finditer(
            r'(?:axios|api|this\.\w+)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]'
            r'(?:\s*,\s*(\{[^}]*\}|\w+))?',
            content, re.IGNORECASE,
        ):
            method = m.group(1).upper()
            path = m.group(2)
            arg = m.group(3)

            key = (method, path)
            if key not in api_map:
                api_map[key] = {
                    "params": {},
                    "path_params": set(),
                    "source_file": rel,
                }

            # Extract path params from template literals: /address/${id}
            for pm in re.finditer(r'\$\{(\w+)\}', path):
                api_map[key]["path_params"].add(pm.group(1))

            if not arg:
                continue

            if method == "GET":
                # GET with inline query params: { params: { key: val } } or { key: val } (shorthand)
                if "params" in arg:
                    # { params: { key1, key2 } } or { params: { key1: val1 } }
                    inner = re.search(r'params\s*:\s*\{([^}]+)\}', arg)
                    if inner:
                        self._extract_keys(inner.group(1), "query", api_map[key])
                else:
                    # Direct shorthand: { key1: val1, key2: val2 } — treat as query
                    self._extract_keys(arg, "query", api_map[key])

            elif method in ("POST", "PUT", "PATCH"):
                if arg.startswith("{"):
                    # Inline body object
                    self._extract_keys(arg, "body", api_map[key])
                # If arg is a variable name (e.g., "params"), skip — Vue usage will fill it

    def _parse_vue_calls(self, sf: SourceFile, api_map: dict, service_funcs: dict[str, tuple[str, str]]) -> None:
        """Parse Vue components for actual service call usage.

        Two patterns:
        1. Direct axios: axios.post('/path', { key: val })
        2. Service call: await serviceFunc({ key: val }) or await serviceFunc(params_var)
        """
        content = sf.content

        # Pre-extract named variable assignments that look like API bodies
        named_objects: dict[str, str] = {}
        for m in re.finditer(r'(?:const|let|var)\s+(\w+)\s*=\s*(\{[^}]+\})', content):
            named_objects[m.group(1)] = m.group(2)

        # Pattern 1: Direct axios/api calls in Vue
        for m in re.finditer(
            r'(?:axios|api|http)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]'
            r'(?:\s*,\s*(\{[^}]*\}))?',
            content, re.IGNORECASE,
        ):
            method = m.group(1).upper()
            path = m.group(2)
            arg = m.group(3)

            key = (method, path)
            if key not in api_map:
                api_map[key] = {
                    "params": {},
                    "path_params": set(),
                    "source_file": sf.relative,
                }

            # Path params
            for pm in re.finditer(r'\$\{(\w+)\}', path):
                api_map[key]["path_params"].add(pm.group(1))

            if arg:
                if method == "GET":
                    if "params" in arg:
                        inner = re.search(r'params\s*:\s*(\{[^}]+\})', arg)
                        if inner:
                            self._extract_keys(inner.group(1), "query", api_map[key])
                    else:
                        self._extract_keys(arg, "query", api_map[key])
                elif method in ("POST", "PUT", "PATCH"):
                    self._extract_keys(arg, "body", api_map[key])

        # Pattern 2: Service function calls — serviceFunc(obj_or_var)
        # 2a. Direct: await serviceFunc({...}) or await serviceFunc(var)
        for m in re.finditer(r'await\s+(\w+)\s*\(\s*(\{[^}]+\}|\w+)\s*\)', content):
            func_name = m.group(1)
            arg = m.group(2)
            if func_name not in service_funcs:
                continue
            self._add_vue_param(func_name, arg, named_objects, service_funcs, api_map, sf.relative)

        # 2b. Ternary: await cond ? fn1(var) : fn2(var)
        for m in re.finditer(r'await\s+[^?]*\?\s*(\w+)\s*\(\s*(\{[^}]+\}|\w+)\s*\)', content):
            func_name = m.group(1)
            arg = m.group(2)
            if func_name not in service_funcs:
                continue
            self._add_vue_param(func_name, arg, named_objects, service_funcs, api_map, sf.relative)
        # After ternary ?, also check the else branch
        for m in re.finditer(r':\s*(\w+)\s*\(\s*(\{[^}]+\}|\w+)\s*\)', content):
            func_name = m.group(1)
            arg = m.group(2)
            if func_name not in service_funcs:
                continue
            self._add_vue_param(func_name, arg, named_objects, service_funcs, api_map, sf.relative)

    def _add_vue_param(self, func_name, arg, named_objects, service_funcs, api_map, rel):
        """Helper to add a parameter from a Vue service call."""
        method, path = service_funcs[func_name]
        key = (method, path)
        if key not in api_map:
            api_map[key] = {"params": {}, "path_params": set(), "source_file": rel}
        ptype = "body" if method in ("POST", "PUT", "PATCH") else "query"
        if arg.startswith("{"):
            self._extract_keys(arg, ptype, api_map[key])
        elif arg in named_objects:
            self._extract_keys(named_objects[arg], ptype, api_map[key])

    def _extract_keys(self, obj_str: str, param_type: str, api_info: dict) -> None:
        """Extract parameter keys from a JS object literal string.

        Handles both quoted and unquoted keys:
        - "loginName": value  → loginName
        - loginName: value    → loginName
        """
        inner = obj_str.strip(" {}")
        # Match both quoted and unquoted keys
        for m in re.finditer(r'(?:["\'](\w+)["\']|(\w+))\s*:', inner):
            key = m.group(1) or m.group(2)
            if key and key not in ("params", "headers", "timeout"):
                if key not in api_info["params"]:
                    api_info["params"][key] = ApiParam(
                        name=key, param_type=param_type,
                    )



def _path_to_name(path: str) -> str:
    """Convert API path to a readable name."""
    clean = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return clean if clean else "root"
