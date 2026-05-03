"""Cross-file call resolution for JavaScript/TypeScript via ES Module imports.

The Java resolver uses fully-qualified class names, which doesn't apply to JS/TS.
This module resolves calls by:
1. Building an export index: (file_path, symbol_name) -> MethodDef/ClassDef
2. Resolving import paths (aliases like @/ and relative paths) to actual file paths
3. Correlating call sites with imported symbols to create CALLS edges
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .models import (
    CallSite,
    ClassDef,
    ExportDecl,
    ImportDecl,
    MethodDef,
    ParsedFile,
)


def resolve_js_ts_calls(
    parsed_files: list[ParsedFile],
    project_root: str | None = None,
) -> list[tuple[str, str, str, str, float, str | None, str | None]]:
    """Resolve cross-file calls for JS/TS files via import correlation.

    For each file:
    1. Resolve import module paths to actual target file paths
    2. Build import->export mapping: imported_symbol -> (target_file, exported_symbol)
    3. For each call site, if target_name matches an imported symbol, resolve to target MethodDef
    4. If the target method makes an HTTP call, attach http_method/http_path to the relation

    Returns:
        List of (caller_method, caller_file, target_method, target_file, confidence,
                  http_method, http_path, http_params) tuples.
    """
    # Only process JS/TS files
    js_ts_files = [pf for pf in parsed_files if _is_js_ts(pf.file_path)]
    if not js_ts_files:
        return []

    # Build export index: (file_path, export_name) -> MethodDef or ClassDef
    export_index = _build_export_index(js_ts_files)

    # Build import resolution: for each file, map imported symbols to target files
    file_import_map = _build_import_map(js_ts_files, project_root)

    # Build a combined method lookup: (file_path, method_name) -> MethodDef
    method_by_file_name: dict[tuple[str, str], MethodDef] = {}
    for pf in js_ts_files:
        for method in pf.methods:
            method_by_file_name[(pf.file_path, method.name)] = method

    # Build a global method name index: method_name -> list of MethodDef
    # Used as fallback when import-based resolution fails (e.g., utility functions)
    method_by_name: dict[str, list[MethodDef]] = {}
    for pf in js_ts_files:
        for method in pf.methods:
            method_by_name.setdefault(method.name, []).append(method)

    # Build a function-to-methods return type index:
    # func_name -> [(target_file, method_name), ...]
    # For patterns like `storageLocal().getItem()` where `storageLocal` is an
    # exported function. We find the file that exports the function and look for
    # methods defined in the same file — those are the likely return value methods.
    return_type_index: dict[str, list[tuple[str, str]]] = {}
    for pf in js_ts_files:
        for method in pf.methods:
            if method.class_name is None and method.is_public:
                content = method.content or ""

                # Special case: Pinia/Vuex store hooks and composables
                # Functions ending with "Hook" or starting with "use" that return
                # an object — all top-level methods in the same file are return value candidates
                if method.name.endswith("Hook") or (
                    method.name.startswith("use")
                    and len(method.name) > 3
                    and method.name[3:4].isupper()
                ):
                    for other in pf.methods:
                        if other.name != method.name and other.class_name is None:
                            return_type_index.setdefault(method.name, []).append(
                                (pf.file_path, other.name)
                            )
                    # Also include class methods
                    for cls in pf.classes:
                        for m in pf.methods:
                            if m.class_name == cls.name:
                                return_type_index.setdefault(method.name, []).append(
                                    (pf.file_path, m.name)
                                )
                    continue

                # General case: check if the function body references other methods
                for other in pf.methods:
                    if other.name != method.name and other.name in content:
                        return_type_index.setdefault(method.name, []).append(
                            (pf.file_path, other.name)
                        )
                # Also include class methods from the same file
                for cls in pf.classes:
                    if cls.name in content or f"new {cls.name}" in content:
                        for m in pf.methods:
                            if m.class_name == cls.name:
                                return_type_index.setdefault(method.name, []).append(
                                    (pf.file_path, m.name)
                                )

    # Build a class lookup: (file_path, class_name) -> ClassDef
    class_by_file_name: dict[tuple[str, str], ClassDef] = {}
    for pf in js_ts_files:
        for cls in pf.classes:
            class_by_file_name[(pf.file_path, cls.name)] = cls

    # Build Vue ref → component mapping
    # For patterns like `h(component, { ref: formRef })` where `formRef` is a Vue ref,
    # and later `formRef.value.getRef()` is called. We link `formRef` to the component.
    vue_ref_map: dict[tuple[str, str], str] = {}  # (caller_file, ref_var) -> target_file
    for pf in js_ts_files:
        _build_vue_ref_map(pf, vue_ref_map, method_by_file_name)

    # Build HTTP endpoint lookup
    http_endpoint_map: dict[tuple[str, str], tuple[str, str]] = {}
    for pf in js_ts_files:
        for call in pf.calls:
            if call.http_method and call.http_path:
                http_endpoint_map[(pf.file_path, call.caller_method)] = (
                    call.http_method,
                    call.http_path,
                )

    # Known external receiver → local file mapping
    # For patterns like `storageLocal().getItem()` where storageLocal is from
    # an external package (@pureadmin/utils) but has a local equivalent.
    # Maps external receiver name to a local file that provides the same API.
    EXTERNAL_RECEIVER_MAP = {
        "storageLocal": "src/utils/localforage/index.ts",
    }

    # Resolve calls
    results: list[tuple[str, str, str, str, float, str | None, str | None, str | None]] = []

    for pf in js_ts_files:
        import_map = file_import_map.get(pf.file_path, {})

        for call in pf.calls:
            target_name = call.target_name
            if not target_name:
                continue

            resolved = False

            # --- Case 1: Attribute chain call — api.get('/path') ---
            # target_name is "api.get", receiver is "api", which is an imported symbol
            if call.receiver and "." in target_name:
                parts = target_name.split(".", 1)
                receiver_name = parts[0]
                method_name = parts[1]

                # Check if the receiver is an imported symbol
                if receiver_name in import_map:
                    target_file, _ = import_map[receiver_name]

                    # Look for the method in the target file
                    target_method = method_by_file_name.get((target_file, method_name))
                    if target_method:
                        caller_method = call.caller_method or "anonymous"
                        caller_file = pf.file_path

                        http_info = http_endpoint_map.get((target_file, target_method.name))
                        http_method = http_info[0] if http_info else None
                        http_path = http_info[1] if http_info else None

                        http_params = None
                        for tc in _find_parsed_file(js_ts_files, target_file).calls:
                            if tc.http_method and tc.http_path and \
                               tc.caller_method == target_method.name:
                                http_params = tc.http_params
                                break

                        results.append((
                            caller_method,
                            caller_file,
                            target_method.name,
                            target_method.file_path,
                            0.9,
                            http_method,
                            http_path,
                            http_params,
                        ))
                        resolved = True

            if resolved:
                continue

            # --- Case 2b: Vue component reference — target_name is "LoginPhone.vue" ---
            # Vue extractor sets target_name to the .vue file basename. We need to
            # strip the .vue suffix, match against the import symbol name, then
            # resolve to the actual .vue target file.
            if target_name.endswith(".vue"):
                symbol_name = target_name[:-4]  # Strip ".vue"
                if symbol_name in import_map:
                    target_file, _ = import_map[symbol_name]
                    # For Vue SFCs, keep the .vue suffix on target_name to match GitNexus
                    caller_method = call.caller_method or "anonymous"
                    caller_file = pf.file_path

                    results.append((
                        caller_method,
                        caller_file,
                        target_name,  # Keep ".vue" suffix: "LoginPhone.vue"
                        target_file,
                        0.9,
                        None,
                        None,
                        None,
                    ))
                    resolved = True

            if resolved:
                continue

            # --- Case 2: Direct imported symbol — foo() or { foo } import ---
            if target_name in import_map:
                target_file, export_name = import_map[target_name]

                # Find the actual method in the target file
                target_method = method_by_file_name.get((target_file, target_name))
                if target_method:
                    caller_method = call.caller_method or "anonymous"
                    caller_file = pf.file_path

                    http_info = http_endpoint_map.get((target_file, target_method.name))
                    http_method = http_info[0] if http_info else None
                    http_path = http_info[1] if http_info else None

                    http_params = None
                    for tc in _find_parsed_file(js_ts_files, target_file).calls:
                        if tc.http_method and tc.http_path and \
                           tc.caller_method == target_method.name:
                            http_params = tc.http_params
                            break

                    results.append((
                        caller_method,
                        caller_file,
                        target_method.name,
                        target_method.file_path,
                        0.9,  # High confidence: import-based resolution
                        http_method,
                        http_path,
                        http_params,
                    ))
                    resolved = True

            if resolved:
                continue

            # --- Case 3: Same-file method call — target_name matches a method in the same file ---
            if "." not in target_name:
                target_method = method_by_file_name.get((pf.file_path, target_name))
                if target_method:
                    caller_method = call.caller_method or "anonymous"
                    caller_file = pf.file_path

                    results.append((
                        caller_method,
                        caller_file,
                        target_method.name,
                        target_method.file_path,
                        0.85,
                        None,
                        None,
                        None,
                    ))
                    resolved = True

            if resolved:
                continue

            # --- Case 3b: Same-file class reference — target_name is a class defined in same file ---
            # For patterns like `new ImageCapture()` where ImageCapture is a class
            # defined in the same file, or class references used as values.
            if "." not in target_name:
                target_cls = class_by_file_name.get((pf.file_path, target_name))
                if target_cls:
                    caller_method = call.caller_method or "anonymous"
                    caller_file = pf.file_path

                    results.append((
                        caller_method,
                        caller_file,
                        target_name,
                        target_cls.file_path,
                        0.85,
                        None,
                        None,
                        None,
                    ))
                    resolved = True

            if resolved:
                continue

            # --- Case 4b: External receiver mapping — storageLocal.getItem() ---
            # For patterns like `storageLocal().getItem()` where storageLocal is from
            # an external package but has a local equivalent file.
            if "." in target_name:
                parts = target_name.split(".", 1)
                receiver_name = parts[0]
                method_name = parts[1]

                if receiver_name in EXTERNAL_RECEIVER_MAP:
                    target_file = EXTERNAL_RECEIVER_MAP[receiver_name]
                    target_method = method_by_file_name.get((target_file, method_name))
                    if target_method:
                        caller_method = call.caller_method or "anonymous"
                        caller_file = pf.file_path

                        results.append((
                            caller_method,
                            caller_file,
                            target_method.name,
                            target_method.file_path,
                            0.7,  # Medium: external-to-local mapping
                            None,
                            None,
                            None,
                        ))
                        resolved = True

            if resolved:
                continue

            # --- Case 4: Utility function fallback — match by name across project ---
            # For calls like storageLocal.getItem() where storageLocal is external
            # but getItem() exists in the project (e.g., localforage/index.ts)
            # Also handles bare target_name when it's a unique method in the project.
            if "." in target_name:
                method_name = target_name.split(".", 1)[-1]
            else:
                method_name = target_name

            # Only resolve if there's a unique match in the project
            candidates = method_by_name.get(method_name, [])
            if len(candidates) == 1:
                target_method = candidates[0]
                caller_method = call.caller_method or "anonymous"
                caller_file = pf.file_path

                results.append((
                    caller_method,
                    caller_file,
                    target_method.name,
                    target_method.file_path,
                    0.5,  # Low confidence: name-only match
                    None,
                    None,
                    None,
                ))
                resolved = True

            if resolved:
                continue

            # --- Case 5: Return value chain — funcName().methodName() ---
            # For patterns like `storageLocal().getItem()` or `useMultiTagsStoreHook().handleTags()`
            # where funcName is an exported function and methodName is defined in the same file.
            if "." in target_name:
                parts = target_name.split(".", 1)
                func_name = parts[0]
                method_name = parts[1]

                if func_name in return_type_index:
                    for (target_file, target_method_name) in return_type_index[func_name]:
                        if target_method_name == method_name:
                            target_method = method_by_file_name.get((target_file, method_name))
                            if target_method:
                                caller_method = call.caller_method or "anonymous"
                                caller_file = pf.file_path

                                http_info = http_endpoint_map.get((target_file, target_method.name))
                                http_method = http_info[0] if http_info else None
                                http_path = http_info[1] if http_info else None

                                http_params = None
                                for tc in _find_parsed_file(js_ts_files, target_file).calls:
                                    if tc.http_method and tc.http_path and \
                                       tc.caller_method == target_method.name:
                                        http_params = tc.http_params
                                        break

                                results.append((
                                    caller_method,
                                    caller_file,
                                    target_method.name,
                                    target_method.file_path,
                                    0.75,  # Medium-high: same-file function-to-method link
                                    http_method,
                                    http_path,
                                    http_params,
                                ))
                                resolved = True
                                break

            if resolved:
                continue

            # --- Case 6: Vue ref.value.method() — formRef.value.getRef() ---
            # For patterns like `formRef.value.getRef()` where formRef is a Vue ref
            # passed to `h(component, { ref: formRef })`.
            # The receiver would be "formRef.value" and we need to resolve it to
            # the component file, then find the method there.
            if call.receiver and ".value" in call.receiver:
                # Extract the ref variable name from receiver (e.g., "formRef.value" -> "formRef")
                ref_var = call.receiver.split(".value")[0]
                ref_key = (pf.file_path, ref_var)
                if ref_key in vue_ref_map:
                    target_file = vue_ref_map[ref_key]
                    method_name = target_name.split(".")[-1] if "." in target_name else target_name
                    target_method = method_by_file_name.get((target_file, method_name))
                    if target_method:
                        caller_method = call.caller_method or "anonymous"
                        caller_file = pf.file_path

                        results.append((
                            caller_method,
                            caller_file,
                            target_method.name,
                            target_method.file_path,
                            0.8,  # Medium-high: ref-based component linkage
                            None,
                            None,
                            None,
                        ))
                        resolved = True

    return results


def _find_parsed_file(parsed_files: list[ParsedFile], file_path: str) -> ParsedFile:
    """Find a ParsedFile by its file_path."""
    for pf in parsed_files:
        if pf.file_path == file_path:
            return pf
    return ParsedFile(file_path=file_path)


def _build_vue_ref_map(
    pf: ParsedFile,
    vue_ref_map: dict[tuple[str, str], str],
    method_by_file_name: dict[tuple[str, str], MethodDef],
) -> None:
    """Build Vue ref → component file mapping from h(component, { ref: refVar }) patterns.

    Scans the file for:
    1. `h(componentVar, { ref: refVar })` or `h(componentVar, { ref: refVar, ... })`
    2. Maps `refVar` → the file that `componentVar` is imported from.

    This enables resolving `refVar.value.methodName()` calls to the target component.
    """
    # Build import map for this file
    import_map = {}
    for imp in pf.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                symbol = parts[1]
                target = parts[0]
                if target.endswith(".vue"):
                    import_map[symbol] = target

    # Find h(component, { ref: refVar }) patterns in the parsed method content
    # We look for calls where target_name is "h" and the receiver might give us the component
    # Since we can't easily parse nested structures, we use a heuristic:
    # Look for patterns in method content that match `h(xxx, { ref:`
    h_pattern = re.compile(r'\bh\s*\(\s*([a-zA-Z_$][\w$]*)\s*,\s*\{[^}]*ref\s*:\s*([a-zA-Z_$][\w$]*)')

    for method in pf.methods:
        content = method.content or ""
        for match in h_pattern.finditer(content):
            component_var = match.group(1)
            ref_var = match.group(2)

            if component_var in import_map:
                target_file = import_map[component_var]
                # Normalize target file path: it's currently like "./form.vue" or "../form.vue"
                # We need to resolve it to the actual project-relative path
                caller_dir = os.path.dirname(pf.file_path)
                candidate = os.path.normpath(os.path.join(caller_dir, target_file))
                # Check if this matches any known file
                for known_file in method_by_file_name:
                    if known_file[0] == candidate:
                        vue_ref_map[(pf.file_path, ref_var)] = known_file[0]
                        break
                else:
                    # Fallback: try to find by basename
                    target_base = os.path.basename(target_file)
                    for known_file in method_by_file_name:
                        if known_file[0].endswith(target_base):
                            vue_ref_map[(pf.file_path, ref_var)] = known_file[0]
                            break


def _is_js_ts(file_path: str) -> bool:
    """Check if file is JS/TS/Vue based on extension."""
    return file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".vue"))


def _build_export_index(
    parsed_files: list[ParsedFile],
) -> dict[str, list[tuple[str, str]]]:
    """Build export index: symbol_name -> list of (file_path, export_kind).

    Also indexes method names inside exported objects (e.g. const api = { get: ..., post: ... })
    so that api.get() can be resolved to the get method in the target file.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for pf in parsed_files:
        for export in pf.exports:
            index.setdefault(export.name, []).append((pf.file_path, export.kind))
        # Also index exported methods (top-level functions with export)
        for method in pf.methods:
            if method.class_name is None and method.is_public:
                index.setdefault(method.name, []).append((pf.file_path, "function"))
            # Index methods inside exported objects: api.get, api.post
            if method.class_name and method.class_name.isidentifier():
                # Check if the class_name matches an exported variable
                index.setdefault(f"{method.class_name}.{method.name}", []).append(
                    (pf.file_path, "method")
                )
        # Exported classes
        for cls in pf.classes:
            if cls.is_public:
                index.setdefault(cls.name, []).append((pf.file_path, "class"))
    return index


