"""Business module inference from frontend/backend code analysis."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path


def infer_modules(
    frontend_path: str | Path | None = None,
    pages: list | None = None,
    components: list | None = None,
    api_calls: list | None = None,
    backend_endpoints: list[tuple[str, str, str, str, str]] | None = None,
) -> tuple[dict[str, dict], dict[str, list]]:
    """Infer business modules from frontend and backend code.

    Returns:
        modules: {module_name: {description, page_count, endpoint_count, ...}}
        affiliations: {module_name: [entity_ids]}
    """
    modules: dict[str, dict] = {}
    affiliations: dict[str, list] = defaultdict(list)

    # --- Frontend module inference ---
    if frontend_path and pages:
        _infer_frontend_modules(frontend_path, pages, components, modules, affiliations)

    # --- Backend module inference ---
    if backend_endpoints:
        _infer_backend_modules(backend_endpoints, modules, affiliations)

    # --- Reconcile: merge frontend+backend module info ---
    _reconcile_modules(modules, affiliations, api_calls, backend_endpoints)

    return dict(modules), dict(affiliations)


def _infer_frontend_modules(
    frontend_path, pages, components, modules: dict, affiliations: dict,
) -> None:
    """Infer modules from project configuration, with layered fallbacks.

    Layer 1: Router file parsing (Vue Router / React Router)
    Layer 2: views/ directory subdirectories
    Layer 3: Route URL segment grouping
    """
    fp = Path(frontend_path) if isinstance(frontend_path, str) else frontend_path

    # Layer 1: Parse router configuration file
    router_routes = _extract_router_routes(fp)
    if router_routes:
        for page in pages:
            module_name = _route_name_to_module(page.name, router_routes)
            if module_name:
                mod_id = f"Module_{module_name}"
                if module_name not in modules:
                    modules[module_name] = _new_module(module_name, source="frontend-router")
                modules[module_name]["page_count"] += 1
                affiliations[mod_id].append(page.id)
        return  # Router is authoritative, skip fallback

    # Layer 2: Directory-based — src/views/<module>/
    views_dir = _find_views_dir(fp)
    if views_dir:
        has_subdirs = False
        for sub in views_dir.iterdir():
            if sub.is_dir() and not sub.name.startswith(("_", ".")):
                has_subdirs = True
                module_name = _dir_to_module(sub.name)

                vue_files = list(sub.rglob("*.vue"))
                page_count = len(vue_files)

                if module_name not in modules:
                    modules[module_name] = _new_module(module_name, source="frontend-dirs")
                modules[module_name]["page_count"] += page_count

                for page in pages:
                    rel = page.file_path.replace("\\", "/")
                    if f"/{sub.name}/" in rel or rel.endswith(f"/{sub.name}.vue"):
                        mod_id = f"Module_{module_name}"
                        affiliations[mod_id].append(page.id)

        if modules:
            return  # Directory-based found modules, skip fallback

    # Layer 3: Route-path-based — group by first URL segment
    for page in pages:
        url = page.url if page.url.startswith("/") else "/" + page.url
        segments = [s for s in url.strip("/").split("/") if s]
        if not segments:
            continue
        module_name = segments[0].split("-")[0].split("_")[0]
        if len(module_name) < 2:
            continue

        mod_id = f"Module_{module_name}"
        if module_name not in modules:
            modules[module_name] = _new_module(module_name, source="frontend-routes")
        modules[module_name]["page_count"] += 1
        affiliations[mod_id].append(page.id)


def _infer_backend_modules(
    backend_endpoints: list[tuple[str, str, str, str, str]],
    modules: dict,
    affiliations: dict,
) -> None:
    """Infer modules from Controller names and API path prefixes."""
    # 1. Controller-based: UserController → "User", TaskController → "Task"
    for ep_id, method, path, ctrl_name, func_name in backend_endpoints:
        # Extract module from controller name: HeliosController → "Helios"
        module_name = ctrl_name.replace("Controller", "").replace("controller", "")
        # Handle common patterns: XxxYyyController → lowercase first segment
        module_name = _controller_to_module(module_name)

        mod_id = f"Module_{module_name}"
        if mod_id not in modules:
            modules[module_name] = {
                "name": module_name,
                "description": _module_description(module_name),
                "source": "backend",
                "page_count": 0,
                "component_count": 0,
                "action_count": 0,
                "endpoint_count": 0,
                "api_count": 0,
            }
        modules[module_name]["endpoint_count"] += 1
        modules[module_name]["source"] = (
            "frontend+backend" if modules[module_name]["source"] != "backend"
            else "backend"
        )
        affiliations[mod_id].append(ep_id)


def _reconcile_modules(
    modules: dict,
    affiliations: dict,
    api_calls: list | None,
    backend_endpoints: list[tuple[str, str, str, str, str]] | None,
) -> None:
    """Merge module info and enrich with action counts."""
    # Map API calls to modules by path prefix
    if api_calls:
        for ac in api_calls:
            if not ac.api_path:
                continue
            # Find matching module by API path
            for mod_name, mod_info in modules.items():
                if _path_belongs_to_module(ac.api_path, mod_name):
                    mod_info["api_count"] += 1
                    break

    # Enrich endpoint counts
    if backend_endpoints:
        endpoint_by_module: dict[str, int] = defaultdict(int)
        for _ep_id, _method, path, ctrl_name, _func in backend_endpoints:
            mod = _controller_to_module(ctrl_name.replace("Controller", "").replace("controller", ""))
            endpoint_by_module[mod] += 1
        for mod_name, count in endpoint_by_module.items():
            if mod_name in modules:
                modules[mod_name]["endpoint_count"] = count


def _path_belongs_to_module(api_path: str, module_name: str) -> bool:
    """Check if an API path belongs to a module."""
    clean_path = api_path.strip("/").lower()
    clean_module = module_name.lower()
    return clean_module in clean_path


def _find_views_dir(root: Path) -> Path | None:
    """Find the views directory in the frontend project."""
    for candidate in [
        root / "src" / "views",
        root / "src" / "pages",
        root / "views",
        root / "pages",
    ]:
        if candidate.is_dir():
            return candidate
    return None


def _dir_to_module(dir_name: str) -> str:
    """Convert directory name to module name."""
    # kebab-case → Title Case
    parts = dir_name.replace("-", " ").replace("_", " ").split()
    return " ".join(p.capitalize() for p in parts) if parts else dir_name


def _controller_to_module(ctrl_name: str) -> str:
    """Convert Controller class name to module name."""
    # PascalCase → Title Case: Helios → "Helios", AdvancedAnalytics → "Advanced Analytics"
    spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', ctrl_name)
    return spaced.strip()


def _module_description(module_name: str) -> str:
    """Generate a human-readable description for a module."""
    name = module_name.lower()
    descriptions = {
        "helios": "Task management and scheduling",
        "agent": "Agent lifecycle management",
        "team": "Team collaboration",
        "task": "Task operations",
        "monitor": "System monitoring",
        "system": "System administration",
        "audit": "Audit and logging",
        "claim": "Task claiming workflow",
        "sla": "SLA management",
        "progress": "Progress tracking",
        "quality": "Quality assessment",
        "skill": "Skill management",
        "dashboard": "Dashboard and overview",
        "analytics": "Analytics and reporting",
        "advanced analytics": "Advanced analytics",
        "notification": "Notifications and alerts",
        "schedule": "Scheduling",
        "scheduling": "Scheduling",
        "retry": "Retry policy",
        "feature": "Feature management",
        "assignee": "Assignment management",
        "claim picker": "Claim selection",
        "execution": "Execution steps",
        "process": "Process management",
        "task stats": "Task statistics",
        "task search": "Task search",
        "task archival": "Task archival",
        "task misc": "Miscellaneous tasks",
        "skill growth": "Skill growth tracking",
        "cc connect": "CC Connect integration",
        "agent profile": "Agent profile",
        "agent work status": "Agent work status",
    }
    return descriptions.get(name, f"{module_name} module")


# ------------------------------------------------------------------
# Router-based module inference
# ------------------------------------------------------------------

def _new_module(name: str, source: str = "") -> dict:
    """Create a new module dict with default fields."""
    return {
        "name": name,
        "description": _module_description(name),
        "source": source,
        "page_count": 0,
        "component_count": 0,
        "action_count": 0,
        "endpoint_count": 0,
        "api_count": 0,
    }


def _find_router_file(root: Path) -> Path | None:
    """Find the router configuration file in a frontend project.

    Searches common locations for Vue Router and React Router configs.
    """
    candidates = [
        # Vue Router
        root / "src" / "router" / "index.js",
        root / "src" / "router" / "index.ts",
        root / "src" / "routes" / "index.js",
        root / "src" / "routes" / "index.ts",
        # React Router
        root / "src" / "App.js",
        root / "src" / "App.tsx",
        root / "src" / "routes.js",
        root / "src" / "routes.tsx",
        # Root-level
        root / "router.js",
        root / "router.ts",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _extract_router_routes(root: Path) -> list[tuple[str, str]]:
    """Extract route (name, path) pairs from router configuration files.

    Parses Vue Router and React Router style route definitions.
    Returns list of (route_name, route_path) tuples.

    Strategy: parse the routes array as key-value blocks bounded by `{` and `}`.
    For each block, extract `path` and `name` values and pair them.
    """
    routes: list[tuple[str, str]] = []
    router_file = _find_router_file(root)
    if not router_file:
        return routes

    content = router_file.read_text()

    # Strategy: find all {...} blocks that contain both path and name
    # This handles multi-line route objects reliably
    brace_start = 0
    while True:
        idx = content.find("{", brace_start)
        if idx == -1:
            break
        # Find matching closing brace
        depth = 0
        end = idx
        for i in range(idx, min(idx + 500, len(content))):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end <= idx:
            brace_start = idx + 1
            continue

        block = content[idx:end]
        path_m = re.search(r"path\s*:\s*['\"]([^'\"]+)['\"]", block)
        name_m = re.search(r"name\s*:\s*['\"]([^'\"]+)['\"]", block)
        if path_m and name_m:
            route = (name_m.group(1), path_m.group(1))
            if route not in routes:
                routes.append(route)

        brace_start = end

    # React Router fallback: <Route path="/xxx" name="xxx" />
    if not routes:
        for m in re.finditer(
            r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\'][^>]*>',
            content,
        ):
            path = m.group(1)
            tag = m.group(0)
            name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', tag)
            route_name = name_match.group(1) if name_match else ""
            if path:
                routes.append((route_name, path))

    return routes


def _route_name_to_module(page_name: str, routes: list[tuple[str, str]]) -> str | None:
    """Map a page to a module name using router route definitions.

    Strategy: find the route whose name or path matches the page,
    then extract the semantic module name.

    Handles action-prefixed routes: create-order → order, address-edit → address.
    """
    page_lower = page_name.lower()

    # Try to find a matching route by name or path
    for route_name, route_path in routes:
        route_lower = route_name.lower().replace("-", "").replace("_", "")
        path_lower = route_path.replace("/", "").replace("-", "").replace("_", "")

        if route_lower == page_lower or path_lower == page_lower:
            # Extract module: prefer the semantically meaningful part
            # Action-prefixed routes: create-order → order, address-edit → address
            segments = route_name.replace("-", "_").split("_")
            if len(segments) >= 2:
                # Check if first segment is an action verb → use second segment
                action_verbs = {"create", "edit", "new", "add", "update", "delete", "remove", "list", "view", "show"}
                if segments[0] in action_verbs and len(segments[1]) >= 2:
                    return segments[1]
                # Otherwise use first segment
                if len(segments[0]) >= 2:
                    return segments[0]
            # Single segment route
            if len(segments) == 1 and len(segments[0]) >= 2:
                return segments[0]

    # Fallback: extract from page name directly (PascalCase → first word)
    words = re.sub(r'([A-Z])', r' \1', page_name).strip().split()
    if words:
        module = words[0].lower()
        if len(module) >= 2:
            return module

    return None
