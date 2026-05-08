"""Analysis pipeline: scan → parse → extract → resolve → store."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..core.models import ParsedFile
from ..core.scanner import scan
from ..core.extractor import parse as parse_java
from ..core.extractor_js import parse as parse_js
from ..core.extractor_ts import parse as parse_ts
from ..core.extractor_vue import parse as parse_vue
from ..core.extractor_html import parse as parse_html
from ..core.extractor_xml import parse as parse_xml
from ..core.resolver import resolve_calls, resolve_imports
from ..core.resolver_js import resolve_js_ts_calls
from ..graph.store import GraphStore
from ..storage.repo_manager import register_repo, RepoInfo


def _resolve_html_endpoint_calls(parsed_files: list) -> list[tuple]:
    """Extract HTTP endpoint calls from HTML template files.

    HTML files produce CallSite objects with http_method/http_path from
    window.location.href, $.ajax, fetch(), and form actions. These are
    returned as 8-tuples for direct inclusion in call_relations.

    Returns:
        List of (caller, caller_file, target, target_file, confidence,
                 http_method, http_path, http_params) tuples.
    """
    results: list[tuple] = []
    for pf in parsed_files:
        if not pf.file_path.endswith(".html"):
            continue
        for call in pf.calls:
            if call.http_method and call.http_path:
                results.append((
                    call.caller_method or pf.file_path,
                    pf.file_path,
                    call.target_name,
                    "",  # No specific target file — matched via USES_ENDPOINT
                    0.7,
                    call.http_method,
                    call.http_path,
                    call.http_params,
                ))
    return results


def run_analysis(
    repo_path: str | Path,
    db_path: str | Path,
    progress_callback=None,
) -> dict:
    """Run the full analysis pipeline on a Java repository.

    Args:
        repo_path: Path to the repository root.
        db_path: Path for the KuzuDB database.
        progress_callback: Optional callable(progress_pct: int, message: str).

    Returns:
        Statistics about the analysis.
    """
    repo_path = Path(repo_path) if isinstance(repo_path, str) else repo_path
    db_path = Path(db_path) if isinstance(db_path, str) else db_path

    def _progress(pct: int, msg: str) -> None:
        if progress_callback:
            # Round to 5% increments to reduce output frequency
            pct_rounded = round(pct / 5) * 5
            if pct_rounded != _progress._last_pct or _progress._last_msg != msg:
                progress_callback(pct_rounded, msg)
                _progress._last_pct = pct_rounded
                _progress._last_msg = msg

    _progress._last_pct = -1
    _progress._last_msg = ""

    # Step 1: Scan
    _progress(5, "Scanning for source files...")
    source_files = scan(repo_path)
    _progress(10, f"Found {len(source_files)} source files")

    # Step 2: Parse files concurrently (ThreadPoolExecutor — tree-sitter releases GIL)
    _progress(15, "Parsing files (concurrent)...")
    t0 = time.monotonic()
    parsed_files = _parse_concurrent(source_files, _progress, len(source_files))
    parse_elapsed = time.monotonic() - t0
    _progress(55, f"Parsed {len(parsed_files)}/{len(source_files)} files in {parse_elapsed:.1f}s")

    # Step 3: Resolve cross-file relations (chunked parallel)
    _progress(60, "Resolving cross-file relations...")
    class_map = resolve_imports(parsed_files)
    t_resolve = time.monotonic()
    call_relations = _resolve_calls_parallel(parsed_files, class_map)

    # Step 3b: Resolve JS/TS cross-file calls via ES Module imports
    js_ts_relations = resolve_js_ts_calls(parsed_files, project_root=str(repo_path))
    call_relations.extend(js_ts_relations)

    # Step 3c: Extract HTML template HTTP endpoint references
    html_relations = _resolve_html_endpoint_calls(parsed_files)
    call_relations.extend(html_relations)

    # Step 3d: Enrich Java CALLS relations with Spring endpoint info
    # Build (method_name, file_path) → (http_method, http_path) from controller annotations
    spring_endpoint_map = _build_spring_endpoint_map(parsed_files)
    call_relations = _enrich_java_calls(call_relations, spring_endpoint_map)

    resolve_elapsed = time.monotonic() - t_resolve
    _progress(70, f"Resolved {len(call_relations)} calls in {resolve_elapsed:.1f}s")

    # Step 4: Build graph with batched writes
    _progress(70, "Building knowledge graph (batched)...")
    t1 = time.monotonic()
    store = GraphStore(db_path)
    store.init_schema()
    _write_to_graph_batched(store, parsed_files, source_files, repo_path, call_relations, class_map, spring_endpoint_map)
    # Step 4b: Parse MyBatis mapper XMLs and create MAPS_TO edges
    _write_mybatis_relations(store, source_files, parsed_files)
    graph_elapsed = time.monotonic() - t1
    _progress(95, f"Graph built in {graph_elapsed:.1f}s")

    # Step 5: Save metadata and register
    stats = _compute_stats(parsed_files)
    repo_info = RepoInfo(
        name=repo_path.name,
        path=str(repo_path),
        stats=stats,
    )
    register_repo(repo_info)

    store.close()
    _progress(100, f"Analysis complete (parse: {parse_elapsed:.1f}s, resolve: {resolve_elapsed:.1f}s, graph: {graph_elapsed:.1f}s)")

    return stats


def _parse_file(sf) -> ParsedFile:
    """Parse a source file, routing to the correct language extractor."""
    if sf.lang == "java":
        return parse_java(sf.relative, sf.content)
    elif sf.lang == "js":
        return parse_js(sf.relative, sf.content)
    elif sf.lang == "ts":
        return parse_ts(sf.relative, sf.content)
    elif sf.lang == "vue":
        return parse_vue(sf.relative, sf.content)
    elif sf.lang == "html":
        return parse_html(sf.relative, sf.content)
    elif sf.lang == "xml":
        return parse_xml(sf.relative, sf.content)
    else:
        raise ValueError(f"Unknown language: {sf.lang}")


def _parse_concurrent(
    source_files: list,
    progress_callback,
    total: int,
) -> list[ParsedFile]:
    """Parse source files concurrently using ThreadPoolExecutor (tree-sitter releases GIL)."""
    parsed: list[ParsedFile] = []
    import threading
    lock = threading.Lock()
    done = 0
    last_reported = 0

    max_workers = min(8, os.cpu_count() or 8)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_parse_file, sf): sf
            for sf in source_files
        }
        for future in as_completed(futures):
            sf = futures[future]
            try:
                pf = future.result()
                with lock:
                    parsed.append(pf)
            except Exception as e:
                progress_callback(
                    15 + int(40 * (done + 1) / max(total, 1)),
                    f"Skipping {sf.relative}: {e}",
                )
            done += 1
            # Only report every 10% progress to reduce output noise
            pct = 15 + int(40 * done / max(total, 1))
            if pct - last_reported >= 10:
                progress_callback(pct, f"Parsed {done}/{total} files")
                last_reported = pct

    # Sort by file_path for deterministic output
    parsed.sort(key=lambda pf: pf.file_path)
    return parsed


def _resolve_calls_parallel(
    parsed_files: list[ParsedFile],
    class_map: dict[str, str],
) -> list[tuple]:
    """Resolve call targets in parallel using ThreadPoolExecutor."""
    from ..core.resolver import resolve_calls_chunk_with_indices

    max_workers = min(4, os.cpu_count() or 4)
    chunk_size = max(1, len(parsed_files) // max_workers)
    chunks = [parsed_files[i:i + chunk_size] for i in range(0, len(parsed_files), chunk_size)]

    results: list[tuple] = []
    import threading
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(resolve_calls_chunk_with_indices, chunk, parsed_files, class_map): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            chunk_results = future.result()
            with lock:
                results.extend(chunk_results)

    return results


def _write_to_graph_batched(
    store: GraphStore,
    parsed_files: list[ParsedFile],
    source_files,  # list[SourceFile]
    repo_path: Path,
    call_relations: list[tuple],
    class_map: dict[str, str],
    spring_endpoint_map: dict[str, list[tuple[str, str, str]]] | None = None,
) -> None:
    """Write all parsed data to the graph database using batched UNWIND."""
    _t0 = time.monotonic()
    def _step(msg: str) -> None:
        elapsed = time.monotonic() - _t0
        print(f"  [graph] {elapsed:.1f}s: {msg}")

    with BatchWriter(store) as bw:
        # 1. Write folders
        _step("Starting folders...")
        seen_folders: set[str] = set()
        folders: list[dict] = []
        folder_contains: list[dict] = []
        for sf in source_files:
            folder = str(Path(sf.relative).parent)
            if folder and folder != "." and folder not in seen_folders:
                seen_folders.add(folder)
                folder_id = _make_folder_id(folder)
                folders.append({
                    "id": folder_id,
                    "name": Path(folder).name,
                    "filePath": folder,
                })
                parent = str(Path(folder).parent)
                if parent and parent != ".":
                    parent_id = _make_folder_id(parent)
                    folder_contains.append({
                        "from_id": folder_id,
                        "to_id": parent_id,
                    })
        bw.insert_nodes("Folder", folders)
        bw.insert_relations("Folder", "Folder", folder_contains, "CONTAINS", 1.0, "directory structure")

        # 2. Write files
        file_nodes: list[dict] = []
        file_contains: list[dict] = []
        for sf in source_files:
            file_id = _make_file_id(sf.relative)
            file_nodes.append({
                "id": file_id,
                "name": Path(sf.relative).name,
                "filePath": sf.relative,
                "content": sf.text[:10000],
            })
            folder = str(Path(sf.relative).parent)
            if folder and folder != ".":
                folder_id = _make_folder_id(folder)
                file_contains.append({
                    "from_id": folder_id,
                    "to_id": file_id,
                })
        bw.insert_nodes("File", file_nodes)
        bw.insert_relations("Folder", "File", file_contains, "CONTAINS", 1.0, "directory structure")
        _step("Files done")

        # Pre-build method/field/constructor/class lookup maps for relation writing
        method_ids: dict[tuple[str, str], str] = {}
        field_ids: dict[tuple[str, str], str] = {}
        constructor_ids: dict[tuple[str, str], str] = {}
        class_ids: dict[tuple[str, str], str] = {}  # (class_simple_name, file_path) -> class_node_id

        # First pass: generate unique IDs with dedup, store in method_ids for lookup AND node writing
        _seen_method_ids: set[str] = set()
        _method_object_to_id: dict[int, str] = {}  # id(method) -> final node ID
        for pf in parsed_files:
            for method in pf.methods:
                mid = _make_method_id(
                    f"{method.class_name}.{method.name}" if method.class_name else method.name,
                    method.file_path,
                    method.start_line,
                )
                # Handle duplicate IDs (minified JS with many same-name functions on same line)
                if mid in _seen_method_ids:
                    # Append global counter to ensure uniqueness
                    mid = f"{mid}_m{len(_seen_method_ids)}"
                _seen_method_ids.add(mid)
                method_ids[(method.name, method.file_path)] = mid
                if method.class_name:
                    method_ids[(f"{method.class_name}.{method.name}", method.file_path)] = mid
                # Also store by object identity so the node-writing pass can reuse the same ID
                _method_object_to_id[id(method)] = mid
            for field in pf.fields:
                fid = _make_field_id(field.name, field.file_path, field.start_line)
                field_ids[(field.name, field.file_path)] = fid
                if field.class_name:
                    field_ids[(f"{field.class_name}.{field.name}", field.file_path)] = fid
            for ctor in pf.constructors:
                cid = _make_constructor_id(ctor.class_name, ctor.name, ctor.file_path, ctor.start_line)
                constructor_ids[(ctor.name, ctor.file_path)] = cid
                if ctor.class_name:
                    constructor_ids[(f"{ctor.class_name}.{ctor.name}", ctor.file_path)] = cid
            for cls in pf.classes:
                cls_id = _make_class_id(cls.name, pf.file_path, cls.start_line, cls.is_interface)
                class_ids[(cls.name, pf.file_path)] = cls_id

        # 3. Write classes, interfaces, and their relations
        class_nodes: list[dict] = []
        interface_nodes: list[dict] = []
        class_defines: list[dict] = []
        has_methods: list[dict] = []
        has_properties: list[dict] = []
        extends_rels: list[dict] = []
        implements_rels: list[dict] = []
        method_defines: list[dict] = []
        field_defines: list[dict] = []

        _seen_class_ids: set[str] = set()
        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            for cls in pf.classes:
                node_id = _make_class_id(cls.name, pf.file_path, cls.start_line, cls.is_interface)
                if node_id in _seen_class_ids:
                    node_id = f"{node_id}_n{len(_seen_class_ids)}"
                _seen_class_ids.add(node_id)
                class_defines.append({
                    "from_id": file_id,
                    "to_id": node_id,
                })
                if cls.is_interface:
                    interface_nodes.append({
                        "id": node_id,
                        "name": cls.name,
                        "filePath": pf.file_path,
                        "startLine": cls.start_line,
                        "endLine": cls.end_line,
                        "isPublic": cls.is_public,
                        "content": cls.content[:5000],
                    })
                else:
                    class_nodes.append({
                        "id": node_id,
                        "name": cls.name,
                        "filePath": pf.file_path,
                        "startLine": cls.start_line,
                        "endLine": cls.end_line,
                        "isPublic": cls.is_public,
                        "isAbstract": cls.is_abstract,
                        "isInterface": cls.is_interface,
                        "content": cls.content[:5000],
                    })

                # HAS_METHOD
                table = "Interface" if cls.is_interface else "Class"
                cls_simple_name = cls.name.split(".")[-1]
                for method in pf.methods:
                    if method.class_name and (
                        method.class_name == cls.name
                        or method.class_name == cls_simple_name
                    ):
                        method_id = method_ids.get(
                            (f"{method.class_name}.{method.name}", method.file_path)
                        )
                        if not method_id:
                            # Also try with the FQN class name
                            method_id = method_ids.get(
                                (f"{cls.name}.{method.name}", method.file_path)
                            )
                        if method_id:
                            has_methods.append({
                                "from_id": node_id,
                                "to_id": method_id,
                            })

                # HAS_PROPERTY
                for field in pf.fields:
                    if field.class_name and (
                        field.class_name == cls.name
                        or field.class_name == cls_simple_name
                    ):
                        field_id = field_ids.get(
                            (f"{field.class_name}.{field.name}", field.file_path)
                        )
                        if not field_id:
                            field_id = field_ids.get(
                                (f"{cls.name}.{field.name}", field.file_path)
                            )
                        if field_id:
                            has_properties.append({
                                "from_id": node_id,
                                "to_id": field_id,
                            })

            # Methods and fields for this file — use pre-computed IDs from first pass
            _seen_method_ids_nodes: set[str] = set()
            for method in pf.methods:
                # Look up the already-deduplicated ID from first pass (single source of truth)
                method_id = _method_object_to_id.get(id(method))
                if method_id is None:
                    # Fallback: should not happen, but generate ID if missing
                    method_id = _make_method_id(
                        f"{method.class_name}.{method.name}" if method.class_name else method.name,
                        method.file_path,
                        method.start_line,
                    )
                    if method_id in _seen_method_ids_nodes:
                        method_id = f"{method_id}_n{len(_seen_method_ids_nodes)}"
                    _seen_method_ids_nodes.add(method_id)
                method_data: dict = {
                    "id": method_id,
                    "name": method.name,
                    "className": method.class_name or "",
                    "filePath": method.file_path,
                    "startLine": method.start_line,
                    "endLine": method.end_line,
                    "returnType": method.return_type,
                    "parameterCount": len(method.parameters),
                    "isStatic": method.is_static,
                    "isPublic": method.is_public,
                    "isConstructor": method.is_constructor,
                    "content": method.content[:5000],
                    "from_id": file_id,
                }
                # Add HTTP endpoint info for Spring controller methods (kept for backward compat)
                file_endpoints = spring_endpoint_map.get(pf.file_path, []) if spring_endpoint_map else []
                # Find first matching endpoint for this method (there may be multiple)
                method_http = None
                for mn, hm, hp in file_endpoints:
                    if mn == method.name:
                        method_http = (hm, hp)
                        break
                if method_http:
                    http_method, http_path = method_http
                    method_data["httpMethod"] = http_method
                    method_data["httpPath"] = http_path
                    method_data["httpParams"] = ""
                else:
                    # Ensure all rows have the same keys for CSV COPY
                    method_data["httpMethod"] = ""
                    method_data["httpPath"] = ""
                    method_data["httpParams"] = ""
                method_defines.append(method_data)

            # 5. Write fields — deduplicate by ID
            _seen_field_ids: set[str] = set()
            for field in pf.fields:
                field_id = _make_field_id(field.name, field.file_path, field.start_line)
                if field_id in _seen_field_ids:
                    field_id = f"{field_id}_n{len(_seen_field_ids)}"
                _seen_field_ids.add(field_id)
                field_defines.append({
                    "id": field_id,
                    "name": field.name,
                    "typeName": field.type_name,
                    "className": field.class_name,
                    "filePath": field.file_path,
                    "startLine": field.start_line,
                    "endLine": field.end_line,
                    "isStatic": field.is_static,
                    "isPublic": field.is_public,
                    "from_id": file_id,
                })

        bw.insert_nodes("Class", class_nodes)
        bw.insert_nodes("Interface", interface_nodes)
        bw.insert_file_relations("Class", class_defines, "DEFINES")
        bw.insert_file_relations("Interface", class_defines, "DEFINES")
        _step("Classes done")

        # Build class/interface ID lookup for EXTENDS/IMPLEMENTS/IMPORTS
        class_id_lookup: dict[str, str] = {}
        for node in class_nodes:
            class_id_lookup[node["name"]] = node["id"]
        for node in interface_nodes:
            class_id_lookup[node["name"]] = node["id"]

        # Build simple name → class_id index for fast annotation/constructor lookups
        class_simple_lookup: dict[str, str] = {}
        for fqn, nid in class_id_lookup.items():
            class_simple_lookup[fqn.split(".")[-1]] = nid

        # EXTENDS (second pass after lookup is available)
        extends_by_pair: dict[tuple[str, str], list[dict]] = {}
        for pf in parsed_files:
            for cls in pf.classes:
                if not cls.extends:
                    continue
                node_id = _make_class_id(cls.name, pf.file_path, cls.start_line, cls.is_interface)
                parent_id = class_id_lookup.get(cls.extends)
                if not parent_id:
                    continue
                from_table = "Interface" if cls.is_interface else "Class"
                to_table = "Interface" if parent_id.startswith("Interface_") else "Class"
                extends_by_pair.setdefault((from_table, to_table), []).append({
                    "from_id": node_id,
                    "to_id": parent_id,
                })
        for (from_t, to_t), rels in extends_by_pair.items():
            bw.insert_relations(from_t, to_t, rels, "EXTENDS", 0.8, "class extends")

        # IMPLEMENTS
        impl_rels: list[dict] = []
        # Build simple name → node_id lookup for implements matching
        simple_name_lookup: dict[str, str] = {}
        for name, node_id in class_id_lookup.items():
            simple_name_lookup[name.split(".")[-1]] = node_id

        for pf in parsed_files:
            for cls in pf.classes:
                if cls.is_interface:
                    continue
                node_id = _make_class_id(cls.name, pf.file_path, cls.start_line, cls.is_interface)
                for iface in cls.implements:
                    # Try exact FQN first, then simple name
                    iface_id = class_id_lookup.get(iface) or simple_name_lookup.get(iface)
                    if iface_id:
                        impl_rels.append({
                            "from_id": node_id,
                            "to_id": iface_id,
                        })
        bw.insert_relations("Class", "Interface", impl_rels, "IMPLEMENTS", 0.8, "class implements")
        _step("Relations done")

        # Insert Method and Field nodes FIRST (needed before HAS_METHOD/HAS_PROPERTY relations)
        bw.insert_nodes_with_defines("Method", method_defines, "DEFINES")
        bw.insert_nodes_with_defines("Field", field_defines, "DEFINES")
        _step("Methods and Fields done")

        # HAS_PROPERTY (requires Class/Interface AND Field to exist)
        bw.insert_typed_relations("Class", "Field", has_properties, "HAS_PROPERTY", 1.0, "class has property")
        bw.insert_typed_relations("Interface", "Field", has_properties, "HAS_PROPERTY", 1.0, "interface has property")

        # HAS_METHOD (requires Class/Interface AND Method to exist)
        bw.insert_typed_relations("Class", "Method", has_methods, "HAS_METHOD", 1.0, "class has method")
        bw.insert_typed_relations("Interface", "Method", has_methods, "HAS_METHOD", 1.0, "interface has method")

        # HAS_SETTER / HAS_GETTER: Field → Method (by JavaBean naming convention)
        has_setters: list[dict] = []
        has_getters: list[dict] = []
        for pf in parsed_files:
            for field in pf.fields:
                prop = field.name
                if not prop or not field.class_name:
                    continue
                # Build expected setter/getter names
                cap = prop[0].upper() + prop[1:] if len(prop) > 1 else prop.upper()
                setter_name = f"set{cap}"
                getter_name = f"get{cap}"
                # Look up field ID and matching method IDs
                fid = field_ids.get((prop, field.file_path))
                if not fid:
                    fid = field_ids.get((f"{field.class_name}.{prop}", field.file_path))
                if not fid:
                    continue
                for method in pf.methods:
                    if method.class_name != field.class_name or not method.class_name:
                        continue
                    key = (f"{method.class_name}.{method.name}", method.file_path)
                    mid = method_ids.get(key)
                    if not mid:
                        continue
                    if method.name == setter_name:
                        has_setters.append({"from_id": fid, "to_id": mid})
                    elif method.name == getter_name:
                        has_getters.append({"from_id": fid, "to_id": mid})
                    # Boolean getter: isXxx
                    elif prop.startswith("is") and len(prop) > 2:
                        is_getter = f"is{prop[0].upper()}{prop[1:]}" if prop[1:] != prop[1:].lower() else f"is{prop}"
                        if method.name == is_getter or method.name == f"is{cap}":
                            has_getters.append({"from_id": fid, "to_id": mid})
        if has_setters:
            bw.insert_typed_relations(
                "Field", "Method", has_setters, "HAS_SETTER", 1.0,
                "field has setter method",
            )
        if has_getters:
            bw.insert_typed_relations(
                "Field", "Method", has_getters, "HAS_GETTER", 1.0,
                "field has getter method",
            )

        # Insert Constructor nodes — deduplicate by ID
        _seen_ctor_ids: set[str] = set()
        ctor_defines: list[dict] = []
        has_constructors: list[dict] = []
        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            for ctor in pf.constructors:
                cid = _make_constructor_id(ctor.class_name, ctor.name, ctor.file_path, ctor.start_line)
                if cid in _seen_ctor_ids:
                    cid = f"{cid}_n{len(_seen_ctor_ids)}"
                _seen_ctor_ids.add(cid)
                ctor_defines.append({
                    "id": cid,
                    "name": ctor.name,
                    "className": ctor.class_name,
                    "filePath": ctor.file_path,
                    "startLine": ctor.start_line,
                    "endLine": ctor.end_line,
                    "parameterCount": len(ctor.parameters),
                    "isPublic": ctor.is_public,
                    "content": ctor.content[:5000],
                    "from_id": file_id,
                })
                # HAS_CONSTRUCTOR relation
                cls_node_id = class_simple_lookup.get(ctor.class_name)
                if cls_node_id and cls_node_id.startswith("Class_"):
                    has_constructors.append({"from_id": cls_node_id, "to_id": cid})

        bw.insert_nodes_with_defines("Constructor", ctor_defines, "DEFINES")
        if has_constructors:
            bw.insert_relations("Class", "Constructor", has_constructors, "HAS_CONSTRUCTOR", 1.0, "class has constructor")
        _step("Constructors done")

        # Insert Variable nodes
        var_defines: list[dict] = []
        var_counter: dict[str, int] = {}
        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            for var in pf.variables:
                base_key = f"{var.file_path or pf.file_path}:{var.name}:{var.line}"
                idx = var_counter.get(base_key, 0)
                var_counter[base_key] = idx + 1
                vid = _make_variable_id(var.name, var.file_path or pf.file_path, var.line, idx)
                var_defines.append({
                    "id": vid,
                    "name": var.name,
                    "typeName": var.type_name,
                    "methodName": var.method_name,
                    "className": var.class_name,
                    "filePath": var.file_path or pf.file_path,
                    "line": var.line,
                    "isFinal": var.is_final,
                    "from_id": file_id,
                })
        bw.insert_nodes_with_defines("Variable", var_defines, "DEFINES")

        # Insert Annotation nodes
        ann_defines: list[dict] = []
        import json as _json
        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            ann_counter = 0
            for ann in pf.annotations:
                aid = _make_annotation_id(ann.name, ann.target_name, ann.file_path, ann.line, ann_counter)
                ann_counter += 1
                ann_defines.append({
                    "id": aid,
                    "name": ann.name,
                    "targetType": ann.target_type,
                    "targetName": ann.target_name,
                    "filePath": ann.file_path,
                    "line": ann.line,
                    "attributes": _json.dumps(ann.attributes),
                    "from_id": file_id,
                })
        bw.insert_nodes_with_defines("Annotation", ann_defines, "DEFINES")

        # HAS_ANNOTATION relations
        has_annotations_cls: list[dict] = []
        has_annotations_method: list[dict] = []
        has_annotations_field: list[dict] = []
        for pf in parsed_files:
            ann_counter = 0
            for ann in pf.annotations:
                aid = _make_annotation_id(ann.name, ann.target_name, ann.file_path, ann.line, ann_counter)
                ann_counter += 1
                if ann.target_type == "class":
                    cls_id = class_simple_lookup.get(ann.target_name)
                    if cls_id:
                        has_annotations_cls.append({"from_id": cls_id, "to_id": aid})
                elif ann.target_type == "method":
                    method_id = method_ids.get((ann.target_name, ann.file_path))
                    if method_id:
                        has_annotations_method.append({"from_id": method_id, "to_id": aid})
                elif ann.target_type == "field":
                    field_id = field_ids.get((ann.target_name, ann.file_path))
                    if field_id:
                        has_annotations_field.append({"from_id": field_id, "to_id": aid})
        if has_annotations_cls:
            bw.insert_relations("Class", "Annotation", has_annotations_cls, "HAS_ANNOTATION", 1.0, "class has annotation")
        if has_annotations_method:
            bw.insert_relations("Method", "Annotation", has_annotations_method, "HAS_ANNOTATION", 1.0, "method has annotation")
        if has_annotations_field:
            bw.insert_relations("Field", "Annotation", has_annotations_field, "HAS_ANNOTATION", 1.0, "field has annotation")
        _step("Annotations done")

        # Insert TypeAlias nodes (TS only)
        typealias_defines: list[dict] = []
        for pf in parsed_files:
            if not pf.type_aliases:
                continue
            file_id = _make_file_id(pf.file_path)
            for ta in pf.type_aliases:
                tid = _make_type_alias_id(ta.name, pf.file_path, ta.start_line)
                typealias_defines.append({
                    "id": tid,
                    "name": ta.name,
                    "filePath": pf.file_path,
                    "startLine": ta.start_line,
                    "endLine": ta.end_line,
                    "content": ta.content[:5000] if ta.content else "",
                    "from_id": file_id,
                })
        bw.insert_nodes_with_defines("TypeAlias", typealias_defines, "DEFINES")

        # Insert Enum nodes (TS only)
        enum_defines: list[dict] = []
        for pf in parsed_files:
            if not pf.enums:
                continue
            file_id = _make_file_id(pf.file_path)
            for en in pf.enums:
                eid = _make_enum_id(en.name, pf.file_path, en.start_line)
                enum_defines.append({
                    "id": eid,
                    "name": en.name,
                    "filePath": pf.file_path,
                    "startLine": en.start_line,
                    "endLine": en.end_line,
                    "isConst": en.is_const,
                    "content": en.content[:5000] if en.content else "",
                    "from_id": file_id,
                })
        bw.insert_nodes_with_defines("Enum", enum_defines, "DEFINES")
        _step("TypeAlias and Enum done")

        # 5b. Write API nodes and EXPOSES relations (Method → API)
        # Each Spring endpoint annotation creates an API node.
        # Multiple methods can expose the same API, but each API node is unique by (httpMethod, httpPath).
        _step("Building API nodes...")
        api_nodes: list[dict] = []
        exposes_rels: list[dict] = []
        seen_api_ids: set[str] = set()
        # (http_method, http_path) → api_id for USES_ENDPOINT matching
        api_lookup: dict[tuple[str, str], str] = {}
        # Track first method for each API (for className/methodName on API node)
        api_to_method: dict[str, tuple[str, str]] = {}  # api_id → (className, methodName)

        if spring_endpoint_map:
            for file_path, endpoints in spring_endpoint_map.items():
                for method_name, http_method, http_path in endpoints:
                    api_id = f"api:{http_method}:{http_path}"
                    if api_id not in seen_api_ids:
                        seen_api_ids.add(api_id)
                        api_nodes.append({
                            "id": api_id,
                            "httpMethod": http_method,
                            "httpPath": http_path,
                            "httpParams": "",
                            "className": "",
                            "methodName": "",
                            "filePath": file_path,
                        })
                        api_lookup[(http_method, http_path)] = api_id
                        api_to_method[api_id] = ("", "")
                    # EXPOSES: Method → API
                    mid = method_ids.get((method_name, file_path))
                    if mid:
                        exposes_rels.append({"from_id": mid, "to_id": api_id, "confidence": 1.0})
                        # Track the first method for this API
                        if api_to_method[api_id][0] == "":
                            full_name = method_name
                            for pf in parsed_files:
                                for m in pf.methods:
                                    if m.name == method_name and m.file_path == file_path:
                                        full_name = f"{m.class_name}.{method_name}" if m.class_name else method_name
                                        break
                            api_to_method[api_id] = (full_name.rsplit(".", 1)[0], full_name.rsplit(".", 1)[-1] if "." in full_name else full_name)

        # Update API nodes with className/methodName from first exposed method
        for api in api_nodes:
            api_id = api["id"]
            cn, mn = api_to_method.get(api_id, ("", ""))
            api["className"] = cn
            api["methodName"] = mn

        if api_nodes:
            store.bulk_copy_nodes("API", api_nodes)
        if exposes_rels:
            store.bulk_copy_relations(
                "Method", "API", exposes_rels, "EXPOSES",
                "controller method exposes HTTP endpoint",
                extra_columns=["httpMethod", "httpPath", "httpParams"],
            )
        _step(f"API nodes done ({len(api_nodes)} APIs, {len(exposes_rels)} EXPOSES)")

        # 6. Write CALLS relations (deduplicate by caller_id + target_id)
        _step("Starting CALLS relations...")

        # Caller can be Method or Constructor; target can be Method, Constructor, or Class
        calls_by_tables: dict[tuple[str, str], list[dict]] = {}
        seen_calls: set[tuple[str, str]] = set()
        for item in call_relations:
            # Handle both 5-tuple (Java: caller, file, target, file, confidence)
            # and 8-tuple (JS/TS: + http_method, http_path, http_params)
            caller_name, caller_file, target_name, target_file, confidence = item[:5]
            http_method = item[5] if len(item) > 5 else None
            http_path = item[6] if len(item) > 6 else None
            http_params = item[7] if len(item) > 7 else None

            # Resolve caller ID: try method first, then constructor
            caller_id = method_ids.get((caller_name, caller_file))
            caller_table = "Method"
            if not caller_id:
                caller_id = constructor_ids.get((caller_name, caller_file))
                caller_table = "Constructor"
            if not caller_id:
                continue

            # Resolve target ID: try method first, then constructor, then class
            target_id = method_ids.get((target_name, target_file))
            target_table = "Method"
            if not target_id:
                target_id = constructor_ids.get((target_name, target_file))
                target_table = "Constructor"
            if not target_id:
                target_id = class_ids.get((target_name, target_file))
                target_table = "Class"
            if not target_id:
                continue

            pair = (caller_id, target_id)
            if pair not in seen_calls:
                seen_calls.add(pair)
                rel_data: dict = {
                    "from_id": caller_id,
                    "to_id": target_id,
                    "confidence": confidence,
                }
                if http_method:
                    rel_data["httpMethod"] = http_method
                if http_path:
                    rel_data["httpPath"] = http_path
                if http_params:
                    rel_data["httpParams"] = http_params
                calls_by_tables.setdefault((caller_table, target_table), []).append(rel_data)
        for (from_table, to_table), data in calls_by_tables.items():
            # Always include all HTTP columns since schema has them
            store.bulk_copy_relations(
                from_table, to_table, data, "CALLS", "method invocation",
                extra_columns=["httpMethod", "httpPath", "httpParams"],
            )
        _step("CALLS done")

        # 6b. Build USES_ENDPOINT relations: frontend methods → API nodes
        # by matching HTTP method + path from JS/TS CALLS to API nodes
        _step("Building USES_ENDPOINT relations (frontend → API)...")
        uses_endpoint_rels: list[dict] = []
        seen_uses: set[tuple[str, str]] = set()

        # Scan JS/TS CALLS relations for HTTP endpoint info
        for item in call_relations:
            if len(item) < 6:
                continue
            caller, caller_file, target, target_file, confidence = item[:5]
            http_method = item[5] if len(item) > 5 else None
            http_path = item[6] if len(item) > 6 else None
            http_params = item[7] if len(item) > 7 else None

            if not http_method or not http_path:
                continue

            # Only process frontend (JS/TS) files
            if not _is_js_ts_file(caller_file):
                continue

            # Look up matching API node by (http_method, http_path)
            api_id = api_lookup.get((http_method, http_path))
            if not api_id:
                continue

            # Get caller method ID
            caller_id = method_ids.get((caller, caller_file))
            if not caller_id:
                continue

            pair = (caller_id, api_id)
            if pair not in seen_uses:
                seen_uses.add(pair)
                uses_endpoint_rels.append({
                    "from_id": caller_id,
                    "to_id": api_id,
                    "confidence": 0.85,
                    "httpMethod": http_method or "",
                    "httpPath": http_path or "",
                    "httpParams": http_params or "",
                })

        if uses_endpoint_rels:
            store.bulk_copy_relations(
                "Method", "API", uses_endpoint_rels, "USES_ENDPOINT",
                "frontend method uses backend API endpoint",
                extra_columns=["httpMethod", "httpPath", "httpParams"],
            )
        _step(f"USES_ENDPOINT done ({len(uses_endpoint_rels)} relations)")

        # 7. Write ACCESSES relations (Method/Constructor → Field)
        accesses_data: list[dict] = []
        seen_accesses: set[tuple[str, str]] = set()
        for pf in parsed_files:
            for access in pf.field_accesses:
                # Look up the method or constructor ID
                accessor_id = method_ids.get((access.method_name, access.file_path))
                if not accessor_id:
                    # Try constructor lookup
                    accessor_id = constructor_ids.get((access.method_name, access.file_path))
                if not accessor_id:
                    continue

                # Look up the field ID
                field_id = field_ids.get((access.field_name, access.file_path))
                if not field_id:
                    # Try with class.field pattern
                    if access.class_name:
                        field_id = field_ids.get((f"{access.class_name}.{access.field_name}", access.file_path))
                if not accessor_id or not field_id:
                    continue

                pair = (accessor_id, field_id)
                if pair not in seen_accesses:
                    seen_accesses.add(pair)
                    accesses_data.append({
                        "from_id": accessor_id,
                        "to_id": field_id,
                        "isWrite": access.is_write,
                    })
        if accesses_data:
            bw.insert_typed_relations("Method", "Field", accesses_data, "ACCESSES", 1.0, "field access")
            bw.insert_typed_relations("Constructor", "Field", accesses_data, "ACCESSES", 1.0, "field access")

        # 8. Write IMPORTS relations
        # Build class/interface ID lookup from actual written nodes
        class_id_lookup: dict[str, str] = {}
        for node in class_nodes:
            class_id_lookup[node["name"]] = node["id"]
        for node in interface_nodes:
            class_id_lookup[node["name"]] = node["id"]

        imports_data: list[dict] = []
        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            for imp in pf.imports:
                target_class = imp.qualified_name
                target_id = class_id_lookup.get(target_class)
                if target_id:
                    imports_data.append({
                        "from_id": file_id,
                        "to_id": target_id,
                    })
        bw.insert_relations("File", "Class", imports_data, "IMPORTS", 1.0, "import relation")
        bw.insert_relations("File", "Interface", imports_data, "IMPORTS", 1.0, "import relation")


def _to_setter_name(java_prop: str) -> str:
    """Convert a JavaBean property name to its setter method name."""
    if not java_prop:
        return ""
    first = java_prop[0].upper()
    rest = java_prop[1:] if len(java_prop) > 1 else ""
    return f"set{first}{rest}"


def _to_getter_name(java_prop: str, is_boolean: bool = False) -> str:
    """Convert a JavaBean property name to its getter method name."""
    if not java_prop:
        return ""
    first = java_prop[0].upper()
    rest = java_prop[1:] if len(java_prop) > 1 else ""
    prefix = "is" if is_boolean else "get"
    return f"{prefix}{first}{rest}"


def _write_mybatis_relations(
    store: GraphStore,
    source_files,
    parsed_files: list,
) -> None:
    """Parse MyBatis mapper XMLs and create MAPS_TO edges.
    """
    from .mybatis_parser import parse_mapper_xml

    # === MyBatis MAPS_TO edges (Mapper.method → Entity/Field/setter) ===
    xml_files = [sf for sf in source_files if sf.lang == "xml"]
    if not xml_files:
        return

    mappers: list = []
    for sf in xml_files:
        info = parse_mapper_xml(sf.path)
        if info:
            info.source_file = sf.relative
            mappers.append(info)

    if not mappers:
        return

    # For each mapper, look up the interface node by namespace
    maps_to_rels: list[dict] = []  # from_id=Method, to_id=Class/Field/Method(setter)

    for mp in mappers:
        namespace = mp.namespace
        mapper_simple = namespace.rsplit(".", 1)[-1]

        # Find the Mapper interface by FQN
        iface_rows = store.query(
            "MATCH (n:Interface) WHERE n.name = $name RETURN n.id AS id LIMIT 1",
            {"name": namespace},
        )
        if not iface_rows:
            iface_rows = store.query(
                "MATCH (n:Interface) WHERE n.name CONTAINS $name RETURN n.id AS id, n.name AS nname LIMIT 1",
                {"name": mapper_simple},
            )
        if not iface_rows:
            continue

        mapper_iface_id = iface_rows[0]["id"]

        # Build resultMap lookup: id -> entity_fqn + properties
        rm_lookup: dict[str, dict] = {}
        for rm in mp.result_maps:
            rm_lookup[rm.id] = {"entity_fqn": rm.entity_fqn, "props": rm.properties}

        for stmt in mp.statements:
            method_name = stmt.id

            # Find the method on this Mapper interface
            method_rows = store.query(
                "MATCH (n:Method) WHERE n.name = $name AND n.className = $cls RETURN n.id AS id LIMIT 1",
                {"name": method_name, "cls": mapper_simple},
            )
            if not method_rows:
                continue

            method_id = method_rows[0]["id"]

            # Get entity from resultMap
            entity_fqn = None
            rm_data = None
            if stmt.result_map and stmt.result_map in rm_lookup:
                rm_data = rm_lookup[stmt.result_map]
                entity_fqn = rm_data["entity_fqn"]
            elif stmt.parameter_type and "." in stmt.parameter_type:
                entity_fqn = stmt.parameter_type

            if not entity_fqn:
                continue

            entity_simple = entity_fqn.rsplit(".", 1)[-1]

            # Find the Entity class
            entity_rows = store.query(
                "MATCH (n:Class) WHERE n.name = $name RETURN n.id AS id LIMIT 1",
                {"name": entity_fqn},
            )
            if not entity_rows:
                entity_rows = store.query(
                    "MATCH (n:Class) WHERE n.name CONTAINS $name RETURN n.id AS id, n.name AS nname LIMIT 1",
                    {"name": entity_simple},
                )
            if entity_rows:
                entity_id = entity_rows[0]["id"]
                maps_to_rels.append({
                    "from_id": method_id,
                    "to_id": entity_id,
                    "confidence": 0.95,
                })

            # Get field-level mappings and create MAPS_TO to setter methods
            if rm_data:
                for prop in rm_data["props"]:
                    java_prop = prop.property
                    if not java_prop:
                        continue

                    # Find the Field node
                    field_rows = store.query(
                        "MATCH (n:Field) WHERE n.name = $name AND n.className = $cls RETURN n.id AS id LIMIT 1",
                        {"name": java_prop, "cls": entity_simple},
                    )
                    if not field_rows:
                        field_rows = store.query(
                            "MATCH (n:Field) WHERE n.name = $name AND n.className CONTAINS $cls "
                            "RETURN n.id AS id LIMIT 1",
                            {"name": java_prop, "cls": entity_simple},
                        )
                    if field_rows:
                        maps_to_rels.append({
                            "from_id": method_id,
                            "to_id": field_rows[0]["id"],
                            "confidence": 0.9,
                        })

                    # Find the setter Method node and create MAPS_TO
                    setter_name = _to_setter_name(java_prop)
                    setter_rows = store.query(
                        "MATCH (n:Method) WHERE n.name = $name AND n.className = $cls RETURN n.id AS id LIMIT 1",
                        {"name": setter_name, "cls": entity_simple},
                    )
                    if not setter_rows:
                        setter_rows = store.query(
                            "MATCH (n:Method) WHERE n.name = $name AND n.className CONTAINS $cls "
                            "RETURN n.id AS id LIMIT 1",
                            {"name": setter_name, "cls": entity_simple},
                        )
                    if setter_rows:
                        maps_to_rels.append({
                            "from_id": method_id,
                            "to_id": setter_rows[0]["id"],
                            "confidence": 0.9,
                        })

    # Write MAPS_TO relations
    if maps_to_rels:
        seen: set = set()
        deduped: list[dict] = []
        for r in maps_to_rels:
            pair = (r["from_id"], r["to_id"])
            if pair not in seen:
                seen.add(pair)
                r["httpMethod"] = ""
                r["httpPath"] = ""
                r["httpParams"] = ""
                deduped.append(r)

        # Write to Method→Method, Method→Class, Method→Field separately
        method_to_method = [r for r in deduped if r["to_id"].startswith("Method_")]
        if method_to_method:
            store.bulk_copy_relations(
                "Method", "Method", method_to_method, "MAPS_TO",
                "MyBatis mapper method maps to entity method/field",
                extra_columns=["httpMethod", "httpPath", "httpParams"],
            )
        method_to_class = [r for r in deduped if r["to_id"].startswith("Class_")]
        if method_to_class:
            store.bulk_copy_relations(
                "Method", "Class", method_to_class, "MAPS_TO",
                "MyBatis mapper method maps to entity class",
                extra_columns=["httpMethod", "httpPath", "httpParams"],
            )
        method_to_field = [r for r in deduped if r["to_id"].startswith("Field_")]
        if method_to_field:
            store.bulk_copy_relations(
                "Method", "Field", method_to_field, "MAPS_TO",
                "MyBatis mapper method maps to entity field",
                extra_columns=["httpMethod", "httpPath", "httpParams"],
            )


class BatchWriter:
    """Context manager for batched graph writes with periodic heartbeat."""

    BATCH_SIZE = 500
    HEARTBEAT_INTERVAL = 30  # seconds

    def __init__(self, store: GraphStore) -> None:
        self._store = store
        self._done = 0
        self._t0 = time.monotonic()
        self._last_heartbeat = 0.0

    def __enter__(self) -> "BatchWriter":
        return self

    def __exit__(self, *args) -> None:
        # Print final summary
        elapsed = time.monotonic() - self._t0
        print(f"  [graph] {self._done} batches in {elapsed:.1f}s")

    def _heartbeat(self) -> None:
        """Print heartbeat if 30+ seconds have passed since last one."""
        elapsed = time.monotonic() - self._t0
        if elapsed - self._last_heartbeat >= self.HEARTBEAT_INTERVAL:
            print(f"  [graph] still writing... {self._done} batches done ({elapsed:.0f}s)")
            self._last_heartbeat = elapsed

    def _advance(self) -> None:
        """Mark one batch as completed."""
        self._done += 1
        self._heartbeat()

    def insert_nodes(self, table: str, nodes: list[dict]) -> None:
        if not nodes:
            return
        for batch in _chunked(nodes, self.BATCH_SIZE):
            self._store.bulk_unwind_insert(table, batch)
            self._advance()

    def insert_relations(
        self,
        from_table: str,
        to_table: str,
        relations: list[dict],
        rel_type: str,
        confidence: float,
        reason: str,
    ) -> None:
        if not relations:
            return
        for batch in _chunked(relations, self.BATCH_SIZE):
            self._store.bulk_unwind_relation(
                from_table, to_table, batch, rel_type, confidence, reason,
            )
            self._advance()

    def insert_relations_with_confidence(
        self,
        from_table: str,
        to_table: str,
        relations: list[dict],
        rel_type: str,
        reason: str,
    ) -> None:
        """Insert relations where each row has its own confidence value."""
        if not relations:
            return
        for batch in _chunked(relations, self.BATCH_SIZE):
            self._store.bulk_unwind_relation_with_confidence(
                from_table, to_table, batch, rel_type, reason,
            )
            self._advance()

    def insert_file_relations(
        self,
        to_table: str,
        relations: list[dict],
        rel_type: str,
    ) -> None:
        """Insert File → X relations where relations contain {from_id, to_id}."""
        if not relations:
            return
        for batch in _chunked(relations, self.BATCH_SIZE):
            self._store.bulk_unwind_relation(
                "File", to_table, batch, rel_type, 1.0, f"file defines {to_table.lower()}",
            )
            self._advance()

    def insert_typed_relations(
        self,
        from_table: str,
        to_table: str,
        relations: list[dict],
        rel_type: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Insert relations where from_table nodes have ids starting with the table prefix."""
        # Filter relations to only those matching this from_table
        if not relations:
            return
        # The relation dicts already have from_id/to_id, just filter by table
        filtered = [r for r in relations if r["from_id"].startswith(from_table + "_")]
        if not filtered:
            return
        for batch in _chunked(filtered, self.BATCH_SIZE):
            self._store.bulk_unwind_relation(
                from_table, to_table, batch, rel_type, confidence, reason,
            )
            self._advance()

    def insert_nodes_with_defines(
        self,
        table: str,
        nodes: list[dict],
        rel_type: str,
    ) -> None:
        """Insert nodes that also have a DEFINES edge from their File.

        Uses CSV COPY for large batches (much faster than UNWIND+MATCH).
        Falls back to UNWIND for small batches.
        """
        if not nodes:
            return
        # For small batches, UNWIND is fine. For large, CSV COPY is much faster.
        if len(nodes) <= 500:
            for batch in _chunked(nodes, self.BATCH_SIZE):
                self._store.bulk_unwind_insert_with_defines(table, batch, rel_type)
                self._advance()
        else:
            # CSV COPY: insert all at once, no MATCH overhead
            self._store.bulk_copy_nodes_with_defines(table, nodes, rel_type)


