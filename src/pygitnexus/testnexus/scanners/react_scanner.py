"""React TSX/JSX scanner: extracts pages, components, actions, and API calls."""

from __future__ import annotations

import re

from ..core.models import Action, APICall, Component, Page
from .base import BaseScanner, SourceFile


class ReactScanner(BaseScanner):
    """Scanner for React TSX/JSX/TS files."""

    file_extensions = (".tsx", ".jsx", ".ts", ".js")

    def extract_pages(self, files: list[SourceFile]) -> list[Page]:
        """Extract pages from React router and file conventions."""
        pages = []
        seen_ids: set[str] = set()

        # 1. Parse route definitions (React Router)
        for sf in files:
            if "router" not in sf.relative.lower() and "route" not in sf.relative.lower():
                if not any(kw in sf.content for kw in ["Route", "createBrowserRouter", "createHashRouter"]):
                    continue
            # Match <Route path="/xxx" element={<Xxx />} />
            for m in re.finditer(
                r'<Route\s+path="([^"]+)"\s+(?:element=\{<(\w+)|component=\{(\w+))',
                sf.content,
            ):
                path = m.group(1)
                name = m.group(2) or m.group(3) or "Unknown"
                page_id = f"Page_{name}"
                if page_id not in seen_ids:
                    seen_ids.add(page_id)
                    pages.append(Page(
                        id=page_id,
                        name=name,
                        file_path=sf.relative,
                        url=path,
                        platform="react",
                    ))

            # Match createBrowserRouter routes: { path: '/xxx', element: <Xxx /> }
            for m in re.finditer(
                r"path:\s*['\"]([^'\"]+)['\"]\s*,\s*element:\s*<(\w+)",
                sf.content,
            ):
                path, name = m.group(1), m.group(2)
                page_id = f"Page_{name}"
                if page_id not in seen_ids:
                    seen_ids.add(page_id)
                    pages.append(Page(
                        id=page_id,
                        name=name,
                        file_path=sf.relative,
                        url=path,
                        platform="react",
                    ))

        # 2. File-based: files under src/pages/ or src/routes/ or src/views/
        for sf in files:
            rel = sf.relative.replace("\\", "/")
            if "/pages/" in rel or "/routes/" in rel or "/views/" in rel:
                name = sf.path.stem
                # Skip index files that are just re-exports
                if name.lower() in ("index",) and name == sf.path.stem:
                    continue
                page_id = f"Page_{name}"
                if page_id not in seen_ids:
                    seen_ids.add(page_id)
                    pages.append(Page(
                        id=page_id,
                        name=name,
                        file_path=sf.relative,
                        url=f"/{name.lower()}",
                        platform="react",
                    ))

        return pages

    def extract_components(self, files: list[SourceFile]) -> list[Component]:
        """Extract React components and JS modules."""
        components = []
        seen_ids: set[str] = set()

        for sf in files:
            if not sf.relative.endswith((".tsx", ".jsx", ".js", ".ts")):
                continue
            name = sf.path.stem
            rel = sf.relative.replace("\\", "/")
            comp_type = "page" if any(d in rel for d in ["/pages/", "/routes/", "/views/"]) else "component"

            comp_id = f"Comp_{name}_{sf.relative.replace('/', '_').replace('.', '_')}"
            if comp_id in seen_ids:
                continue
            seen_ids.add(comp_id)

            components.append(Component(
                id=comp_id,
                name=name,
                file_path=sf.relative,
                type=comp_type,
                platform="react",
            ))

        return components

    def extract_actions(self, files: list[SourceFile]) -> list[Action]:
        """Extract user actions and API calls from React components."""
        actions = []
        seen_ids: set[str] = set()

        for sf in files:
            if not sf.relative.endswith((".tsx", ".jsx", ".ts", ".js")):
                continue
            content = sf.content
            comp_name = sf.path.stem

            # 1. API calls: fetch(), axios, useQuery, useMutation
            # fetch('/api/xxx', { method: 'POST' })
            for m in re.finditer(
                r'fetch\s*\(\s*[`\'"]([^`\'"]+)[`\'"](?:\s*,\s*\{[^}]*method:\s*[`\'"](\w+)[`\'"])?',
                content,
                re.IGNORECASE,
            ):
                api_path = m.group(1)
                method = (m.group(2) or "GET").upper()
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

            # fetchAPI('/api/xxx') - common wrapper pattern
            for m in re.finditer(
                r'(?:fetchAPI|apiCall|request)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
                content,
                re.IGNORECASE,
            ):
                api_path = m.group(1)
                line = content[:m.start()].count('\n') + 1
                action_name = f"fetch_{_path_to_name(api_path)}"
                action_id = f"Action_{comp_name}_{action_name}_{line}"
                if action_id not in seen_ids:
                    seen_ids.add(action_id)
                    actions.append(Action(
                        id=action_id,
                        name=action_name,
                        type="fetch",
                        component_name=comp_name,
                        http_method="GET",
                        api_path=api_path,
                        line_number=line,
                    ))

            # axios.get/post/put/delete
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

            # useQuery / useMutation
            for m in re.finditer(
                r'use(?:Query|Mutation)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
                content,
            ):
                api_path = m.group(1)
                line = content[:m.start()].count('\n') + 1
                hook = "query" if "useQuery" in content[max(0, m.start()-20):m.start()] else "mutation"
                method = "GET" if hook == "query" else "POST"
                action_name = f"{hook}_{_path_to_name(api_path)}"
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

            # 2. Event handlers: onClick={xxx}, onSubmit={xxx}
            for m in re.finditer(r'on(?:Click|Submit|Change|KeyDown|Focus|Blur)=\{(\w+)\}', content):
                handler = m.group(1)
                line = content[:m.start()].count('\n') + 1
                action_id = f"Action_{comp_name}_{handler}_{line}"
                if action_id not in seen_ids:
                    seen_ids.add(action_id)
                    event_type = "submit" if "Submit" in m.group(0) else "click"
                    actions.append(Action(
                        id=action_id,
                        name=handler,
                        type=event_type,
                        component_name=comp_name,
                        line_number=line,
                    ))

            # 3. Navigation: navigate('/xxx'), useNavigate
            for m in re.finditer(r'navigate\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content):
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
        """Extract raw API call sites from React code."""
        calls = []

        for sf in files:
            if not sf.relative.endswith((".tsx", ".jsx", ".ts", ".js")):
                continue
            content = sf.content
            comp_name = sf.path.stem

            # fetch calls
            for m in re.finditer(
                r'(?:await\s+)?fetch\s*\(\s*[`\'"]([^`\'"]+)[`\'"](?:\s*,\s*\{[^}]*method:\s*[`\'"](\w+)[`\'"])?',
                content,
                re.IGNORECASE,
            ):
                api_path = m.group(1)
                method = (m.group(2) or "GET").upper()
                line = content[:m.start()].count('\n') + 1
                has_await = "await" in content[max(0, m.start()-10):m.start()]
                calls.append(APICall(
                    http_method=method,
                    api_path=api_path,
                    line_number=line,
                    file_path=sf.relative,
                    component=comp_name,
                    receiver="fetch",
                    has_await=has_await,
                ))

            # axios calls
            for m in re.finditer(
                r'(?:await\s+)?(?:axios|api)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
                content,
                re.IGNORECASE,
            ):
                method = m.group(1).upper()
                api_path = m.group(2)
                line = content[:m.start()].count('\n') + 1
                has_await = "await" in content[max(0, m.start()-10):m.start()]
                calls.append(APICall(
                    http_method=method,
                    api_path=api_path,
                    line_number=line,
                    file_path=sf.relative,
                    component=comp_name,
                    receiver="axios",
                    has_await=has_await,
                ))

            # fetchAPI wrapper calls: fetchAPI('/api/xxx')
            for m in re.finditer(
                r'(?:await\s+)?(?:fetchAPI|apiCall|request)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
                content,
                re.IGNORECASE,
            ):
                api_path = m.group(1)
                line = content[:m.start()].count('\n') + 1
                has_await = "await" in content[max(0, m.start()-10):m.start()]
                calls.append(APICall(
                    http_method="GET",
                    api_path=api_path,
                    line_number=line,
                    file_path=sf.relative,
                    component=comp_name,
                    receiver="fetchAPI",
                    has_await=has_await,
                ))

        return calls


def _path_to_name(path: str) -> str:
    """Convert API path to a readable name."""
    clean = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return clean if clean else "root"
