"""Analysis pipeline: scan → parse → extract → resolve → store."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..core.models import ParsedFile
from ..core.scanner import scan
from ..core.extractor import parse
from ..core.resolver import resolve_calls, resolve_imports
from ..graph.store import GraphStore
from ..storage.repo_manager import register_repo, RepoInfo


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
            progress_callback(pct, msg)

    # Step 1: Scan
    _progress(5, "Scanning for Java files...")
    java_files = scan(repo_path)
    _progress(10, f"Found {len(java_files)} Java files")

    # Step 2: Parse files concurrently (ThreadPoolExecutor — tree-sitter releases GIL)
    _progress(15, "Parsing Java files (concurrent)...")
    t0 = time.monotonic()
    parsed_files = _parse_concurrent(java_files, _progress, len(java_files))
    parse_elapsed = time.monotonic() - t0
    _progress(55, f"Parsed {len(parsed_files)}/{len(java_files)} files in {parse_elapsed:.1f}s")

    # Step 3: Resolve cross-file relations (chunked parallel)
    _progress(60, "Resolving cross-file relations...")
    class_map = resolve_imports(parsed_files)
    t_resolve = time.monotonic()
    call_relations = _resolve_calls_parallel(parsed_files, class_map)
    resolve_elapsed = time.monotonic() - t_resolve
    _progress(70, f"Resolved {len(call_relations)} calls in {resolve_elapsed:.1f}s")

    # Step 4: Build graph with batched writes
    _progress(70, "Building knowledge graph (batched)...")
    t1 = time.monotonic()
    store = GraphStore(db_path)
    store.init_schema()
    _write_to_graph_batched(store, parsed_files, java_files, repo_path, call_relations, class_map)
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


def _parse_concurrent(
    java_files: list,
    progress_callback,
    total: int,
) -> list[ParsedFile]:
    """Parse Java files concurrently using a process pool to avoid GIL."""
    parsed: list[ParsedFile] = []
    import threading
    lock = threading.Lock()
    done = 0

    max_workers = min(8, os.cpu_count() or 8)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(parse, jf.relative, jf.content): jf
            for jf in java_files
        }
        for future in as_completed(futures):
            jf = futures[future]
            try:
                pf = future.result()
                with lock:
                    parsed.append(pf)
            except Exception as e:
                progress_callback(
                    15 + int(40 * (done + 1) / max(total, 1)),
                    f"Skipping {jf.relative}: {e}",
                )
            done += 1
            pct = 15 + int(40 * done / max(total, 1))
            progress_callback(pct, f"Parsed {done}/{total} files")

    # Sort by file_path for deterministic output
    parsed.sort(key=lambda pf: pf.file_path)
    return parsed


def _resolve_calls_parallel(
    parsed_files: list[ParsedFile],
    class_map: dict[str, str],
) -> list[tuple[str, str, str, str, float]]:
    """Resolve call targets in parallel using ThreadPoolExecutor."""
    from ..core.resolver import resolve_calls_chunk_with_indices

    max_workers = min(4, os.cpu_count() or 4)
    chunk_size = max(1, len(parsed_files) // max_workers)
    chunks = [parsed_files[i:i + chunk_size] for i in range(0, len(parsed_files), chunk_size)]

    results: list[tuple[str, str, str, str, float]] = []
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
    java_files,  # list[JavaFile]
    repo_path: Path,
    call_relations: list[tuple[str, str, str, str, float]],
    class_map: dict[str, str],
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
        for jf in java_files:
            folder = str(Path(jf.relative).parent)
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
        for jf in java_files:
            file_id = _make_file_id(jf.relative)
            file_nodes.append({
                "id": file_id,
                "name": Path(jf.relative).name,
                "filePath": jf.relative,
                "content": jf.text[:10000],
            })
            folder = str(Path(jf.relative).parent)
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
        for pf in parsed_files:
            for method in pf.methods:
                mid = _make_method_id(
                    f"{method.class_name}.{method.name}" if method.class_name else method.name,
                    method.file_path,
                    method.start_line,
                )
                method_ids[(method.name, method.file_path)] = mid
                if method.class_name:
                    method_ids[(f"{method.class_name}.{method.name}", method.file_path)] = mid
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

        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            for cls in pf.classes:
                node_id = _make_class_id(cls.name, pf.file_path, cls.start_line, cls.is_interface)
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

            # Methods and fields for this file
            for method in pf.methods:
                method_id = _make_method_id(
                    f"{method.class_name}.{method.name}" if method.class_name else method.name,
                    method.file_path,
                    method.start_line,
                )
                method_defines.append({
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
                })

            # 5. Write fields — build directly, no lookup needed
            for field in pf.fields:
                field_id = _make_field_id(field.name, field.file_path, field.start_line)
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

        # Insert Constructor nodes
        ctor_defines: list[dict] = []
        has_constructors: list[dict] = []
        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            for ctor in pf.constructors:
                cid = _make_constructor_id(ctor.class_name, ctor.name, ctor.file_path, ctor.start_line)
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
                cls_fqn = None
                for fqn in class_id_lookup:
                    if fqn.endswith(f".{ctor.class_name}") or fqn == ctor.class_name:
                        cls_fqn = fqn
                        break
                if cls_fqn:
                    cls_node_id = class_id_lookup[cls_fqn]
                    if cls_node_id.startswith("Class_"):
                        has_constructors.append({"from_id": cls_node_id, "to_id": cid})

        bw.insert_nodes_with_defines("Constructor", ctor_defines, "DEFINES")
        if has_constructors:
            bw.insert_relations("Class", "Constructor", has_constructors, "HAS_CONSTRUCTOR", 1.0, "class has constructor")
        _step("Constructors done")

        # Insert Variable nodes
        var_defines: list[dict] = []
        for pf in parsed_files:
            file_id = _make_file_id(pf.file_path)
            for var in pf.variables:
                vid = _make_variable_id(var.name, var.file_path or pf.file_path, var.line)
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
                    cls_id = None
                    for fqn, nid in class_id_lookup.items():
                        if fqn.endswith(f".{ann.target_name}") or fqn == ann.target_name:
                            cls_id = nid
                            break
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

        # 6. Write CALLS relations (deduplicate by caller_id + target_id)
        _step("Starting CALLS relations...")

        # Caller can be Method or Constructor; target can be Method, Constructor, or Class
        calls_by_tables: dict[tuple[str, str], list[dict]] = {}
        seen_calls: set[tuple[str, str]] = set()
        for caller_name, caller_file, target_name, target_file, confidence in call_relations:
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
                calls_by_tables.setdefault((caller_table, target_table), []).append({
                    "from_id": caller_id,
                    "to_id": target_id,
                    "confidence": confidence,
                })
        for (from_table, to_table), data in calls_by_tables.items():
            store.bulk_copy_relations(
                from_table, to_table, data, "CALLS", "method invocation",
            )
        _step("CALLS done")

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


class BatchWriter:
    """Context manager for batched graph writes using UNWIND."""

    BATCH_SIZE = 500

    def __init__(self, store: GraphStore) -> None:
        self._store = store

    def __enter__(self) -> "BatchWriter":
        return self

    def __exit__(self, *args) -> None:
        pass

    def insert_nodes(self, table: str, nodes: list[dict]) -> None:
        if not nodes:
            return
        for batch in _chunked(nodes, self.BATCH_SIZE):
            self._store.bulk_unwind_insert(table, batch)

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

    def insert_nodes_with_defines(
        self,
        table: str,
        nodes: list[dict],
        rel_type: str,
    ) -> None:
        """Insert nodes that also have a DEFINES edge from their File."""
        if not nodes:
            return
        for batch in _chunked(nodes, self.BATCH_SIZE):
            self._store.bulk_unwind_insert_with_defines(table, batch, rel_type)


def _chunked(lst: list, size: int) -> list:
    return [lst[i:i + size] for i in range(0, len(lst), size)]


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


def _make_variable_id(name: str, file_path: str, line: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"Variable_{safe_path}_{name}_{line}"


def _make_annotation_id(ann_name: str, target_name: str, file_path: str, line: int = 0, counter: int = 0) -> str:
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"Annotation_{safe_path}_{ann_name}_{target_name}_{line}_{counter}"