def _detect_aliases(project_root: str | None) -> dict[str, str]:
    """Detect path aliases by scanning the project root for config files.

    Returns dict mapping alias prefix to relative path, e.g. {"@/": "src/"}.
    """
    if not project_root:
        return {}

    aliases = {}

    # Common config files that define path aliases
    config_files = [
        "tsconfig.json",
        "jsconfig.json",
        "vite.config.ts",
        "vite.config.js",
        "next.config.js",
        "next.config.mjs",
        "webpack.config.js",
    ]

    for config_name in config_files:
        config_path = os.path.join(project_root, config_name)
        if not os.path.exists(config_path):
            continue

        try:
            content = Path(config_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # tsconfig/jsconfig: "paths": { "@/*": ["src/*"] }
        if config_name in ("tsconfig.json", "jsconfig.json"):
            import json
            try:
                data = json.loads(content)
                paths = data.get("compilerOptions", {}).get("paths", {})
                for alias, targets in paths.items():
                    # Convert "@/*" to "@/" and "src/*" to "src/"
                    if isinstance(targets, list) and targets:
                        alias_prefix = alias.rstrip("*")
                        target_prefix = targets[0].rstrip("*")
                        aliases[alias_prefix] = target_prefix
            except (json.JSONDecodeError, KeyError):
                pass

        # vite/next/webpack config: heuristic scanning for alias patterns
        if "vite" in config_name or "next" in config_name or "webpack" in config_name:
            # Look for patterns like '@': path.resolve(__dirname, 'src')
            alias_pattern = re.compile(r"['\"](@[^'\"]*)['\"]\s*[:=]\s*(?:path\.resolve\([^,]*,\s*)?['\"]([^'\"]+)['\"]")
            for match in alias_pattern.finditer(content):
                aliases[match.group(1)] = match.group(2)

    return aliases


def _resolve_import_path(
    module_path: str,
    caller_file: str,
    project_root: str | None,
    all_file_paths: set[str],
    aliases: dict[str, str],
) -> str | None:
    """Resolve a module import path to an actual file path within the project.

    Args:
        module_path: The import module path (e.g., "@/api/cron", "./utils")
        caller_file: The file that contains the import
        project_root: Project root directory
        all_file_paths: Set of all known file paths in the project
        aliases: Detected path aliases

    Returns:
        Resolved relative file path, or None if not found.
    """
    # Skip built-in modules and external packages
    if not module_path.startswith((".", "/", "@", "~")):
        # Could be a bare module like "react" — skip external packages
        return None

    resolved = module_path
    alias_applied = False

    # Resolve path aliases
    for alias, target in aliases.items():
        if resolved.startswith(alias):
            resolved = target + resolved[len(alias):]
            alias_applied = True
            break

    # If starts with /, treat as relative to project root
    if resolved.startswith("/"):
        resolved = resolved[1:]

    # If starts with ./ or ../, resolve relative to caller file
    # BUT skip this if the ./ came from alias expansion (e.g. @/ -> ./src/)
    # In that case, strip the leading ./ and treat as project-root-relative
    if alias_applied and resolved.startswith("./"):
        resolved = resolved[2:]
    elif resolved.startswith("./") or resolved.startswith("../"):
        caller_dir = str(Path(caller_file).parent)
        resolved = os.path.normpath(os.path.join(caller_dir, resolved))
        # Normalize to forward slashes
        resolved = resolved.replace(os.sep, "/")

    # Try exact match and common extensions
    extensions = ["", ".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts", "/index.tsx"]
    for ext in extensions:
        candidate = resolved + ext
        if candidate in all_file_paths:
            return candidate

    # Also try without leading ./
    if resolved.startswith("./"):
        bare = resolved[2:]
        for ext in extensions:
            candidate = bare + ext
            if candidate in all_file_paths:
                return candidate

    # Fallback: scan all files for a name match (heuristic)
    base_name = Path(resolved).name
    for fp in all_file_paths:
        if fp.endswith(f"/{base_name}") or fp.endswith(f"/{base_name}.ts") or \
           fp.endswith(f"/{base_name}.tsx") or fp.endswith(f"/{base_name}.js") or \
           fp.endswith(f"/{base_name}.jsx"):
            return fp

    return None


def _build_import_map(
    parsed_files: list[ParsedFile],
    project_root: str | None = None,
) -> dict[str, dict[str, tuple[str, str]]]:
    """Build per-file import maps.

    For each file, create a mapping: imported_symbol -> (target_file_path, exported_symbol)

    Returns:
        {file_path: {imported_symbol: (target_file, exported_symbol), ...}, ...}
    """
    # Collect all known file paths
    all_file_paths: set[str] = {pf.file_path for pf in parsed_files}

    # Detect path aliases
    aliases = _detect_aliases(project_root)

    # Also check parent directories for config files
    if not aliases and project_root:
        parent = Path(project_root)
        for _ in range(3):
            parent = parent.parent
            if str(parent) == '/':
                break
            aliases = _detect_aliases(str(parent))
            if aliases:
                # Adjust aliases: if config was in parent, alias target is relative to that parent
                # e.g. @/* -> src/* when found in parent means @/foo maps to src/foo relative to parent
                # But our files are relative to project_root, so we need to adjust
                # For now, keep aliases as-is and let _resolve_import_path handle it
                break

    # Also add common defaults if no aliases detected
    if not aliases:
        aliases = {"@/": ""}  # @/ maps to project root itself

    result: dict[str, dict[str, tuple[str, str]]] = {}

    for pf in parsed_files:
        import_map: dict[str, tuple[str, str]] = {}

        for imp in pf.imports:
            qualified_name = imp.qualified_name
            if imp.is_wildcard:
                # Namespace import or require — also index the module name
                # so we can resolve api.get() if api is the module
                mod = qualified_name.rsplit("/", 1)[-1]
                if mod:
                    import_map[mod] = (pf.file_path, mod)
                continue

            # Parse "module.symbol" format
            parts = qualified_name.rsplit(".", 1)
            if len(parts) != 2:
                continue

            module_path, symbol_name = parts

            # Resolve module path to actual file
            target_file = _resolve_import_path(
                module_path,
                pf.file_path,
                project_root,
                all_file_paths,
                aliases,
            )

            if target_file:
                import_map[symbol_name] = (target_file, symbol_name)

        result[pf.file_path] = import_map

    return result


def resolve_calls_chunk_with_indices(
    chunk: list[ParsedFile],
    all_parsed_files: list[ParsedFile],
    class_map: dict[str, str],
    project_root: str | None = None,
) -> list[tuple[str, str, str, str, float]]:
    """Resolve JS/TS calls for a chunk of parsed files.

    Compatible with the pipeline's parallel resolution interface.
    """
    results: list[tuple[str, str, str, str, float]] = []

    for pf in chunk:
        if not _is_js_ts(pf.file_path):
            continue

        # Get JS/TS resolved calls
        js_ts_results = resolve_js_ts_calls([pf], project_root)
        results.extend(js_ts_results)

    return results