def _chunked(lst: list, size: int) -> list:
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def _is_js_ts_file(file_path: str) -> bool:
    """Check if a file path is JS/TS/Vue/HTML based on extension."""
    return file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".vue", ".html"))


def _compute_stats(parsed_files: list[ParsedFile]) -> dict:
    """Compute summary statistics."""
    total_classes = sum(len(pf.classes) for pf in parsed_files)
    total_methods = sum(len(pf.methods) for pf in parsed_files)
    total_constructors = sum(len(pf.constructors) for pf in parsed_files)
    total_fields = sum(len(pf.fields) for pf in parsed_files)
    total_variables = sum(len(pf.variables) for pf in parsed_files)
    total_annotations = sum(len(pf.annotations) for pf in parsed_files)
    total_calls = sum(len(pf.calls) for pf in parsed_files)
    total_field_accesses = sum(len(pf.field_accesses) for pf in parsed_files)
    total_imports = sum(len(pf.imports) for pf in parsed_files)
    return {
        "files": len(parsed_files),
        "classes": total_classes,
        "methods": total_methods,
        "constructors": total_constructors,
        "fields": total_fields,
        "variables": total_variables,
        "annotations": total_annotations,
        "calls": total_calls,
        "field_accesses": total_field_accesses,
        "imports": total_imports,
    }


