"""Analysis pipeline: scan → extract → map → build TestGraph."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..core.mapper import map_api_calls
from ..core.models import Page, Component, Action
from ..core.module_infer import infer_modules
from ..graph.store import GraphStore
from ..scanners.base import BaseScanner
from ..storage.meta import ProjectInfo, register_project


def run_analysis(
    frontend_path: str | Path,
    db_path: str | Path,
    scanner: BaseScanner,
    backend_endpoints: list[tuple[str, str, str, str, str]] | None = None,
    progress_callback=None,
    llm_config=None,
    backend_path: str | Path | None = None,
) -> dict:
    """Run the full TestNexus analysis pipeline.

    Args:
        frontend_path: Path to frontend source code.
        db_path: Path for the KuzuDB database.
        scanner: A frontend scanner instance (VueScanner / ReactScanner).
        backend_endpoints: Optional list of (id, method, path, function_name)
                          from pygitnexus backend analysis.
        progress_callback: Optional callable(progress_pct: int, message: str).

    Returns:
        Statistics about the analysis.
    """
    frontend_path = Path(frontend_path) if isinstance(frontend_path, str) else frontend_path
    db_path = Path(db_path) if isinstance(db_path, str) else db_path

    def _progress(pct: int, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    # Step 1: Scan frontend files
    _progress(5, "Scanning for frontend files...")
    files = scanner.scan_files(frontend_path)
    _progress(10, f"Found {len(files)} files")

    # Step 2: Extract pages, components, actions
    _progress(15, "Extracting pages...")
    pages = scanner.extract_pages(files)
    _progress(25, f"Found {len(pages)} pages")

    _progress(30, "Extracting components...")
    components = scanner.extract_components(files)
    _progress(40, f"Found {len(components)} components")

    _progress(45, "Extracting actions and API calls...")
    actions = scanner.extract_actions(files)
    api_calls = scanner.get_api_calls(files)
    _progress(50, f"Found {len(actions)} actions, {len(api_calls)} API calls")

    # Extract API parameters from service files
    _progress(52, "Extracting API parameters...")
    api_params = scanner.extract_api_params(files)
    _progress(55, f"Found {sum(len(p['params']) for p in api_params)} parameters across {len(api_params)} APIs")

    # Step 3: Map frontend API calls to backend endpoints
    _progress(60, "Mapping API calls to backend endpoints...")
    frontend_call_pairs = [(ac.http_method, ac.api_path) for ac in api_calls if ac.api_path]
    if backend_endpoints:
        api_mappings = map_api_calls(frontend_call_pairs, backend_endpoints)
    else:
        api_mappings = []
    _progress(65, f"Found {len(api_mappings)} API mappings")

    # Step 3.5: Infer business modules
    _progress(67, "Inferring business modules...")
    modules, affiliations = infer_modules(
        frontend_path=frontend_path,
        pages=pages,
        components=components,
        api_calls=api_calls,
        backend_endpoints=backend_endpoints,
    )
    _progress(68, f"Found {len(modules)} business modules")

    # Step 3.6: Optional LLM-powered module analysis
    if llm_config is not None:
        try:
            _progress(69, "Running LLM-powered module analysis...")
            from ..core.llm_analyzer import LLMAnalyzer
            from ..core.llm_provider import LLMProvider
            from ..core.metadata_extractor import MetadataExtractor

            provider = LLMProvider(llm_config)
            bp = Path(backend_path) if isinstance(backend_path, str) else backend_path
            extractor = MetadataExtractor(store, Path(frontend_path), bp)
            analysis_result = extractor.extract_all()

            analyzer = LLMAnalyzer(provider, store)
            modules_list, workflows, classifications = analyzer.analyze(analysis_result)
            raw_json = str(analysis_result)  # Will be replaced with actual raw output
            analyzer.store_results(modules_list, workflows, classifications, llm_config.model)
            analyzer.update_modules(modules_list)

            _progress(70, f"LLM analysis complete ({len(modules_list)} modules)")
            provider.close()
        except Exception as e:
            _progress(70, f"LLM analysis skipped: {e}")

    # Step 4: Build TestGraph
    _progress(70, "Building TestGraph...")
    store = GraphStore(db_path)
    store.init_schema()
    _write_to_graph(store, pages, components, actions, api_mappings, modules, affiliations, backend_endpoints, api_calls, api_params)

    # Step 5: Save metadata and register
    _progress(95, "Saving metadata...")
    stats = {
        "files": len(files),
        "pages": len(pages),
        "components": len(components),
        "actions": len(actions),
        "api_calls": len(api_calls),
        "api_mappings": len(api_mappings),
        "modules": len(modules),
        "api_params": sum(len(p.get("params", [])) for p in api_params),
    }
    project_info = ProjectInfo(
        name=frontend_path.name,
        frontend_path=str(frontend_path),
        stats=stats,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
    )
    register_project(project_info)

    store.close()
    _progress(100, "Analysis complete")

    return stats


def _write_to_graph(
    store: GraphStore,
    pages: list[Page],
    components: list[Component],
    actions: list[Action],
    api_mappings: list[tuple[str, str, float, str]],
    modules: dict[str, dict],
    affiliations: dict[str, list],
    backend_endpoints: list[tuple[str, str, str, str, str]] | None = None,
    api_calls: list | None = None,
    api_params: list[dict] | None = None,
) -> None:
    """Write all data to the TestGraph."""

    # 0. Write modules
    for mod_name, mod_info in modules.items():
        mod_id = f"Module_{mod_name}"
        store.insert_node("Module", {
            "id": mod_id,
            "name": mod_info.get("name", mod_name),
            "description": mod_info.get("description", ""),
            "source": mod_info.get("source", ""),
            "pageCount": mod_info.get("page_count", 0),
            "componentCount": mod_info.get("component_count", 0),
            "actionCount": mod_info.get("action_count", 0),
            "endpointCount": mod_info.get("endpoint_count", 0),
            "apiCount": mod_info.get("api_count", 0),
        })

    # 1. Write pages
    for page in pages:
        store.insert_node("Page", {
            "id": page.id,
            "name": page.name,
            "filePath": page.file_path,
            "url": page.url,
            "platform": page.platform,
        })

    # 2. Write components
    for comp in components:
        store.insert_node("Component", {
            "id": comp.id,
            "name": comp.name,
            "filePath": comp.file_path,
            "type": comp.type,
            "platform": comp.platform,
        })

    # 3. Write actions
    for action in actions:
        store.insert_node("Action", {
            "id": action.id,
            "name": action.name,
            "type": action.type,
            "selector": action.selector,
            "componentName": action.component_name,
            "httpMethod": action.http_method,
            "apiPath": action.api_path,
            "lineNumber": action.line_number,
        })
        # HAS_ACTION edge
        if action.component_name:
            comp_id = _find_component_id(components, action.component_name)
            if comp_id:
                store.insert_relation("Component", "Action", comp_id, action.id,
                                      "HAS_ACTION", 1.0, "component has action")

    # 3b. Write APICall entries as Action nodes (from service/*.js, etc.)
    if api_calls:
        for ac in api_calls:
            if not ac.api_path:
                continue
            ac_id = f"Action_{ac.component}_{ac.http_method.lower()}_{_path_to_name(ac.api_path)}_{ac.line_number}"
            store.insert_node("Action", {
                "id": ac_id,
                "name": f"{ac.http_method.lower()}_{_path_to_name(ac.api_path)}",
                "type": "fetch",
                "selector": "",
                "componentName": ac.component,
                "httpMethod": ac.http_method,
                "apiPath": ac.api_path,
                "lineNumber": ac.line_number,
            })
            # HAS_ACTION edge to component
            comp_id = _find_component_id(components, ac.component)
            if comp_id:
                store.insert_relation("Component", "Action", comp_id, ac_id,
                                      "HAS_ACTION", 1.0, "component has api call")

    # 4. Write backend endpoints
    if backend_endpoints:
        for ep_id, method, path, ctrl_name, func_name in backend_endpoints:
            store.insert_node("Endpoint", {
                "id": ep_id,
                "method": method,
                "path": path,
                "controllerName": ctrl_name,
                "functionName": func_name,
                "parameters": "{}",
                "responseType": "",
            })

    # 5. Write API mappings (Action → Endpoint relations)
    # First try to find Action by api_path from existing actions
    for fe_path, be_id, confidence, reason in api_mappings:
        action_id = _find_action_by_api_path(actions, fe_path)
        if not action_id and api_calls:
            # Also search in api_calls
            action_id = _find_apicall_by_api_path(api_calls, fe_path)
        if action_id:
            store.insert_relation("Action", "Endpoint", action_id, be_id,
                                  "CALLS", confidence, reason)

    # 5b. Write API parameters and link to Actions
    if api_params:
        for api_info in api_params:
            method = api_info.get("http_method", "")
            path = api_info.get("api_path", "")
            for param in api_info.get("params", []):
                param_id = f"ApiParam_{_slug(method)}_{_path_to_name(path)}_{param.name}"
                store.insert_node("ApiParam", {
                    "id": param_id,
                    "name": param.name,
                    "paramType": param.param_type,
                    "valueType": param.value_type,
                    "required": param.required,
                    "sampleValue": param.sample_value,
                    "apiMethod": method,
                    "apiPath": path,
                })
                # Link to Action nodes that match this API
                related_action = _find_action_by_api_path(actions, path)
                if not related_action and api_calls:
                    related_action = _find_apicall_by_api_path(api_calls, path)
                if related_action:
                    store.insert_relation("Action", "ApiParam", related_action, param_id,
                                          "HAS_PARAM", 1.0, "action has parameter")

    # 6. Write module affiliations (Module → Page/Component/Endpoint)
    for mod_id, entity_ids in affiliations.items():
        for eid in entity_ids:
            # Determine entity type by ID prefix
            if eid.startswith("Page_"):
                store.insert_relation("Module", "Page", mod_id, eid,
                                      "BELONGS_TO", 1.0, "page belongs to module")
            elif eid.startswith("Comp_"):
                store.insert_relation("Module", "Component", mod_id, eid,
                                      "BELONGS_TO", 1.0, "component belongs to module")
            elif eid.startswith("Endpoint_"):
                store.insert_relation("Module", "Endpoint", mod_id, eid,
                                      "BELONGS_TO", 1.0, "endpoint belongs to module")


def _find_component_id(components: list[Component], name: str) -> str | None:
    """Find component by simple name (not FQN)."""
    for comp in components:
        if comp.name == name:
            return comp.id
    # Try fuzzy match
    for comp in components:
        if name.lower() in comp.name.lower():
            return comp.id
    return None


def _find_action_by_api_path(actions: list[Action], api_path: str) -> str | None:
    """Find action by API path."""
    for action in actions:
        if action.api_path == api_path:
            return action.id
    # Try fuzzy: the action name should contain the path
    clean_path = api_path.strip("/").split("/")[-1]
    for action in actions:
        if clean_path and clean_path.lower() in action.name.lower():
            return action.id
    return None


def _find_apicall_by_api_path(api_calls: list, api_path: str) -> str | None:
    """Find APICall-derived action ID by API path."""
    clean_path = api_path.strip("/")
    for ac in api_calls:
        if not ac.api_path:
            continue
        ac_clean = ac.api_path.strip("/")
        if ac_clean == clean_path:
            return f"Action_{ac.component}_{ac.http_method.lower()}_{_path_to_name(ac.api_path)}_{ac.line_number}"
        # Fuzzy: last segment match
        ac_last = ac_clean.split("/")[-1].split("{")[0].strip("/")
        fe_last = clean_path.split("/")[-1].split("{")[0].strip("/")
        if ac_last and fe_last and ac_last.lower() == fe_last.lower():
            return f"Action_{ac.component}_{ac.http_method.lower()}_{_path_to_name(ac.api_path)}_{ac.line_number}"
    return None


def _path_to_name(path: str) -> str:
    """Convert API path to a readable name."""
    clean = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return clean if clean else "root"


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]", "", text.lower().replace(" ", "_").replace("/", "_"))
