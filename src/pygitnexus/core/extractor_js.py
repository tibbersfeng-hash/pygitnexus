"""Parse JavaScript source files using tree-sitter and extract symbols."""

from __future__ import annotations

import os
import threading

import tree_sitter_javascript as tsjs
import tree_sitter as ts

from .models import (
    AnnotationDef,
    CallSite,
    ClassDef,
    ConstructorDef,
    ExportDecl,
    FieldDef,
    ImportDecl,
    MethodDef,
    ParamDef,
    ParsedFile,
    VariableDef,
)

# Module-level cached parser and queries
_lang: ts.Language | None = None
_parser: ts.Parser | None = None
_parser_lock = threading.Lock()
_queries: dict[str, ts.Query] = {}


def _get_lang() -> ts.Language:
    global _lang
    if _lang is None:
        _lang = ts.Language(tsjs.language())
    return _lang


def _get_parser() -> ts.Parser:
    global _parser
    if _parser is None:
        _parser = ts.Parser(_get_lang())
    return _parser


def _get_query(name: str, source: str) -> ts.Query:
    """Get or create a cached tree-sitter query."""
    if name not in _queries:
        _queries[name] = ts.Query(_get_lang(), source)
    return _queries[name]


def _get_text(node: ts.Node, source: bytes) -> str:
    return source[node.start_byte: node.end_byte].decode("utf-8", errors="replace")


def _node_text(node: ts.Node, source: bytes) -> str:
    return _get_text(node, source).strip()


def _captures_to_dict(query: ts.Query, captures) -> dict[str, list[ts.Node]]:
    """Convert tree-sitter captures."""
    if isinstance(captures, dict):
        return captures
    result: dict[str, list[ts.Node]] = {}
    for capture_index, node in captures:
        name = query.capture_names[capture_index]
        result.setdefault(name, []).append(node)
    return result


def _strip_generics(type_str: str) -> str:
    if "<" in type_str:
        return type_str.split("<")[0].strip()
    return type_str


def _is_exported(node: ts.Node) -> bool:
    """Check if a node is wrapped in an export_statement."""
    parent = node.parent
    while parent:
        if parent.type == "export_statement":
            return True
        parent = parent.parent
    return False