def _make_file_id(relative: str) -> str:
    return f"File_{relative.replace('/', '_').replace('.', '_')}"


def _make_folder_id(folder: str) -> str:
    return f"Folder_{folder.replace('/', '_').replace('.', '_')}"


def _make_class_id(name: str, file_path: str, start_line: int = 0, is_interface: bool = False) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    safe = name.replace(".", "_").replace("/", "_")
    prefix = "Interface" if is_interface else "Class"
    return f"{prefix}_{safe_path}_{safe}_{start_line}"


def _make_method_id(fq_name: str, file_path: str, start_line: int = 0) -> str:
    safe_name = fq_name.replace(".", "_").replace("/", "_")
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"Method_{safe_path}_{safe_name}_{start_line}"


def _make_field_id(name: str, file_path: str, start_line: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"Field_{safe_path}_{name}_{start_line}"


def _make_constructor_id(class_name: str, name: str, file_path: str, start_line: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    safe_class = class_name.replace(".", "_").replace("/", "_")
    return f"Constructor_{safe_path}_{safe_class}_{name}_{start_line}"


def _make_variable_id(name: str, file_path: str, line: int = 0, counter: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    suffix = f"_{counter}" if counter > 0 else ""
    return f"Variable_{safe_path}_{name}_{line}{suffix}"


def _make_annotation_id(ann_name: str, target_name: str, file_path: str, line: int = 0, counter: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"Annotation_{safe_path}_{ann_name}_{target_name}_{line}_{counter}"


def _make_type_alias_id(name: str, file_path: str, start_line: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"TypeAlias_{safe_path}_{name}_{start_line}"


def _make_enum_id(name: str, file_path: str, start_line: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"Enum_{safe_path}_{name}_{start_line}"


def _build_spring_endpoint_map(
    parsed_files: list[ParsedFile],
) -> dict[str, list[tuple[str, str, str]]]:
    """Build (file_path) → list of (method_name, http_method, http_path) from Spring annotations.

    Returns a list (not dict) per file so duplicate method names don't overwrite each other.
    Each tuple: (method_name, http_method, http_path)
    """
    # Annotation name → HTTP method mapping
    annotation_method_map = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "DeleteMapping": "DELETE",
        "PatchMapping": "PATCH",
    }

    result: dict[str, list[tuple[str, str, str]]] = {}

    # First pass: find class-level @RequestMapping paths
    class_prefix: dict[str, str] = {}  # class_name → prefix_path
    for pf in parsed_files:
        for ann in pf.annotations:
            if ann.name == "RequestMapping" and ann.target_type == "class":
                path = ann.attributes.get("path", "") or ann.attributes.get("value", "")
                if path:
                    class_prefix[ann.target_name] = path

    # Build method_name → class_name map per file (to link method annotations to their class)
    method_class_map: dict[str, dict[str, str]] = {}  # file_path → {method_name → class_name}
    for pf in parsed_files:
        for method in pf.methods:
            if method.class_name:
                method_class_map.setdefault(pf.file_path, {})[method.name] = method.class_name

    # Second pass: find method-level HTTP mapping annotations
    for pf in parsed_files:
        endpoints: list[tuple[str, str, str]] = []
        file_method_class = method_class_map.get(pf.file_path, {})

        for ann in pf.annotations:
            # Skip class-level annotations — they only contribute prefixes
            if ann.target_type == "class":
                continue

            http_method = annotation_method_map.get(ann.name)
            if not http_method and ann.name == "RequestMapping":
                # @RequestMapping can specify method
                http_method = "GET"  # default
                method_attr = ann.attributes.get("method", "")
                if method_attr:
                    # Extract HTTP method from e.g. "RequestMethod.POST"
                    if "." in method_attr:
                        method_attr = method_attr.split(".")[-1]
                    if method_attr in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                        http_method = method_attr

            if http_method:
                path = ann.attributes.get("path", "") or ann.attributes.get("value", "")
                if path:
                    # Combine with class-level prefix
                    full_path = path
                    # Look up the class name for this method annotation
                    class_name = file_method_class.get(ann.target_name)
                    if class_name and class_name in class_prefix:
                        prefix = class_prefix[class_name]
                        full_path = prefix.rstrip("/") + "/" + path.lstrip("/")

                    endpoints.append((ann.target_name, http_method, full_path))

        if endpoints:
            result[pf.file_path] = endpoints

    return result


def _enrich_java_calls(
    call_relations: list[tuple],
    spring_endpoint_map: dict[str, list[tuple[str, str, str]]],
) -> list[tuple]:
    """Enrich Java CALLS relations with Spring endpoint info.

    For calls targeting a controller method with HTTP endpoint info,
    convert the 5-tuple to an 8-tuple with http_method, http_path, http_params.
    """
    enriched: list[tuple] = []
    for item in call_relations:
        if len(item) == 8:
            # Already has HTTP info (JS/TS)
            enriched.append(item)
            continue

        # 5-tuple Java call: (caller, caller_file, target, target_file, confidence)
        caller, caller_file, target, target_file, confidence = item

        # Check if the target method is a Spring controller endpoint
        endpoint_list = spring_endpoint_map.get(target_file, [])
        matched = False
        for method_name, http_method, http_path in endpoint_list:
            if method_name == target:
                # Convert to 8-tuple
                enriched.append((caller, caller_file, target, target_file, confidence, http_method, http_path, None))
                matched = True
                break
        if not matched:
            enriched.append(item)

    return enriched
