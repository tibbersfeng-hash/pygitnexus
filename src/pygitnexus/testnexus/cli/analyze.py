"""Analyze command: scan frontend code and build TestGraph."""

from __future__ import annotations

import re
from pathlib import Path

import click

from ..core.pipeline import run_analysis
from ..scanners.react_scanner import ReactScanner
from ..scanners.vue_scanner import VueScanner


@click.command("analyze")
@click.option("--frontend", required=True, help="Path to frontend source code.")
@click.option("--backend", default=None, help="Path to backend source code (for API mapping).")
@click.option("--platform", default=None, help="Force platform: vue or react.")
@click.option("--llm", is_flag=True, help="Enable LLM-enhanced business module analysis.")
@click.option("--provider", default=None, help="LLM provider: openai or anthropic (env: TESTNEXUS_LLM_PROVIDER).")
@click.option("--llm-url", default=None, help="LLM API base URL (env: TESTNEXUS_LLM_URL).")
@click.option("--api-key", default=None, help="API key (env: TESTNEXUS_API_KEY).")
@click.option("--model", default=None, help="LLM model name (env: TESTNEXUS_LLM_MODEL).")
def analyze_cmd(
    frontend: str,
    backend: str | None,
    platform: str | None,
    llm: bool,
    provider: str | None,
    llm_url: str | None,
    api_key: str | None,
    model: str | None,
) -> None:
    """Analyze frontend code and build the TestGraph."""
    frontend_path = Path(frontend).resolve()
    if not frontend_path.is_dir():
        click.echo(f"Error: {frontend} is not a directory.")
        raise SystemExit(1)

    # Auto-detect platform
    if platform is None:
        platform = _detect_platform(frontend_path)

    scanner = VueScanner() if platform == "vue" else ReactScanner()
    click.echo(f"Platform: {platform}")
    click.echo(f"Scanning: {frontend_path}")

    # Gather backend endpoints if available
    backend_endpoints = None
    if backend:
        backend_endpoints = _extract_backend_endpoints(backend)

    # Build LLM config if enabled
    llm_config = None
    if llm:
        from ..core.llm_provider import LLMProviderError, resolve_llm_config
        try:
            llm_config = resolve_llm_config(
                provider=provider,
                api_key=api_key,
                model=model,
                base_url=llm_url,
            )
            if not llm_config.api_key:
                click.echo("Error: No API key found. Set --api-key, TESTNEXUS_API_KEY, or ANTHROPIC_AUTH_TOKEN in ~/.claude/settings.json.")
                raise SystemExit(1)
        except LLMProviderError as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)

    db_dir = frontend_path / ".testnexus"
    db_path = db_dir / "kuzu"
    db_dir.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        import shutil
        if db_path.is_dir():
            shutil.rmtree(db_path)
        else:
            db_path.unlink()

    def progress(pct: int, msg: str) -> None:
        click.echo(f"  [{pct:3d}%] {msg}")

    stats = run_analysis(
        frontend_path, db_path, scanner,
        backend_endpoints=backend_endpoints,
        progress_callback=progress,
        llm_config=llm_config,
        backend_path=backend,
    )

    click.echo(f"\nAnalysis complete!")
    for key, value in stats.items():
        click.echo(f"  {key}: {value}")


def _detect_platform(path: Path) -> str:
    """Auto-detect frontend platform."""
    # Check package.json for framework hints
    pkg = path / "package.json"
    if pkg.exists():
        content = pkg.read_text()
        if "vue" in content.lower():
            return "vue"
        if "react" in content.lower():
            return "react"

    # Check file extensions
    vue_count = len(list(path.rglob("*.vue")))
    tsx_count = len(list(path.rglob("*.tsx"))) + len(list(path.rglob("*.jsx")))
    if vue_count > tsx_count:
        return "vue"
    return "react"