def _extract_modifiers(node: ts.Node, source: bytes) -> str:
    """Extract modifier text from a node (JS doesn't have modifiers like Java)."""
    parts = []
    # Check for direct modifier tokens as children
    for child in node.children:
        if child.type in ("static", "async", "abstract"):
            parts.append(child.type)
    # Check for export wrapper
    if _is_exported(node):
        parts.append("export")
    return " ".join(parts) + (" " if parts else "")


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse a single JavaScript file and extract all symbols."""
    lang = _get_lang()
    with _parser_lock:
        parser = _get_parser()
        tree = parser.parse(content)

    result = ParsedFile(file_path=file_path)

    # Pre-compute class byte ranges
    class_byte_map = _build_class_byte_map(tree.root_node, content)

    # --- Classes ---
    class_q = _get_query("js_class", """
        (class_declaration
          name: (identifier) @name
        ) @class
    """)
    cursor = ts.QueryCursor(class_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(class_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        class_node = cd["class"][0]
        class_name = _node_text(name_nodes[0], content)

        modifiers_text = _extract_modifiers(class_node, content)

        extends = _extract_extends(class_node, content)
        implements: list[str] = []  # JS doesn't have implements clause

        result.classes.append(ClassDef(
            name=class_name,
            file_path=file_path,
            start_line=class_node.start_point.row + 1,
            end_line=class_node.end_point.row + 1,
            is_public="export" in modifiers_text,
            is_abstract=False,
            is_interface=False,
            extends=extends,
            implements=implements,
            content=_get_text(class_node, content),
        ))

    # --- Class Methods ---
    method_q = _get_query("js_method", """
        (method_definition
          name: (property_identifier) @name
        ) @method
    """)
    cursor = ts.QueryCursor(method_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(method_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        method_node = cd["method"][0]
        method_name = _node_text(name_nodes[0], content)

        # Skip constructors (handled separately)
        if method_name == "constructor":
            continue

        owner = _find_enclosing_class_fast(method_node, content, class_byte_map)
        modifiers_text = _extract_modifiers(method_node, content)
        params = _extract_parameters(method_node, content)
        return_type = ""  # JS has no return type annotations

        result.methods.append(MethodDef(
            name=method_name,
            class_name=owner,
            file_path=file_path,
            start_line=method_node.start_point.row + 1,
            end_line=method_node.end_point.row + 1,
            return_type=return_type,
            parameters=params,
            is_static="static" in modifiers_text,
            is_public=True,  # JS class methods are public by default
            is_constructor=False,
            content=_get_text(method_node, content),
        ))

    # --- Constructors ---
    ctor_q = _get_query("js_ctor", """
        (method_definition
          name: (property_identifier) @name
        ) @ctor
    """)
    cursor = ts.QueryCursor(ctor_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(ctor_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        method_name = _node_text(name_nodes[0], content)
        if method_name != "constructor":
            continue
        ctor_node = cd["ctor"][0]

        owner = _find_enclosing_class_fast(ctor_node, content, class_byte_map)
        params = _extract_parameters(ctor_node, content)

        result.constructors.append(ConstructorDef(
            name=owner or "constructor",
            class_name=owner or "",
            file_path=file_path,
            start_line=ctor_node.start_point.row + 1,
            end_line=ctor_node.end_point.row + 1,
            parameters=params,
            is_public=True,
            content=_get_text(ctor_node, content),
        ))

    # --- Class Fields ---
    field_q = _get_query("js_field", """
        (field_definition
          property: (property_identifier) @name
        ) @field
    """)
    cursor = ts.QueryCursor(field_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(field_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        field_node = cd["field"][0]
        field_name = _node_text(name_nodes[0], content)
        owner = _find_enclosing_class_fast(field_node, content, class_byte_map)
        modifiers_text = _extract_modifiers(field_node, content)

        result.fields.append(FieldDef(
            name=field_name,
            type_name="",  # JS has no type annotations
            class_name=owner or "",
            file_path=file_path,
            start_line=field_node.start_point.row + 1,
            end_line=field_node.end_point.row + 1,
            is_static="static" in modifiers_text,
            is_public="static" not in modifiers_text or True,
        ))

    # --- Getters and Setters ---
    # In JS, getter/setter are method_definition with get/set modifier child
    getter_setter_q = _get_query("js_getter_setter", """
        (method_definition
          name: (property_identifier) @name
        ) @method
    """)
    cursor = ts.QueryCursor(getter_setter_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(getter_setter_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        method_node = cd["method"][0]
        method_name = _node_text(name_nodes[0], content)

        # Skip constructors
        if method_name == "constructor":
            continue

        # Check for get/set modifiers
        has_get = False
        has_set = False
        for child in method_node.children:
            if child.type == "get":
                has_get = True
            elif child.type == "set":
                has_set = True

        if not has_get and not has_set:
            continue

        owner = _find_enclosing_class_fast(method_node, content, class_byte_map)
        params = _extract_parameters(method_node, content)

        if has_get:
            result.methods.append(MethodDef(
                name=f"get {method_name}",
                class_name=owner or "",
                file_path=file_path,
                start_line=method_node.start_point.row + 1,
                end_line=method_node.end_point.row + 1,
                return_type="",
                parameters=[],
                is_static=False,
                is_public=True,
                is_constructor=False,
                content=_get_text(method_node, content),
            ))
        elif has_set:
            result.methods.append(MethodDef(
                name=f"set {method_name}",
                class_name=owner or "",
                file_path=file_path,
                start_line=method_node.start_point.row + 1,
                end_line=method_node.end_point.row + 1,
                return_type="",
                parameters=params,
                is_static=False,
                is_public=True,
                is_constructor=False,
                content=_get_text(method_node, content),
            ))

    # --- Top-level Functions ---
    func_q = _get_query("js_func", """
        (function_declaration
          name: (identifier) @name
        ) @func
    """)
    cursor = ts.QueryCursor(func_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(func_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        func_node = cd["func"][0]
        func_name = _node_text(name_nodes[0], content)

        params = _extract_parameters(func_node, content)
        modifiers_text = _extract_modifiers(func_node, content)

        result.methods.append(MethodDef(
            name=func_name,
            class_name=None,  # Top-level function
            file_path=file_path,
            start_line=func_node.start_point.row + 1,
            end_line=func_node.end_point.row + 1,
            return_type="",
            parameters=params,
            is_static=False,
            is_public="export" in modifiers_text or True,
            is_constructor=False,
            content=_get_text(func_node, content),
        ))

    # --- Arrow Functions and Function Expressions assigned to variables ---
    var_func_q = _get_query("js_var_func", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @name
            value: [
                (arrow_function)
                (function_expression)
            ] @body
          )
        ) @decl
    """)
    cursor = ts.QueryCursor(var_func_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(var_func_q, captures)
        name_nodes = cd.get("name", [])
        body_nodes = cd.get("body", [])
        if not name_nodes or not body_nodes:
            continue
        func_name = _node_text(name_nodes[0], content)
        body_node = body_nodes[0]

        params = _extract_params_from_function(body_node, content)
        modifiers_text = _extract_modifiers(cd.get("decl", [None])[0], content) if cd.get("decl") else ""

        result.methods.append(MethodDef(
            name=func_name,
            class_name=None,  # Top-level
            file_path=file_path,
            start_line=body_node.start_point.row + 1,
            end_line=body_node.end_point.row + 1,
            return_type="",
            parameters=params,
            is_static=False,
            is_public="export" in modifiers_text or True,
            is_constructor=False,
            content=_get_text(body_node, content),
        ))

    # --- export default arrow_function / function_expression ---
    export_default_q = _get_query("js_export_default", """
        (export_statement
          value: [
            (arrow_function)
            (function_expression)
          ] @body) @decl
    """)
    cursor = ts.QueryCursor(export_default_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(export_default_q, captures)
        body_nodes = cd.get("body", [])
        if not body_nodes:
            continue
        body_node = body_nodes[0]
        func_name = "default"

        params = _extract_params_from_function(body_node, content)
        result.methods.append(MethodDef(
            name=func_name,
            class_name=None,
            file_path=file_path,
            start_line=body_node.start_point.row + 1,
            end_line=body_node.end_point.row + 1,
            return_type="",
            parameters=params,
            is_static=False,
            is_public=True,
            is_constructor=False,
            content=_get_text(body_node, content),
        ))

    # --- Imports (ES modules) ---
    _extract_imports(tree.root_node, content, result)

    # --- Exports ---
    _extract_exports(tree.root_node, content, result)

    # --- Method calls ---
    _extract_calls(tree.root_node, content, lang, file_path, result, class_byte_map)

    # --- Module-level calls (top-level code outside any function) ---
    _extract_module_level_calls_js(tree.root_node, content, lang, file_path, result)

    # --- Local variables ---
    result.variables = _extract_variables(tree.root_node, content, file_path, class_byte_map)

    # --- Field accesses (minimal for JS) ---
    result.field_accesses = []

    # --- React Hooks detection ---
    _detect_react_hooks(tree.root_node, content, file_path, result)

    # --- HTTP endpoint extraction (for API layer files) ---
    _extract_http_endpoints(tree.root_node, content, file_path, result)

    return result


def _extract_extends(class_node: ts.Node, source: bytes) -> str | None:
    """Extract the superclass from a class_declaration node."""
    for child in class_node.children:
        if child.type == "class_heritage":
            for sub in child.children:
                if sub.type == "extends_clause":
                    for item in sub.children:
                        if item.type in ("identifier", "member_expression"):
                            return _node_text(item, source)
    return None


def _extract_parameters(node: ts.Node, source: bytes) -> list[ParamDef]:
    """Extract parameters from a method_definition or function_declaration node."""
    params = []
    for child in node.children:
        if child.type == "formal_parameters":
            for param in child.children:
                if param.type == "identifier":
                    params.append(ParamDef(name=_node_text(param, source), type_name=""))
                elif param.type == "assignment_pattern":
                    for sub in param.children:
                        if sub.type == "identifier":
                            params.append(ParamDef(name=_node_text(sub, source), type_name=""))
                            break
            break
    return params


def _extract_params_from_function(func_node: ts.Node, source: bytes) -> list[ParamDef]:
    """Extract parameters from arrow_function or function_expression node."""
    return _extract_parameters(func_node, source)


def _extract_imports(root: ts.Node, source: bytes, result: ParsedFile) -> None:
    """Extract ES module imports and CommonJS require() calls."""
    # ES module imports
    import_q = _get_query("js_import", """
        (import_statement
          .
          (import_clause
            [
              (identifier) @default_import
              (named_imports (import_specifier (identifier) @named_import))
              (namespace_import (identifier) @ns_import)
            ])
          .
          (string) @source)
    """)
    cursor = ts.QueryCursor(import_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(import_q, caps)
        source_nodes = cd.get("source", [])
        if not source_nodes:
            continue
        # Extract string content (skip quotes)
        source_text = _node_text(source_nodes[0], source)
        module_name = source_text.strip("'\"`")

        # Get imported names
        for node in cd.get("default_import", []):
            result.imports.append(ImportDecl(
                qualified_name=f"{module_name}.{_node_text(node, source)}",
                is_wildcard=False,
                file_path=result.file_path,
            ))
        for node in cd.get("named_import", []):
            result.imports.append(ImportDecl(
                qualified_name=f"{module_name}.{_node_text(node, source)}",
                is_wildcard=False,
                file_path=result.file_path,
            ))
        for node in cd.get("ns_import", []):
            result.imports.append(ImportDecl(
                qualified_name=module_name,
                is_wildcard=True,
                file_path=result.file_path,
            ))

    # CommonJS: const X = require("module")
    require_q = _get_query("js_require", """
        (call_expression
          function: (identifier) @func
          arguments: (arguments (string) @mod))
    """)
    cursor = ts.QueryCursor(require_q)
    seen: set[str] = set()
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(require_q, caps)
        func_nodes = cd.get("func", [])
        mod_nodes = cd.get("mod", [])
        if not func_nodes or not mod_nodes:
            continue
        func_name = _node_text(func_nodes[0], source)
        if func_name != "require":
            continue
        module = _node_text(mod_nodes[0], source).strip("'\"`")
        if module in seen:
            continue
        seen.add(module)
        result.imports.append(ImportDecl(
            qualified_name=module,
            is_wildcard=True,
            file_path=result.file_path,
        ))

    # Dynamic import(): import("module.js")
    dynamic_import_q = _get_query("js_dynamic_import", """
        (call_expression
          function: (import)
          arguments: (arguments (string) @mod))
    """)
    cursor = ts.QueryCursor(dynamic_import_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(dynamic_import_q, caps)
        mod_nodes = cd.get("mod", [])
        if not mod_nodes:
            continue
        module = _node_text(mod_nodes[0], source).strip("'\"`")
        # Extract module name as the "default import"
        mod_name = module.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if mod_name:
            result.imports.append(ImportDecl(
                qualified_name=f"{module}.{mod_name}",
                is_wildcard=False,
                file_path=result.file_path,
            ))


def _extract_exports(root: ts.Node, source: bytes, result: ParsedFile) -> None:
    """Extract export declarations including re-exports and module.exports."""
    # export class/function/const/let/var
    export_q = _get_query("js_export", """
        (export_statement) @export
    """)
    cursor = ts.QueryCursor(export_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(export_q, caps)
        export_nodes = cd.get("export", [])
        if not export_nodes:
            continue
        export_node = export_nodes[0]

        # Determine what's being exported
        kind = ""
        name = ""
        line = export_node.start_point.row + 1

        # Check for re-export: export { foo } from 'bar' or export * from './api'
        source_node = None
        for child in export_node.children:
            if child.type == "string":
                source_node = child
                break

        if source_node:
            # This is a re-export
            module_name = _node_text(source_node, source).strip("'\"`")

            # Check for export * from 'module'
            has_wildcard = any(child.type == "*" for child in export_node.children)
            if has_wildcard:
                result.imports.append(ImportDecl(
                    qualified_name=module_name,
                    is_wildcard=True,
                    file_path=result.file_path,
                ))
                continue

            # Check for export { foo, bar } from 'module'
            for child in export_node.children:
                if child.type == "export_clause":
                    for sub in child.children:
                        if sub.type == "export_specifier":
                            for name_child in sub.children:
                                if name_child.type == "identifier":
                                    sym_name = _node_text(name_child, source)
                                    result.exports.append(ExportDecl(
                                        name=sym_name,
                                        kind="re_export",
                                        file_path=result.file_path,
                                        line=name_child.start_point.row + 1,
                                    ))
                                    result.imports.append(ImportDecl(
                                        qualified_name=f"{module_name}.{sym_name}",
                                        is_wildcard=False,
                                        file_path=result.file_path,
                                    ))
            continue

        # export default
        for child in export_node.children:
            if child.type == "default":
                kind = "default"
                # Try to find the name
                for sub in child.children:
                    if sub.type == "identifier":
                        name = _node_text(sub, source)
                if not name:
                    name = "default"
                break
            elif child.type == "class_declaration":
                kind = "class"
                for sub in child.children:
                    if sub.type == "identifier":
                        name = _node_text(sub, source)
                        break
            elif child.type == "function_declaration":
                kind = "function"
                for sub in child.children:
                    if sub.type == "identifier":
                        name = _node_text(sub, source)
                        break
            elif child.type in ("lexical_declaration", "variable_declaration"):
                kind = "variable"
                for sub in child.children:
                    if sub.type == "variable_declarator":
                        for name_child in sub.children:
                            if name_child.type == "identifier":
                                name = _node_text(name_child, source)
                                break
            elif child.type in ("named_exports", "export_clause"):
                kind = "namespace"
                for sub in child.children:
                    if sub.type == "export_specifier":
                        for name_child in sub.children:
                            if name_child.type == "identifier":
                                result.exports.append(ExportDecl(
                                    name=_node_text(name_child, source),
                                    kind="variable",
                                    file_path=result.file_path,
                                    line=name_child.start_point.row + 1,
                                ))
                if kind == "namespace":
                    continue

        if name:
            result.exports.append(ExportDecl(
                name=name,
                kind=kind,
                file_path=result.file_path,
                line=line,
            ))

    # CommonJS: module.exports = { ... } or exports.foo = ...
    _extract_module_exports(root, source, result)


def _extract_module_exports(
    root: ts.Node,
    source: bytes,
    result: ParsedFile,
) -> None:
    """Extract CommonJS module.exports and exports.foo patterns."""
    # module.exports = { foo, bar }
    exports_q = _get_query("js_module_exports", """
        (assignment_expression
          left: [
            (member_expression
              object: (identifier) @obj
              property: (property_identifier) @prop)
            (identifier) @mod_id
          ]
          right: (object
            (pair key: (property_identifier) @key) @pair)
        ) @assign
    """)

    # Simpler: just find all (property_identifier) keys inside the right-hand
    # object of module.exports assignments
    # Since the query above may not work perfectly, use a simpler approach:
    # Find all `module.exports` assignments and extract property keys
    simple_q = _get_query("js_module_exports_simple", """
        (assignment_expression
          left: (member_expression
            object: (identifier) @mod
            property: (property_identifier) @prop_name)) @assign
    """)
    cursor = ts.QueryCursor(simple_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(simple_q, caps)
        mod_nodes = cd.get("mod", [])
        if not mod_nodes:
            continue
        mod_text = _node_text(mod_nodes[0], source)
        if mod_text != "module":
            continue

        assign_node = cd.get("assign", [None])[0]
        if assign_node is None:
            continue

        # Find object literal on the right side
        for child in assign_node.children:
            if child.type == "object":
                for pair_child in child.children:
                    if pair_child.type == "pair":
                        for sub in pair_child.children:
                            if sub.type == "property_identifier":
                                result.exports.append(ExportDecl(
                                    name=_node_text(sub, source),
                                    kind="module_exports",
                                    file_path=result.file_path,
                                    line=sub.start_point.row + 1,
                                ))

    # exports.foo = ...  (individual exports)
    individual_q = _get_query("js_individual_exports", """
        (assignment_expression
          left: (member_expression
            object: (identifier) @exports_id
            property: (property_identifier) @prop_name)) @assign
    """)
    cursor = ts.QueryCursor(individual_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(individual_q, caps)
        exports_nodes = cd.get("exports_id", [])
        prop_nodes = cd.get("prop_name", [])
        if not exports_nodes or not prop_nodes:
            continue
        exports_text = _node_text(exports_nodes[0], source)
        if exports_text != "exports":
            continue
        result.exports.append(ExportDecl(
            name=_node_text(prop_nodes[0], source),
            kind="module_exports",
            file_path=result.file_path,
            line=prop_nodes[0].start_point.row + 1,
        ))


def _extract_calls(
    root: ts.Node,
    source: bytes,
    lang: ts.Language,
    file_path: str,
    result: ParsedFile,
    class_byte_map: list[tuple[int, int, str]],
) -> None:
    """Extract method calls from all functions and methods.

    For member expressions like `api.get()`, if `api` is imported, the
    `target_name` is set to `api.get` so the resolver can match it to
    the target module's exported `get` method.
    """
    # Find all imports to build a set of imported symbol names
    imported_names: set[str] = set()
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                imported_names.add(parts[1])
        else:
            # For wildcard/require, extract module name as imported
            mod = imp.qualified_name.rsplit("/", 1)[-1]
            if mod:
                imported_names.add(mod)

    call_q = _get_query("js_call", """
        (call_expression
          function: [
            (identifier) @call_name
            (member_expression
              property: (property_identifier) @call_name)
          ]
        ) @call
    """)

    # Find all function bodies — single cursor reused for all functions (P3)
    func_body_q = ts.Query(lang, """
        [
            (function_declaration (identifier) @fname (statement_block) @body)
            (method_definition (property_identifier) @fname (statement_block) @body)
            (function_expression (identifier)? @fname (statement_block) @body)
        ]
    """)

    # Arrow functions with statement_block (e.g. `const fn = () => { ... }`)
    arrow_block_q = ts.Query(lang, """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @fname
            value: (arrow_function (statement_block) @body)))
    """)

    cursor = ts.QueryCursor(func_body_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(func_body_q, captures)
        body_nodes = cd.get("body", [])
        name_nodes = cd.get("fname", [])
        if not body_nodes:
            continue

        body_node = body_nodes[0]
        if name_nodes:
            method_name = _node_text(name_nodes[0], source)
        else:
            # Try to find variable_declarator name for `const X = function() {}`
            parent = body_node.parent
            var_name = None
            while parent:
                if parent.type == "variable_declarator":
                    for child in parent.children:
                        if child.type in ("identifier", "property_identifier"):
                            var_name = _node_text(child, source)
                            break
                    break
                parent = parent.parent
            method_name = var_name if var_name else os.path.basename(file_path)
        enclosing_class = _find_enclosing_class_fast(body_node, source, class_byte_map)
        fq_method = f"{enclosing_class}.{method_name}" if enclosing_class else method_name

        # Build type map for this function
        type_map: dict[str, str] = _build_type_map(body_node, source)

        call_cursor = ts.QueryCursor(call_q)
        seen: set[tuple[int, str]] = set()
        for _, caps in call_cursor.matches(body_node):
            call_cd = _captures_to_dict(call_q, caps)
            call_nodes = call_cd.get("call", [])
            call_name_nodes = call_cd.get("call_name", [])
            if not call_nodes or not call_name_nodes:
                continue
            call_node = call_nodes[0]
            call_name = _node_text(call_name_nodes[0], source)

            dedup_key = (call_node.start_byte, call_name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Extract receiver and build full target name for imported receivers
            receiver = None
            receiver_type = None
            target_name = call_name

            for child in call_node.children:
                if child.type == "member_expression":
                    obj = child.child_by_field_name("object")
                    if obj:
                        receiver = _node_text(obj, source)
                        if receiver in type_map:
                            receiver_type = type_map[receiver]
                        elif receiver and receiver[0:1].isupper():
                            receiver_type = receiver

                        # If receiver is an imported symbol, use full chain name
                        if receiver in imported_names:
                            target_name = f"{receiver}.{call_name}"
                        # Return value chain: receiver is itself a call expression
                        # e.g. storageLocal().getItem() — obj is call_expression(storageLocal)
                        elif obj.type == "call_expression":
                            func_node = obj.child_by_field_name("function")
                            if func_node and func_node.type == "identifier":
                                func_name = _node_text(func_node, source)
                                target_name = f"{func_name}.{call_name}"
                    break
                elif child.type == "identifier" and receiver is None:
                    # Direct call: foo() — no receiver
                    receiver = None
                    break

            result.calls.append(CallSite(
                caller_method=fq_method,
                caller_class=enclosing_class or "",
                target_name=target_name,
                line=call_node.start_point.row + 1,
                receiver=receiver,
                receiver_type=receiver_type,
            ))

    # Arrow functions with statement_block (e.g. `const fn = () => { ... }`)
    cursor = ts.QueryCursor(arrow_block_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(arrow_block_q, captures)
        body_nodes = cd.get("body", [])
        name_nodes = cd.get("fname", [])
        if not body_nodes or not name_nodes:
            continue

        body_node = body_nodes[0]
        func_name = _node_text(name_nodes[0], source)
        enclosing_class = _find_enclosing_class_fast(body_node, source, class_byte_map)
        fq_method = f"{enclosing_class}.{func_name}" if enclosing_class else func_name

        type_map: dict[str, str] = _build_type_map(body_node, source)

        call_cursor = ts.QueryCursor(call_q)
        seen: set[tuple[int, str]] = set()
        for _, caps in call_cursor.matches(body_node):
            call_cd = _captures_to_dict(call_q, caps)
            call_nodes = call_cd.get("call", [])
            call_name_nodes = call_cd.get("call_name", [])
            if not call_nodes or not call_name_nodes:
                continue
            call_node = call_nodes[0]
            call_name = _node_text(call_name_nodes[0], source)

            dedup_key = (call_node.start_byte, call_name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            receiver = None
            receiver_type = None
            target_name = call_name
            for child in call_node.children:
                if child.type == "member_expression":
                    obj = child.child_by_field_name("object")
                    if obj:
                        receiver = _node_text(obj, source)
                        if receiver in type_map:
                            receiver_type = type_map[receiver]
                        elif receiver and receiver[0:1].isupper():
                            receiver_type = receiver

                        if receiver in imported_names:
                            target_name = f"{receiver}.{call_name}"
                        # Return value chain: receiver is itself a call expression
                        elif obj.type == "call_expression":
                            func_node = obj.child_by_field_name("function")
                            if func_node and func_node.type == "identifier":
                                func_name = _node_text(func_node, source)
                                target_name = f"{func_name}.{call_name}"
                    break
                elif child.type == "identifier" and receiver is None:
                    receiver = None
                    break

            result.calls.append(CallSite(
                caller_method=fq_method,
                caller_class=enclosing_class or "",
                target_name=target_name,
                line=call_node.start_point.row + 1,
                receiver=receiver,
                receiver_type=receiver_type,
            ))

    # All arrow functions with statement_block at any depth:
    # e.g. validator: (rule, value, callback) => { ... } inside reactive({...})
    # This captures arrow functions nested inside object literals, arguments, etc.
    all_arrow_q = ts.Query(lang, """
        (arrow_function (statement_block) @body)
    """)

    # Track which arrow function bodies have been processed by previous queries
    processed_arrow_bodies: set[int] = set()
    arrow_block_cursor = ts.QueryCursor(
        ts.Query(lang, "(lexical_declaration (variable_declarator value: (arrow_function (statement_block) @body)))")
    )
    for _, caps in arrow_block_cursor.matches(root):
        ad = _captures_to_dict(arrow_block_cursor, caps)
        for bn in ad.get("body", []):
            processed_arrow_bodies.add(bn.start_byte)

    all_arrow_cursor = ts.QueryCursor(all_arrow_q)
    for _, captures in all_arrow_cursor.matches(root):
        ad = _captures_to_dict(all_arrow_q, captures)
        body_nodes = ad.get("body", [])
        if not body_nodes:
            continue

        body_node = body_nodes[0]

        # Skip if already processed by top-level arrow_block_q
        if body_node.start_byte in processed_arrow_bodies:
            continue

        # Walk up to find the enclosing named function for caller_method
        enclosing_func = None
        enclosing_class = None
        parent = body_node.parent
        while parent:
            if parent.type in ("function_declaration", "method_definition",
                               "function_expression"):
                for child in parent.children:
                    if child.type in ("identifier", "property_identifier"):
                        enclosing_func = _node_text(child, source)
                        break
                if parent.type == "method_definition":
                    enclosing_class = _find_enclosing_class_fast(
                        parent, source, class_byte_map)
                break
            elif parent.type == "arrow_function":
                # Nested arrow — keep walking up to find the enclosing named function
                pass
            parent = parent.parent

        # Determine caller_method
        if enclosing_func:
            if enclosing_class:
                fq_method = f"{enclosing_class}.{enclosing_func}"
            else:
                fq_method = enclosing_func
        else:
            # No enclosing named function — use filename to match
            # GitNexus's behavior for module-level/nested anonymous calls
            fq_method = os.path.basename(file_path)

        call_cursor = ts.QueryCursor(call_q)
        seen: set[tuple[int, str]] = set()
        for _, caps in call_cursor.matches(body_node):
            call_cd = _captures_to_dict(call_q, caps)
            call_nodes = call_cd.get("call", [])
            call_name_nodes = call_cd.get("call_name", [])
            if not call_nodes or not call_name_nodes:
                continue
            call_node = call_nodes[0]
            call_name = _node_text(call_name_nodes[0], source)

            dedup_key = (call_node.start_byte, call_name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            receiver = None
            receiver_type = None
            target_name = call_name
            for child in call_node.children:
                if child.type == "member_expression":
                    obj = child.child_by_field_name("object")
                    if obj:
                        receiver = _node_text(obj, source)

                        if receiver in imported_names:
                            target_name = f"{receiver}.{call_name}"
                        elif obj.type == "call_expression":
                            func_node = obj.child_by_field_name("function")
                            if func_node and func_node.type == "identifier":
                                func_name = _node_text(func_node, source)
                                target_name = f"{func_name}.{call_name}"
                    break

            result.calls.append(CallSite(
                caller_method=fq_method,
                caller_class=enclosing_class or "",
                target_name=target_name,
                line=call_node.start_point.row + 1,
                receiver=receiver,
                receiver_type=receiver_type,
            ))

    # Also extract new expressions (constructor calls)
    _extract_new_expressions(root, source, file_path, result, class_byte_map)


def _extract_new_expressions(
    root: ts.Node,
    source: bytes,
    file_path: str,
    result: ParsedFile,
    class_byte_map: list[tuple[int, int, str]],
) -> None:
    """Extract new X() constructor calls."""
    new_q = _get_query("js_new", """
        (new_expression
          constructor: (identifier) @ctor_name) @new_call
    """)
    cursor = ts.QueryCursor(new_q)
    seen: set[int] = set()
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(new_q, caps)
        new_nodes = cd.get("new_call", [])
        name_nodes = cd.get("ctor_name", [])
        if not new_nodes or not name_nodes:
            continue
        new_node = new_nodes[0]
        if new_node.start_byte in seen:
            continue
        seen.add(new_node.start_byte)

        ctor_name = _node_text(name_nodes[0], source)
        enclosing_class = _find_enclosing_class_fast(new_node, source, class_byte_map)

        # Determine caller_method: use filename for module-level calls
        if enclosing_class:
            new_caller = f"{enclosing_class}.anonymous"
        else:
            new_caller = os.path.basename(file_path)

        result.calls.append(CallSite(
            caller_method=new_caller,
            caller_class=enclosing_class or "",
            target_name=ctor_name,
            line=new_node.start_point.row + 1,
            receiver=ctor_name,
            receiver_type=ctor_name,
        ))


def _extract_module_level_calls_js(
    root: ts.Node,
    source: bytes,
    lang: ts.Language,
    file_path: str,
    result: ParsedFile,
) -> None:
    """Extract calls from module-level (top-level) code outside any function body."""
    module_name = os.path.basename(file_path)

    imported_names: set[str] = set()
    for imp in result.imports:
        if not imp.is_wildcard:
            parts = imp.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                imported_names.add(parts[1])
        else:
            mod = imp.qualified_name.rsplit("/", 1)[-1]
            if mod:
                imported_names.add(mod)

    call_q = _get_query("js_module_call", """
        (call_expression
          function: [
            (identifier) @call_name
            (member_expression
              property: (property_identifier) @call_name)
          ]
        ) @call
    """)

    cursor = ts.QueryCursor(call_q)
    seen: set[tuple[int, str]] = set()
    for _, caps in cursor.matches(root):
        call_cd = _captures_to_dict(call_q, caps)
        call_nodes = call_cd.get("call", [])
        call_name_nodes = call_cd.get("call_name", [])
        if not call_nodes or not call_name_nodes:
            continue
        call_node = call_nodes[0]
        call_name = _node_text(call_name_nodes[0], source)

        # Skip if inside a function/class
        parent = call_node.parent
        inside_func = False
        while parent:
            if parent.type in ("function_declaration", "method_definition",
                               "function_expression", "arrow_function",
                               "class_declaration", "class_body"):
                inside_func = True
                break
            parent = parent.parent
        if inside_func:
            continue

        dedup_key = (call_node.start_byte, call_name)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        receiver = None
        target_name = call_name
        for child in call_node.children:
            if child.type == "member_expression":
                obj = child.child_by_field_name("object")
                if obj:
                    receiver = _node_text(obj, source)
                    if receiver in imported_names:
                        target_name = f"{receiver}.{call_name}"
                    elif obj.type == "call_expression":
                        func_node = obj.child_by_field_name("function")
                        if func_node and func_node.type == "identifier":
                            func_name = _node_text(func_node, source)
                            target_name = f"{func_name}.{call_name}"
                break

        result.calls.append(CallSite(
            caller_method=module_name,
            caller_class="",
            target_name=target_name,
            line=call_node.start_point.row + 1,
            receiver=receiver,
            receiver_type=None,
        ))


def _build_type_map(body_node: ts.Node, source: bytes) -> dict[str, str]:
    """Build a mapping of variable names to their (unknown) types in JS."""
    # In JS, we can't infer types from declarations, but we can track variable names
    type_map: dict[str, str] = {}
    lv_q = _get_query("js_lv", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @vname))
    """)
    cursor = ts.QueryCursor(lv_q)
    for _, caps in cursor.matches(body_node):
        cd = _captures_to_dict(lv_q, caps)
        for n in cd.get("vname", []):
            type_map[_node_text(n, source)] = ""  # Unknown type
    return type_map


def _extract_variables(
    root: ts.Node,
    source: bytes,
    file_path: str,
    class_byte_map: list[tuple[int, int, str]],
) -> list[VariableDef]:
    """Extract local variable declarations (const, let, var, and destructuring)."""
    results: list[VariableDef] = []

    # const/let declarations (lexical_declaration)
    lv_q = _get_query("js_var_extract", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @vname)) @decl
    """)
    cursor = ts.QueryCursor(lv_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(lv_q, caps)
        name_nodes = cd.get("vname", [])
        decl_nodes = cd.get("decl", [])
        if not name_nodes:
            continue

        var_name = _node_text(name_nodes[0], source)
        line = name_nodes[0].start_point.row + 1
        is_final = "const" in _extract_modifiers(decl_nodes[0], source) if decl_nodes else False

        enclosing_class = _find_enclosing_class_fast(name_nodes[0], source, class_byte_map)

        results.append(VariableDef(
            name=var_name,
            type_name="",
            method_name="unknown",
            class_name=enclosing_class or "unknown",
            file_path=file_path,
            line=line,
            is_final=is_final,
        ))

    # var declarations (variable_declaration) — older JS
    var_q = _get_query("js_var_old", """
        (variable_declaration
          (variable_declarator
            name: (identifier) @vname)) @decl
    """)
    cursor = ts.QueryCursor(var_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(var_q, caps)
        name_nodes = cd.get("vname", [])
        if not name_nodes:
            continue

        var_name = _node_text(name_nodes[0], source)
        line = name_nodes[0].start_point.row + 1

        enclosing_class = _find_enclosing_class_fast(name_nodes[0], source, class_byte_map)

        results.append(VariableDef(
            name=var_name,
            type_name="",
            method_name="unknown",
            class_name=enclosing_class or "unknown",
            file_path=file_path,
            line=line,
            is_final=False,  # var is never final
        ))

    # Destructuring: const { a, b } = obj
    destructure_q = _get_query("js_destructure", """
        (lexical_declaration
          (variable_declarator
            name: (object_pattern
              (shorthand_property_identifier_pattern) @vname))) @decl
    """)
    cursor = ts.QueryCursor(destructure_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(destructure_q, caps)
        name_nodes = cd.get("vname", [])
        decl_nodes = cd.get("decl", [])
        for vname_node in name_nodes:
            var_name = _node_text(vname_node, source)
            line = vname_node.start_point.row + 1
            is_final = "const" in _extract_modifiers(decl_nodes[0], source) if decl_nodes else False
            enclosing_class = _find_enclosing_class_fast(vname_node, source, class_byte_map)

            results.append(VariableDef(
                name=var_name,
                type_name="",
                method_name="unknown",
                class_name=enclosing_class or "unknown",
                file_path=file_path,
                line=line,
                is_final=is_final,
            ))

    # Destructuring with renamed keys: const { a: b } = obj
    destructure_rename_q = _get_query("js_destructure_rename", """
        (lexical_declaration
          (variable_declarator
            name: (object_pattern
              (pair_pattern
                value: (identifier) @vname)))) @decl
    """)
    cursor = ts.QueryCursor(destructure_rename_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(destructure_rename_q, caps)
        name_nodes = cd.get("vname", [])
        decl_nodes = cd.get("decl", [])
        for vname_node in name_nodes:
            var_name = _node_text(vname_node, source)
            line = vname_node.start_point.row + 1
            is_final = "const" in _extract_modifiers(decl_nodes[0], source) if decl_nodes else False
            enclosing_class = _find_enclosing_class_fast(vname_node, source, class_byte_map)

            results.append(VariableDef(
                name=var_name,
                type_name="",
                method_name="unknown",
                class_name=enclosing_class or "unknown",
                file_path=file_path,
                line=line,
                is_final=is_final,
            ))

    return results


# --- HTTP endpoint extraction ---

_JS_HTTP_METHOD_MAP = {
    "get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
    "delete": "DELETE", "head": "HEAD", "options": "OPTIONS",
}
_JS_HTTP_CLIENT_NAMES = {
    "api", "http", "httpClient", "axios", "axiosInstance",
    "request", "client", "fetcher", "httpService", "apiClient",
}


def _extract_http_endpoints(
    root: ts.Node,
    source: bytes,
    file_path: str,
    result: ParsedFile,
) -> None:
    """Extract HTTP API endpoint info from JS API layer files.

    Supports: api.get('/path'), axios.get('/path'), fetch('/path').
    """
    http_call_q = _get_query("js_http_call", """
        (call_expression
          function: (member_expression
            property: (property_identifier) @http_method)
          arguments: (arguments
            [
              (string) @path_string
              (template_string) @path_template
            ])) @call
    """)

    fetch_call_q = _get_query("js_fetch_call", """
        (call_expression
          function: (identifier) @fetch_name
          arguments: (arguments
            [
              (string) @path_string
              (template_string) @path_template
            ])) @call
    """)

    cursor = ts.QueryCursor(http_call_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(http_call_q, caps)
        method_nodes = cd.get("http_method", [])
        path_nodes = cd.get("path_string", [])
        template_nodes = cd.get("path_template", [])
        call_nodes = cd.get("call", [])

        if not method_nodes:
            continue

        http_method = _node_text(method_nodes[0], source).lower()
        http_verb = _JS_HTTP_METHOD_MAP.get(http_method)
        if not http_verb:
            continue

        path_value = None
        if path_nodes:
            path_value = _node_text(path_nodes[0], source).strip("'\"`")
        elif template_nodes:
            path_value = _node_text(template_nodes[0], source).strip("`")

        if not path_value:
            continue

        # Check if receiver is a recognized HTTP client
        call_node = call_nodes[0] if call_nodes else None
        is_http_client = False
        if call_node:
            for child in call_node.children:
                if child.type == "member_expression":
                    obj = child.child_by_field_name("object")
                    if obj:
                        receiver_name = _node_text(obj, source)
                        if receiver_name in _JS_HTTP_CLIENT_NAMES:
                            is_http_client = True
                    break

        if not is_http_client:
            continue

        # Find enclosing function
        call_node_for_search = cd.get("http_method", [None])[0]
        if call_node_for_search is None:
            continue
        func_node = _js_find_enclosing_function(call_node_for_search)
        caller_name = _js_find_enclosing_func_name(func_node, source)

        target_line = (path_nodes[0].start_point.row + 1 if path_nodes else
                       template_nodes[0].start_point.row + 1 if template_nodes else 0)
        for call in result.calls:
            if (caller_name and call.caller_method == caller_name) or \
               (call.line == target_line):
                if call.receiver and call.receiver in _JS_HTTP_CLIENT_NAMES:
                    call.http_method = http_verb
                    call.http_path = path_value
                    break

    # fetch()
    fetch_cursor = ts.QueryCursor(fetch_call_q)
    for _, caps in fetch_cursor.matches(root):
        cd = _captures_to_dict(fetch_call_q, caps)
        fetch_name_nodes = cd.get("fetch_name", [])
        path_nodes = cd.get("path_string", [])
        template_nodes = cd.get("path_template", [])

        if not fetch_name_nodes:
            continue
        if _node_text(fetch_name_nodes[0], source) != "fetch":
            continue

        path_value = None
        if path_nodes:
            path_value = _node_text(path_nodes[0], source).strip("'\"`")
        elif template_nodes:
            path_value = _node_text(template_nodes[0], source).strip("`")
        if not path_value:
            continue

        func_node = _js_find_enclosing_function(fetch_name_nodes[0])
        caller_name = _js_find_enclosing_func_name(func_node, source)

        target_line = (path_nodes[0].start_point.row + 1 if path_nodes else
                       template_nodes[0].start_point.row + 1 if template_nodes else 0)
        for call in result.calls:
            if (caller_name and call.caller_method == caller_name) or \
               (call.line == target_line and call.target_name == "fetch"):
                call.http_method = "GET"
                call.http_path = path_value
                break


def _js_find_enclosing_function(node) -> ts.Node | None:
    """Find the enclosing function for a node."""
    current = node.parent
    while current:
        if current.type in ("arrow_function", "function_declaration",
                            "function_expression", "method_definition"):
            return current
        current = current.parent
    return None


def _js_find_enclosing_func_name(node, source: bytes) -> str | None:
    """Find the name of the enclosing function."""
    if node is None:
        return None
    if node.type == "arrow_function":
        parent = node.parent
        if parent and parent.type == "variable_declarator":
            for child in parent.children:
                if child.type == "identifier":
                    return _node_text(child, source)
    if node.type in ("function_declaration", "method_definition"):
        for child in node.children:
            if child.type in ("identifier", "property_identifier"):
                return _node_text(child, source)
    return None


# --- Class byte range pre-computation ---

def _build_class_byte_map(root: ts.Node, source: bytes) -> list[tuple[int, int, str]]:
    """Pre-compute all class byte ranges for a file."""
    class_q = _get_query("js_class_map", """
        (class_declaration name: (identifier) @name) @cls
    """)
    cursor = ts.QueryCursor(class_q)
    classes: list[tuple[int, int, str]] = []
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(class_q, captures)
        cls_nodes = cd.get("cls", [])
        name_nodes = cd.get("name", [])
        if cls_nodes and name_nodes:
            cls_node = cls_nodes[0]
            name = _node_text(name_nodes[0], source)
            classes.append((cls_node.start_byte, cls_node.end_byte, name))
    return classes


def _find_enclosing_class_fast(
    node: ts.Node,
    source: bytes,
    class_byte_map: list[tuple[int, int, str]],
) -> str | None:
    """Find enclosing class using pre-computed byte ranges."""
    enclosers: list[tuple[int, int, str]] = []
    for start, end, name in class_byte_map:
        if node.start_byte >= start and node.end_byte <= end:
            enclosers.append((start, end - start, name))
    if not enclosers:
        return None
    enclosers.sort(key=lambda x: x[0])
    return ".".join(name for _, _, name in enclosers)


# --- React Hooks detection ---

_JS_HOOK_NAMES = {
    "useState", "useEffect", "useContext", "useReducer",
    "useCallback", "useMemo", "useRef", "useImperativeHandle",
    "useLayoutEffect", "useDebugValue", "useDeferredValue",
    "useTransition", "useId", "useSyncExternalStore",
    "useInsertionEffect",
}


def _detect_react_hooks(
    root: ts.Node,
    source: bytes,
    file_path: str,
    result: ParsedFile,
) -> None:
    """Detect React Hook usage and mark custom hooks.

    1. Identifies calls to hook functions (use* pattern) as annotations
    2. Marks functions named use* as custom hooks
    """
    lang = _get_lang()

    # Find all hook calls within function bodies
    hook_call_q = _get_query("js_hook_call", """
        (call_expression
          function: [
            (identifier) @hook_name
            (member_expression
              property: (property_identifier) @hook_name)
          ]) @call
    """)

    # Map each function body to its hook calls
    func_body_q = ts.Query(lang, """
        [
            (function_declaration (identifier) @fname (statement_block) @body)
            (method_definition (property_identifier) @fname (statement_block) @body)
            (arrow_function (statement_block) @body)
            (function_expression (statement_block) @body)
        ]
    """)

    cursor = ts.QueryCursor(func_body_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(func_body_q, captures)
        body_nodes = cd.get("body", [])
        name_nodes = cd.get("fname", [])
        if not body_nodes:
            continue

        body_node = body_nodes[0]
        func_name = _node_text(name_nodes[0], source) if name_nodes else "anonymous"

        # Check if this function is a custom hook (use* pattern)
        if func_name.startswith("use") and len(func_name) > 3 and func_name[3:4].isupper():
            result.annotations.append(AnnotationDef(
                name="custom_hook",
                target_type="method",
                target_name=func_name,
                file_path=file_path,
                line=body_node.start_point.row + 1,
                attributes={"hookName": func_name},
            ))

        # Find hook calls within this function
        hook_cursor = ts.QueryCursor(hook_call_q)
        seen_hooks: set[str] = set()
        for _, caps in hook_cursor.matches(body_node):
            hook_cd = _captures_to_dict(hook_call_q, caps)
            hook_name_nodes = hook_cd.get("hook_name", [])
            if not hook_name_nodes:
                continue

            hname = _node_text(hook_name_nodes[0], source)

            # Skip if not a hook (doesn't start with "use")
            if not hname.startswith("use") or len(hname) < 4:
                continue

            # Check if it's a known React hook or a custom hook
            is_known = hname in _JS_HOOK_NAMES

            # Avoid duplicate annotations for the same function
            dedup_key = f"{func_name}:{hname}"
            if dedup_key in seen_hooks:
                continue
            seen_hooks.add(dedup_key)

            hook_attrs: dict[str, str] = {
                "hookName": hname,
                "isReactHook": str(is_known).lower(),
            }

            # For useEffect, extract dependency array
            if hname == "useEffect":
                call_node = hook_cd.get("call", [None])[0]
                if call_node:
                    deps = _js_extract_useeffect_deps(call_node, source)
                    if deps:
                        hook_attrs["dependencies"] = ", ".join(deps)

            result.annotations.append(AnnotationDef(
                name="uses_hook",
                target_type="method",
                target_name=func_name,
                file_path=file_path,
                line=hook_name_nodes[0].start_point.row + 1,
                attributes=hook_attrs,
            ))


def _js_extract_useeffect_deps(call_node: ts.Node, source: bytes) -> list[str] | None:
    """Extract dependency array from useEffect(fn, [dep1, dep2])."""
    for child in call_node.children:
        if child.type == "arguments":
            # Second argument should be the dependency array
            args = [c for c in child.children if c.type not in (",", "(", ")")]
            if len(args) >= 2:
                arr_node = args[1]
                if arr_node.type == "array":
                    deps = []
                    for item in arr_node.children:
                        if item.type == "identifier":
                            deps.append(_node_text(item, source))
                    return deps if deps else None
    return None
