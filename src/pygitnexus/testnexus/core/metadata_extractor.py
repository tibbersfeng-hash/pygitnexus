"""Metadata extractor: gather structured context from TestGraph for LLM analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..graph.store import GraphStore


@dataclass
class ControllerInfo:
    """Backend controller information."""
    name: str
    file_path: str
    methods: list[dict] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    parent_class: str = ""
    injected_services: list[str] = field(default_factory=list)


@dataclass
class ModuleContext:
    """Aggregated context for a single inferred module."""
    name: str
    source: str  # "frontend" | "backend" | "frontend+backend"
    pages: list[dict] = field(default_factory=list)
    components: list[dict] = field(default_factory=list)
    endpoints: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    api_call_patterns: list[str] = field(default_factory=list)


@dataclass
class ProjectMetadata:
    """Full project metadata for LLM analysis."""
    project_name: str
    frontend_path: str
    backend_path: str | None
    total_stats: dict = field(default_factory=dict)
    modules: list[ModuleContext] = field(default_factory=list)
    controllers: list[ControllerInfo] = field(default_factory=list)
    directory_tree: str = ""
    api_path_prefixes: list[str] = field(default_factory=list)


class MetadataExtractor:
    """Extract structured metadata from TestGraph and filesystem."""

    MAX_TREE_LINES = 200
    MAX_ENTITIES_PER_MODULE = 50

    def __init__(self, store: GraphStore, frontend_path: Path | str,
                 backend_path: Path | str | None = None) -> None:
        self.store = store
        self.frontend_path = Path(frontend_path) if isinstance(frontend_path, str) else frontend_path
        self.backend_path = Path(backend_path) if isinstance(backend_path, str) else backend_path

    def extract_all(self) -> ProjectMetadata:
        """Gather all metadata from graph + filesystem."""
        stats = self._extract_stats()
        modules = self._extract_module_contexts()
        directory_tree = self._extract_directory_tree()
        api_prefixes = self._extract_api_prefixes()

        controllers: list[ControllerInfo] = []
        if self.backend_path:
            controllers = self._extract_controllers_from_files()

        return ProjectMetadata(
            project_name=self.frontend_path.name,
            frontend_path=str(self.frontend_path),
            backend_path=str(self.backend_path) if self.backend_path else None,
            total_stats=stats,
            modules=modules,
            controllers=controllers,
            directory_tree=directory_tree,
            api_path_prefixes=api_prefixes,
        )

    def _extract_stats(self) -> dict:
        """Query graph for total statistics."""
        stats = {}

        for node_type in ("Page", "Component", "Action", "Endpoint", "Module"):
            result = self.store.query(f"MATCH (n:{node_type}) RETURN count(n) AS cnt")
            if result:
                stats[node_type.lower() + "s"] = result[0].get("cnt", 0)
            else:
                stats[node_type.lower() + "s"] = 0

        # API call mappings (Action → Endpoint relations)
        result = self.store.query(
            "MATCH (a:Action)-[r]->(e:Endpoint) WHERE r.type = 'CALLS' RETURN count(r) AS cnt"
        )
        stats["api_mappings"] = result[0].get("cnt", 0) if result else 0

        return stats

    def _extract_module_contexts(self) -> list[ModuleContext]:
        """Query graph for each module's affiliated entities."""
        modules_raw = self.store.query(
            "MATCH (m:Module) RETURN m.id, m.name, m.source, "
            "m.pageCount, m.endpointCount, m.apiCount ORDER BY m.endpointCount DESC"
        )

        contexts: list[ModuleContext] = []
        for mod in modules_raw:
            mod_id = mod.get("m.id", "")
            name = mod.get("m.name", "")
            source = mod.get("m.source", "")

            ctx = ModuleContext(name=name, source=source)

            # Pages
            pages = self.store.query(
                f"MATCH (m:Module {{id: '{mod_id}'}})-[r]->(p:Page) "
                "WHERE r.type = 'BELONGS_TO' RETURN p.name, p.url, p.filePath "
                f"LIMIT {self.MAX_ENTITIES_PER_MODULE}"
            )
            ctx.pages = [{"name": p.get("p.name", ""), "url": p.get("p.url", ""),
                          "file_path": p.get("p.filePath", "")} for p in pages]

            # Components
            comps = self.store.query(
                f"MATCH (m:Module {{id: '{mod_id}'}})-[r]->(c:Component) "
                "WHERE r.type = 'BELONGS_TO' RETURN c.name, c.filePath "
                f"LIMIT {self.MAX_ENTITIES_PER_MODULE}"
            )
            ctx.components = [{"name": c.get("c.name", ""), "file_path": c.get("c.filePath", "")}
                              for c in comps]

            # Endpoints
            eps = self.store.query(
                f"MATCH (m:Module {{id: '{mod_id}'}})-[r]->(e:Endpoint) "
                "WHERE r.type = 'BELONGS_TO' RETURN e.method, e.path, e.controllerName, "
                "e.functionName, e.parameters "
                f"LIMIT {self.MAX_ENTITIES_PER_MODULE}"
            )
            ctx.endpoints = [{
                "method": e.get("e.method", ""),
                "path": e.get("e.path", ""),
                "controller": e.get("e.controllerName", ""),
                "function": e.get("e.functionName", ""),
                "parameters": e.get("e.parameters", ""),
            } for e in eps]

            # Actions
            actions = self.store.query(
                f"MATCH (m:Module {{id: '{mod_id}'}})-[r1]->(p:Page)-[r2*1..3]-(a:Action) "
                "WHERE r1.type = 'BELONGS_TO' RETURN DISTINCT a.name, a.type, "
                "a.httpMethod, a.apiPath "
                f"LIMIT {self.MAX_ENTITIES_PER_MODULE}"
            )
            ctx.actions = [{
                "name": a.get("a.name", ""),
                "type": a.get("a.type", ""),
                "http_method": a.get("a.httpMethod", ""),
                "api_path": a.get("a.apiPath", ""),
            } for a in actions]

            # API call patterns (unique path prefixes)
            prefixes = set()
            for ep in ctx.endpoints:
                path = ep.get("path", "")
                if path:
                    parts = path.strip("/").split("/")
                    if len(parts) >= 2:
                        prefixes.add("/" + "/".join(parts[:2]))
                    else:
                        prefixes.add(path)
            ctx.api_call_patterns = sorted(prefixes)

            contexts.append(ctx)

        return contexts

    def _extract_controllers_from_files(self) -> list[ControllerInfo]:
        """Extract controller info from pygitnexus graph, fallback to file parsing."""
        if not self.backend_path or not self.backend_path.is_dir():
            return []

        # Priority 1: Query pygitnexus KuzuDB
        pygitnexus_db = self.backend_path / ".pygitnexus" / "kuzu"
        if pygitnexus_db.exists():
            controllers = self._extract_controllers_from_pygitnexus(pygitnexus_db)
            if controllers:
                return controllers

        # Fallback: Parse Java files directly
        return self._extract_controllers_from_java_files()

    def _extract_controllers_from_pygitnexus(self, db_path: Path) -> list[ControllerInfo]:
        """Extract controller info from pygitnexus KuzuDB graph."""
        import kuzu

        controllers_map: dict[str, ControllerInfo] = {}
        try:
            db = kuzu.Database(str(db_path))
            conn = kuzu.Connection(db)

            # Get all controller methods with HTTP info
            method_result = conn.execute(
                "MATCH (m:Method) WHERE m.className CONTAINS 'Controller' "
                "AND m.isPublic = true AND m.httpMethod IS NOT NULL "
                "RETURN m.className, m.name, m.returnType, m.parameterCount, "
                "m.httpMethod, m.httpPath, m.httpParams, m.content, m.filePath"
            )

            while method_result.has_next():
                row = method_result.get_next()
                full_class_name = row[0] or ""
                short_name = full_class_name.split(".")[-1] if full_class_name else ""
                if not short_name:
                    continue

                if short_name not in controllers_map:
                    controllers_map[short_name] = ControllerInfo(
                        name=short_name,
                        file_path=row[8] or "",
                    )

                ctrl = controllers_map[short_name]
                ctrl.methods.append({
                    "name": row[1] or "",
                    "params": "",  # parameterCount available but not detailed params
                    "return_type": row[2] or "",
                    "http_method": row[4] or "",
                    "path": row[5] or "",
                })

            # Extract annotations and injected services from Class content
            for ctrl in controllers_map.values():
                class_result = conn.execute(
                    "MATCH (c:Class) WHERE c.name CONTAINS $shortName "
                    "RETURN c.content, c.filePath LIMIT 1",
                    {"shortName": f".{ctrl.name}"}
                )
                while class_result.has_next():
                    row = class_result.get_next()
                    content = row[0] or ""
                    if not ctrl.file_path:
                        ctrl.file_path = row[1] or ""

                    # Extract class-level annotations (lines starting with @ before class declaration)
                    for m in __import__("re").finditer(r'@(\w+)(?:\s*\([^)]*\))?', content):
                        ann = m.group(1)
                        # Only class-level annotations (before "public class")
                        pos = m.start()
                        if "public class" in content[pos:pos + 200]:
                            ctrl.annotations.append(f"@{ann}")
                        elif pos < content.find("public class") if "public class" in content else True:
                            ctrl.annotations.append(f"@{ann}")
                    ctrl.annotations = sorted(set(ctrl.annotations))

                    # Extract injected services from constructor parameters or fields
                    # Pattern: private final XxxService xxxService
                    for m in __import__("re").finditer(
                        r'(?:private\s+)?(?:final\s+)?(\w+Service)\s+(\w+)',
                        content,
                    ):
                        ctrl.injected_services.append(m.group(2))

                    # Also from constructor: ControllerName(XxxService svc, ...)
                    ctor_match = __import__("re").search(
                        rf'public\s+{ctrl.name}\s*\((.*?)\)', content, __import__("re").DOTALL
                    )
                    if ctor_match:
                        params = ctor_match.group(1)
                        for pm in __import__("re").finditer(r'(\w+)\s+(\w+)', params):
                            type_name, param_name = pm.group(1), pm.group(2)
                            if type_name.endswith("Service") or type_name.endswith("Repository"):
                                ctrl.injected_services.append(param_name)

                    ctrl.injected_services = sorted(set(ctrl.injected_services))

            conn.close()
        except Exception:
            return []

        return sorted(controllers_map.values(), key=lambda c: c.name)

    def _extract_controllers_from_java_files(self) -> list[ControllerInfo]:
        """Fallback: Extract controller info from Java source files directly."""
        if not self.backend_path or not self.backend_path.is_dir():
            return []

        controllers: list[ControllerInfo] = []

        # Find *Controller.java files
        for ctrl_file in self.backend_path.rglob("*Controller.java"):
            rel = ctrl_file.relative_to(self.backend_path)
            content = ctrl_file.read_text(encoding="utf-8", errors="ignore")
            info = _parse_controller_file(content, str(rel))
            if info:
                controllers.append(info)

        return controllers

    def _extract_directory_tree(self) -> str:
        """Build a condensed tree string of the frontend structure."""
        lines: list[str] = []
        self._walk_dir(self.frontend_path, prefix="", lines=lines, depth=0)

        # Truncate to max lines
        if len(lines) > self.MAX_TREE_LINES:
            lines = lines[:self.MAX_TREE_LINES]
            lines.append(f"  ... ({self.MAX_TREE_LINES} lines shown)")

        return "\n".join(lines) if lines else "(empty)"

    def _walk_dir(self, path: Path, prefix: str, lines: list[str],
                  depth: int, max_depth: int = 6) -> None:
        """Recursively build directory tree, filtering to relevant files."""
        if depth > max_depth:
            return

        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return

        relevant_exts = {".vue", ".tsx", ".jsx", ".ts", ".js", ".html"}
        relevant_dirs = {"views", "pages", "components", "src", "api", "services", "routes", "router"}

        for entry in entries:
            if entry.name.startswith((".git", "node_modules", "dist", "build", "__pycache__", ".testnexus")):
                continue

            is_dir = entry.is_dir()
            is_relevant = (
                is_dir and entry.name in relevant_dirs
            ) or (
                not is_dir and entry.suffix in relevant_exts
            ) or (
                not is_dir and entry.name in ("package.json", "routes.ts", "router.ts", "index.ts")
            )

            if not is_relevant and is_dir:
                # Check if any child is relevant
                has_relevant = False
                for child in entry.rglob("*"):
                    if child.suffix in relevant_exts:
                        has_relevant = True
                        break
                if not has_relevant:
                    continue

            connector = "├── " if entry != entries[-1] else "└── "
            lines.append(f"{prefix}{connector}{entry.name}")

            if is_dir and is_relevant:
                extension = "│   " if entry != entries[-1] else "    "
                self._walk_dir(entry, prefix + extension, lines, depth + 1, max_depth)

    def _extract_api_prefixes(self) -> list[str]:
        """Collect all unique API path prefixes from endpoints."""
        endpoints = self.store.query(
            "MATCH (e:Endpoint) RETURN e.path ORDER BY e.path"
        )

        prefixes: set[str] = set()
        for ep in endpoints:
            path = ep.get("e.path", "")
            if path:
                parts = path.strip("/").split("/")
                if len(parts) >= 2:
                    prefixes.add("/" + "/".join(parts[:2]))
                else:
                    prefixes.add(path)

        return sorted(prefixes)

    def serialize_for_llm(self) -> str:
        """Serialize ProjectMetadata into LLM-friendly text format."""
        metadata = self.extract_all()
        parts: list[str] = []

        # Header
        parts.append(f"=== Project: {metadata.project_name} ===")
        parts.append(f"Frontend: {metadata.frontend_path}")
        if metadata.backend_path:
            parts.append(f"Backend: {metadata.backend_path}")
        parts.append("")

        # Statistics
        stats = metadata.total_stats
        parts.append("=== Statistics ===")
        parts.append(
            f"Pages: {stats.get('pages', 0)}, "
            f"Components: {stats.get('components', 0)}, "
            f"Actions: {stats.get('actions', 0)}, "
            f"Endpoints: {stats.get('endpoints', 0)}, "
            f"Modules: {stats.get('modules', 0)}, "
            f"API Mappings: {stats.get('api_mappings', 0)}"
        )
        parts.append("")

        # Business Modules (from heuristic inference)
        if metadata.modules:
            parts.append("=== Business Modules (heuristic inference) ===")
            for mod in metadata.modules:
                parts.append(
                    f"- {mod.name} (source: {mod.source}, "
                    f"pages: {len(mod.pages)}, endpoints: {len(mod.endpoints)}, "
                    f"actions: {len(mod.actions)})"
                )
                # Show top endpoints
                for ep in mod.endpoints[:5]:
                    parts.append(
                        f"    {ep.get('method', '?')} {ep.get('path', '?')} "
                        f"({ep.get('controller', '')}.{ep.get('function', '')})"
                    )
            parts.append("")

        # Controllers
        if metadata.controllers:
            parts.append("=== Controllers ===")
            for ctrl in metadata.controllers:
                parts.append(f"## {ctrl.name} ({ctrl.file_path})")
                if ctrl.annotations:
                    parts.append(f"  Annotations: {', '.join(ctrl.annotations)}")
                if ctrl.injected_services:
                    parts.append(f"  @Inject: {', '.join(ctrl.injected_services)}")
                for method in ctrl.methods:
                    parts.append(
                        f"  + {method.get('name', '')}({method.get('params', '')})"
                        f" → {method.get('return_type', '')}"
                    )
                    if method.get("http_method") and method.get("path"):
                        parts.append(
                            f"    HTTP: {method['http_method']} {method['path']}"
                        )
                parts.append("")

        # API Path Prefixes
        if metadata.api_path_prefixes:
            parts.append("=== API Path Prefixes ===")
            parts.append(", ".join(metadata.api_path_prefixes[:30]))
            parts.append("")

        # Frontend Directory
        parts.append("=== Frontend Directory Structure ===")
        parts.append(metadata.directory_tree)
        parts.append("")

        return "\n".join(parts)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _parse_controller_file(content: str, file_path: str) -> ControllerInfo | None:
    """Parse a Java controller file to extract basic info."""
    import re

    # Class name
    class_match = re.search(r'public\s+class\s+(\w+Controller)', content)
    if not class_match:
        return None

    name = class_match.group(1)
    annotations: list[str] = []
    injected_services: list[str] = []
    methods: list[dict] = []

    # Class-level annotations (before class declaration, lines with @)
    class_start = class_match.start()
    class_header = content[max(0, class_start - 500):class_start]
    for m in re.finditer(r'@([A-Z]\w+)(?:\s*(?:\(|\{))?', class_header):
        ann_name = m.group(1)
        if ann_name not in ("Override", "Deprecated", "SuppressWarnings"):
            annotations.append(f"@{ann_name}")
    annotations = sorted(set(annotations))

    # Injected services (field-level @Autowired, @Inject, @Resource)
    for m in re.finditer(
        r'(?:@Autowired|@Inject|@Resource)\s+(?:private\s+)?(?:final\s+)?(\w+)\s+(\w+)',
        content,
    ):
        injected_services.append(m.group(2))
    injected_services = sorted(set(injected_services))

    # Methods: look for public methods with proper patterns
    # Match: public <return_type> <method_name>(<params>)
    method_pattern = re.compile(
        r'public\s+'
        r'(?:[\w.<>?\[\],\s]+?)\s+'  # return type (non-greedy)
        r'(\w+)\s*'                   # method name (group 1)
        r'\(([^)]*)\)',               # params (group 2)
    )

    for m in method_pattern.finditer(content):
        method_name = m.group(1)
        params = m.group(2).strip()

        # Skip common non-method patterns
        if method_name in ("class", "interface", "if", "for", "while", "return", "new", "toString"):
            continue

        # Get the return type by looking at text before method name
        match_text = m.group(0)
        return_type_match = re.match(r'public\s+([\w.<>?\[\],\s]+?)\s+\w+\s*\(', match_text)
        return_type = return_type_match.group(1).strip() if return_type_match else ""

        # Look for HTTP method annotation in the ~200 chars before method declaration
        method_decl_start = m.start()
        preceding = content[max(0, method_decl_start - 300):method_decl_start]

        # Only look at annotations (lines starting with @) immediately before the method
        # Get just the annotation block (consecutive @ lines before the method)
        ann_block = ""
        for line in reversed(preceding.split("\n")):
            stripped = line.strip()
            if stripped.startswith("@"):
                ann_block = stripped + "\n" + ann_block
            elif stripped and not stripped.endswith("{"):
                break  # Stop at non-annotation, non-brace line

        http_method = ""
        http_path = ""

        http_annotations = [
            (r'@GetMapping\s*(?:\(\s*(?:value\s*=\s*)?"([^"]+)"|([^)]*)\))?', "GET"),
            (r'@PostMapping\s*(?:\(\s*(?:value\s*=\s*)?"([^"]+)"|([^)]*)\))?', "POST"),
            (r'@PutMapping\s*(?:\(\s*(?:value\s*=\s*)?"([^"]+)"|([^)]*)\))?', "PUT"),
            (r'@DeleteMapping\s*(?:\(\s*(?:value\s*=\s*)?"([^"]+)"|([^)]*)\))?', "DELETE"),
            (r'@PatchMapping\s*(?:\(\s*(?:value\s*=\s*)?"([^"]+)"|([^)]*)\))?', "PATCH"),
            (r'@RequestMapping\s*\(\s*(?:.*?value\s*=\s*)?"([^"]+)"', None),
        ]

        for pattern, fixed_method in http_annotations:
            am = re.search(pattern, ann_block)
            if am:
                http_method = fixed_method or ""
                # Extract path from first non-None group
                for i in range(1, am.lastindex + 1 if am.lastindex else 1):
                    val = am.group(i)
                    if val:
                        http_path = val
                        break
                break

        methods.append({
            "name": method_name,
            "params": params[:100],  # Truncate long params
            "return_type": return_type,
            "http_method": http_method,
            "path": http_path,
        })

    return ControllerInfo(
        name=name,
        file_path=file_path,
        methods=methods,
        annotations=annotations,
        injected_services=injected_services,
    )