def _extract_backend_endpoints(backend_path: str) -> list[tuple[str, str, str, str, str]] | None:
    """Extract endpoints from a pygitnexus database (Controller methods).

    Uses actual Spring @GetMapping/@PostMapping annotations from pygitnexus
    Class content, falling back to method name inference when annotations
    are not present.
    """
    import re

    try:
        backend_db = Path(backend_path) / ".pygitnexus" / "kuzu"
        if not backend_db.exists():
            return None

        import kuzu
        db = kuzu.Database(str(backend_db))
        conn = kuzu.Connection(db)

        # Get controller classes with their full source content
        controllers = conn.execute(
            "MATCH (c:Class) WHERE c.filePath CONTAINS '/controller/' "
            "AND NOT c.filePath CONTAINS '/controller/vo/' "
            "AND NOT c.filePath CONTAINS '/controller/dto/' "
            "RETURN c.name as name, c.filePath as path, c.content as content"
        )
        controller_data = []
        while controllers.has_next():
            row = controllers.get_next()
            ctrl_name = row[0].split('.')[-1] if row[0] else ""
            ctrl_path = row[1] or ""
            ctrl_content = row[2] or ""
            controller_data.append((ctrl_name, ctrl_path, ctrl_content))

        if not controller_data:
            conn.close()
            return None

        endpoints = []
        endpoint_id = 0

        for ctrl_name, ctrl_path, ctrl_content in controller_data:
            # Extract class-level @RequestMapping
            class_base_path = _extract_request_mapping(ctrl_content)

            # Extract methods with their Spring annotations
            methods = _extract_controller_methods(ctrl_content)

            for method_name, http_method, method_path in methods:
                endpoint_id += 1
                ep_id = f"Endpoint_{endpoint_id}"
                # Combine class-level and method-level paths
                full_path = f"{class_base_path}{method_path}" if method_path.startswith("/") else f"{class_base_path}/{method_path}"
                full_path = full_path.replace("//", "/")
                # Add /api prefix if not present
                if not full_path.startswith("/api"):
                    full_path = f"/api{full_path}"
                endpoints.append((ep_id, http_method, full_path, ctrl_name, method_name))

        # If no annotation-based endpoints found, fall back to inference
        if not endpoints:
            controller_names = [c[0] for c in controller_data]
            result = conn.execute(
                "MATCH (m:Method) WHERE m.className IS NOT NULL "
                "RETURN m.name as name, m.className as className, "
                "m.filePath as filePath, m.isStatic as isStatic, "
                "m.isPublic as isPublic"
            )
            while result.has_next():
                row = result.get_next()
                method_name = row[0] or ""
                class_name = row[1] or ""
                file_path = row[2] or ""
                is_static = row[3]
                is_public = row[4]

                ctrl = class_name.split('.')[-1] if class_name else ""
                if ctrl in controller_names and is_public:
                    endpoint_id += 1
                    ep_id = f"Endpoint_{endpoint_id}"
                    http_method = _infer_http_method(method_name)
                    api_path = _infer_api_path(ctrl, method_name, class_name, file_path)
                    endpoints.append((ep_id, http_method, api_path, ctrl, method_name))

        conn.close()
        return endpoints
    except Exception as e:
        return None


def _extract_request_mapping(content: str) -> str:
    """Extract class-level @RequestMapping path from Controller source."""
    # Match @RequestMapping("/api/xxx") or @RequestMapping(value = "/api/xxx")
    patterns = [
        r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']',
        r'@RequestMapping\s*\(\s*\{[^}]*value\s*=\s*["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return match.group(1)
    return ""


def _extract_controller_methods(content: str) -> list[tuple[str, str, str]]:
    """Extract methods with their Spring @*Mapping annotations from Controller source.

    Returns list of (method_name, http_method, path).
    """
    import re

    # Map annotation type to HTTP method
    annotation_map = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "DeleteMapping": "DELETE",
        "PatchMapping": "PATCH",
        "RequestMapping": None,  # needs explicit method
    }

    results = []

    # Find all method declarations lines with preceding annotations
    # Split content into lines and scan for annotation + method patterns
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check if this line is an annotation
        annotation_match = re.search(r'@(Get|Post|Put|Delete|Patch|Request)Mapping', line)
        if annotation_match:
            # Collect annotation attributes
            annotation_line = line
            while i < len(lines) - 1 and ')' not in line:
                i += 1
                annotation_line += lines[i]
                line = lines[i]

            # Determine HTTP method
            anno_type = annotation_match.group(1) + "Mapping"
            http_method = annotation_map.get(anno_type, "GET")

            # For @RequestMapping, try to infer method from 'method' attribute
            if anno_type == "RequestMapping":
                method_match = re.search(r'method\s*=\s*RequestMethod\.(\w+)', annotation_line)
                if method_match:
                    http_method = method_match.group(1).upper()
                else:
                    http_method = "GET"

            # Extract path from annotation
            path_match = re.search(r'(?:value\s*=\s*)?["\']([^"\']+)["\']', annotation_line)
            method_path = path_match.group(1) if path_match else ""

            # Look for method declaration in subsequent lines
            j = i + 1
            while j < len(lines) and j <= i + 3:
                method_decl = re.search(
                    r'(?:public|private|protected)\s+[\w<>.,\s\[\]?$]+\s+(\w+)\s*\(',
                    lines[j],
                )
                if method_decl:
                    method_name = method_decl.group(1)
                    results.append((method_name, http_method, method_path))
                    break
                j += 1

        i += 1

    return results


def _infer_http_method(method_name: str) -> str:
    """Infer HTTP method from Java method name."""
    name = method_name.lower()
    if name.startswith(("get", "list", "search", "query", "find")):
        return "GET"
    elif name.startswith(("create", "add", "insert", "submit", "register")):
        return "POST"
    elif name.startswith(("update", "edit", "modify", "set", "sync")):
        return "PUT"
    elif name.startswith(("delete", "remove", "purge", "clear", "archive")):
        return "DELETE"
    return "GET"


def _infer_api_path(ctrl: str, method: str, class_name: str, file_path: str) -> str:
    """Infer API path from controller and method."""
    # Try to get the base path from known patterns
    base = ctrl.replace("Controller", "").lower()
    # Common patterns
    path_map = {
        "dashboard": "/api",
        "helios": "/api/helios/tasks",
        "agent": "/api/helios/tasks/agents",
        "team": "/api/team",
        "task": "/api/helios/tasks",
    }
    for key, val in path_map.items():
        if key in base or key in file_path.lower():
            return f"{val}/{method.lower()}"
    return f"/api/{base}/{method.lower()}"
