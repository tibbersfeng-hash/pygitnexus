"""Parse TypeScript source files using tree-sitter and extract symbols."""

from __future__ import annotations

import os
import threading

import tree_sitter_typescript as tsts
import tree_sitter as ts

from .models import (
    AnnotationDef,
    CallSite,
    ClassDef,
    ConstructorDef,
    EnumDef,
    ExportDecl,
    FieldDef,
    ImportDecl,
    MethodDef,
    ParamDef,
    ParsedFile,
    TypeAliasDef,
    VariableDef,
)

# Module-level cached parsers for TS and TSX
_lang_ts: ts.Language | None = None
_lang_tsx: ts.Language | None = None
_parser_ts: ts.Parser | None = None
_parser_tsx: ts.Parser | None = None
_parser_lock = threading.Lock()
_queries_ts: dict[str, ts.Query] = {}
_queries_tsx: dict[str, ts.Query] = {}


def _get_lang(is_tsx: bool) -> ts.Language:
    global _lang_ts, _lang_tsx
    if is_tsx:
        if _lang_tsx is None:
            _lang_tsx = ts.Language(tsts.language_tsx())
        return _lang_tsx
    if _lang_ts is None:
        _lang_ts = ts.Language(tsts.language_typescript())
    return _lang_ts


def _get_parser(is_tsx: bool) -> ts.Parser:
    global _parser_ts, _parser_tsx
    if is_tsx:
        if _parser_tsx is None:
            _parser_tsx = ts.Parser(_get_lang(True))
        return _parser_tsx
    if _parser_ts is None:
        _parser_ts = ts.Parser(_get_lang(False))
    return _parser_ts


def _is_tsx(file_path: str) -> bool:
    return file_path.endswith(".tsx")


def _get_query(name: str, source: str, is_tsx: bool) -> ts.Query:
    """Get or create a cached tree-sitter query for the appropriate language."""
    cache = _queries_tsx if is_tsx else _queries_ts
    if name not in cache:
        lang = _get_lang(is_tsx)
        cache[name] = ts.Query(lang, source)
    return cache[name]


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


def _extract_type_annotation(node: ts.Node, source: bytes) -> str:
    """Extract type annotation text from a node with type_annotation child."""
    for child in node.children:
        if child.type == "type_annotation":
            # Get the type part (skip the ':')
            for sub in child.children:
                if sub.type != ":":
                    return _node_text(sub, source)
    return ""


def _extract_parameters(node: ts.Node, source: bytes, is_tsx: bool) -> list[ParamDef]:
    """Extract parameters with type annotations."""
    params = []
    for child in node.children:
        if child.type == "formal_parameters":
            param_q = _get_query("ts_param", """
                ([
                    (required_parameter)
                    (optional_parameter)
                ] @param)
            """, is_tsx)
            cursor = ts.QueryCursor(param_q)
            for _, caps in cursor.matches(child):
                cd = _captures_to_dict(param_q, caps)
                param_nodes = cd.get("param", [])
                for pnode in param_nodes:
                    # Find identifier
                    pname = ""
                    ptype = ""
                    for sub in pnode.children:
                        if sub.type == "identifier":
                            pname = _node_text(sub, source)
                        elif sub.type == "type_annotation":
                            for ts_sub in sub.children:
                                if ts_sub.type != ":":
                                    ptype = _node_text(ts_sub, source)
                        elif sub.type == "assignment_pattern":
                            for asub in sub.children:
                                if asub.type == "identifier":
                                    pname = _node_text(asub, source)
                    if pname:
                        params.append(ParamDef(name=pname, type_name=_strip_generics(ptype), raw_type=ptype))
            break
    return params


def _extract_params_simple(node: ts.Node, source: bytes, is_tsx: bool) -> list[ParamDef]:
    """Extract parameters without type annotations (fallback)."""
    params = []
    for child in node.children:
        if child.type == "formal_parameters":
            param_q = _get_query("ts_param_simple", """
                ([
                    (required_parameter)
                    (optional_parameter)
                ] @param)
            """, is_tsx)
            cursor = ts.QueryCursor(param_q)
            for _, caps in cursor.matches(child):
                cd = _captures_to_dict(param_q, caps)
                param_nodes = cd.get("param", [])
                for pnode in param_nodes:
                    for sub in pnode.children:
                        if sub.type == "identifier":
                            params.append(ParamDef(name=_node_text(sub, source), type_name=""))
                            break
                        elif sub.type == "assignment_pattern":
                            for asub in sub.children:
                                if asub.type == "identifier":
                                    params.append(ParamDef(name=_node_text(asub, source), type_name=""))
                                    break
            break
    return params


def _is_exported(node: ts.Node) -> bool:
    """Check if a node is wrapped in an export_statement."""
    parent = node.parent
    while parent:
        if parent.type == "export_statement":
            return True
        parent = parent.parent
    return False


def _extract_modifiers(node: ts.Node, source: bytes) -> str:
    """Extract modifier text from a node.

    For TS: top-level export is a wrapper, class members have modifiers like
    static, async, accessibility_modifier (public/private/protected).
    """
    text = ""
    if _is_exported(node):
        text = "export "
    # Check for direct modifier tokens
    for child in node.children:
        if child.type in ("static", "async", "abstract"):
            text += _node_text(child, source) + " "
        elif child.type == "accessibility_modifier":
            text += _node_text(child, source) + " "
    return text


def _extract_decorators(node: ts.Node, source: bytes, file_path: str, is_tsx: bool,
                        target_type: str, target_name: str, line: int) -> list:
    """Extract decorators as annotations."""
    from .models import AnnotationDef
    results = []
    dec_q = _get_query("ts_decorator", """
        (decorator (identifier) @dname)
        (decorator (call_expression function: (identifier) @dname))
    """, is_tsx)
    cursor = ts.QueryCursor(dec_q)
    for _, caps in cursor.matches(node):
        cd = _captures_to_dict(dec_q, caps)
        dname_nodes = cd.get("dname", [])
        if dname_nodes:
            results.append(AnnotationDef(
                name=_node_text(dname_nodes[0], source),
                target_type=target_type,
                target_name=target_name,
                file_path=file_path,
                line=line,
            ))
    return results


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse a single TypeScript file and extract all symbols."""
    is_tsx = _is_tsx(file_path)
    lang = _get_lang(is_tsx)
    with _parser_lock:
        parser = _get_parser(is_tsx)
        tree = parser.parse(content)

    result = ParsedFile(file_path=file_path)

    # Pre-compute class byte ranges
    class_byte_map = _build_class_byte_map(tree.root_node, content, is_tsx)

    # --- Classes ---
    class_q = _get_query("ts_class", """
        (class_declaration
          name: (type_identifier) @name
        ) @class
    """, is_tsx)
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
        implements = _extract_implements(class_node, content)

        # Extract decorators as annotations
        result.annotations.extend(
            _extract_decorators(class_node, content, file_path, is_tsx, "class", class_name,
                                class_node.start_point.row + 1)
        )

        result.classes.append(ClassDef(
            name=class_name,
            file_path=file_path,
            start_line=class_node.start_point.row + 1,
            end_line=class_node.end_point.row + 1,
            is_public="export" in modifiers_text,
            is_abstract="abstract" in modifiers_text,
            is_interface=False,
            extends=extends,
            implements=implements,
            content=_get_text(class_node, content),
        ))

    # --- Interfaces ---
    iface_q = _get_query("ts_interface", """
        (interface_declaration
          name: (type_identifier) @name
        ) @iface
    """, is_tsx)
    cursor = ts.QueryCursor(iface_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(iface_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        iface_node = cd["iface"][0]
        iface_name = _node_text(name_nodes[0], content)

        modifiers_text = _extract_modifiers(iface_node, content)

        # Extract interface extends (e.g. interface A extends B, C)
        iface_extends = _extract_interface_extends(iface_node, content)

        result.classes.append(ClassDef(
            name=iface_name,
            file_path=file_path,
            start_line=iface_node.start_point.row + 1,
            end_line=iface_node.end_point.row + 1,
            is_public="export" in modifiers_text,
            is_abstract=False,
            is_interface=True,
            extends=None,
            implements=iface_extends,  # Use implements list for interface extends
            content=_get_text(iface_node, content),
        ))

        # Extract decorators
        result.annotations.extend(
            _extract_decorators(iface_node, content, file_path, is_tsx, "class", iface_name,
                                iface_node.start_point.row + 1)
        )

        # Extract interface method signatures as methods
        sig_q = _get_query("ts_method_sig", """
            (method_signature
              name: (property_identifier) @name
              parameters: (formal_parameters) @params) @sig
        """, is_tsx)
        sig_cursor = ts.QueryCursor(sig_q)
        for _, sig_caps in sig_cursor.matches(iface_node):
            sig_cd = _captures_to_dict(sig_q, sig_caps)
            sig_name_nodes = sig_cd.get("name", [])
            sig_params_nodes = sig_cd.get("params", [])
            if sig_name_nodes:
                sig_name = _node_text(sig_name_nodes[0], content)
                sig_node = sig_cd["sig"][0]
                params = []
                return_type = _extract_type_annotation(sig_node, content)
                result.methods.append(MethodDef(
                    name=sig_name,
                    class_name=iface_name,
                    file_path=file_path,
                    start_line=sig_node.start_point.row + 1,
                    end_line=sig_node.end_point.row + 1,
                    return_type=return_type,
                    parameters=params,
                    is_static=False,
                    is_public=True,
                    is_constructor=False,
                    content=_get_text(sig_node, content),
                ))

    # --- Type Aliases ---
    type_q = _get_query("ts_type_alias", """
        (type_alias_declaration
          name: (type_identifier) @name) @type_alias
    """, is_tsx)
    cursor = ts.QueryCursor(type_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(type_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        type_node = cd["type_alias"][0]
        result.type_aliases.append(TypeAliasDef(
            name=_node_text(name_nodes[0], content),
            file_path=file_path,
            start_line=type_node.start_point.row + 1,
            end_line=type_node.end_point.row + 1,
            content=_get_text(type_node, content),
        ))

    # --- Enums ---
    enum_q = _get_query("ts_enum", """
        (enum_declaration
          name: (identifier) @name) @enum
    """, is_tsx)
    cursor = ts.QueryCursor(enum_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(enum_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        enum_node = cd["enum"][0]
        enum_name = _node_text(name_nodes[0], content)
        modifiers_text = _extract_modifiers(enum_node, content)

        result.enums.append(EnumDef(
            name=enum_name,
            file_path=file_path,
            start_line=enum_node.start_point.row + 1,
            end_line=enum_node.end_point.row + 1,
            is_const="const" in modifiers_text,
            content=_get_text(enum_node, content),
        ))

    # --- Class Methods ---
    method_q = _get_query("ts_method", """
        (method_definition
          name: (property_identifier) @name
        ) @method
    """, is_tsx)
    cursor = ts.QueryCursor(method_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(method_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        method_node = cd["method"][0]
        method_name = _node_text(name_nodes[0], content)

        # Skip constructors
        if method_name == "constructor":
            continue

        owner = _find_enclosing_class_fast(method_node, content, class_byte_map)
        modifiers_text = _extract_modifiers(method_node, content)
        params = _extract_parameters(method_node, content, is_tsx)
        return_type = _extract_type_annotation(method_node, content)

        # Extract decorators
        result.annotations.extend(
            _extract_decorators(method_node, content, file_path, is_tsx, "method", method_name,
                                method_node.start_point.row + 1)
        )

        result.methods.append(MethodDef(
            name=method_name,
            class_name=owner,
            file_path=file_path,
            start_line=method_node.start_point.row + 1,
            end_line=method_node.end_point.row + 1,
            return_type=return_type,
            parameters=params,
            is_static="static" in modifiers_text,
            is_public=True,
            is_constructor=False,
            content=_get_text(method_node, content),
        ))

    # --- Constructors ---
    ctor_q = _get_query("ts_ctor", """
        (method_definition
          name: (property_identifier) @name
        ) @ctor
    """, is_tsx)
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
        params = _extract_parameters(ctor_node, content, is_tsx)

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
    field_q = _get_query("ts_field", """
        (public_field_definition
          name: (property_identifier) @name
          type: (_) @ftype) @field
    """, is_tsx)
    cursor = ts.QueryCursor(field_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(field_q, captures)
        name_nodes = cd.get("name", [])
        type_nodes = cd.get("ftype", [])
        if not name_nodes:
            continue
        field_node = cd["field"][0]
        field_name = _node_text(name_nodes[0], content)
        field_type = _node_text(type_nodes[0], source=content) if type_nodes else ""
        owner = _find_enclosing_class_fast(field_node, content, class_byte_map)
        modifiers_text = _extract_modifiers(field_node, content)

        result.fields.append(FieldDef(
            name=field_name,
            type_name=field_type,  # Keep full type with generics
            class_name=owner or "",
            file_path=file_path,
            start_line=field_node.start_point.row + 1,
            end_line=field_node.end_point.row + 1,
            is_static="static" in modifiers_text,
            is_public=True,
        ))

    # --- Top-level Functions ---
    func_q = _get_query("ts_func", """
        (function_declaration
          name: (identifier) @name
        ) @func
    """, is_tsx)
    cursor = ts.QueryCursor(func_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(func_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        func_node = cd["func"][0]
        func_name = _node_text(name_nodes[0], content)

        params = _extract_parameters(func_node, content, is_tsx)
        return_type = _extract_type_annotation(func_node, content)
        modifiers_text = _extract_modifiers(func_node, content)

        result.annotations.extend(
            _extract_decorators(func_node, content, file_path, is_tsx, "method", func_name,
                                func_node.start_point.row + 1)
        )

        result.methods.append(MethodDef(
            name=func_name,
            class_name=None,
            file_path=file_path,
            start_line=func_node.start_point.row + 1,
            end_line=func_node.end_point.row + 1,
            return_type=return_type,
            parameters=params,
            is_static=False,
            is_public="export" in modifiers_text or True,
            is_constructor=False,
            content=_get_text(func_node, content),
        ))

    # --- Arrow/Function Expression Variables ---
    var_func_q = _get_query("ts_var_func", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @name
            value: [
                (arrow_function)
                (function_expression)
            ] @body
          )
        ) @decl
    """, is_tsx)
    cursor = ts.QueryCursor(var_func_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(var_func_q, captures)
        name_nodes = cd.get("name", [])
        body_nodes = cd.get("body", [])
        if not name_nodes or not body_nodes:
            continue
        func_name = _node_text(name_nodes[0], content)
        body_node = body_nodes[0]

        params = _extract_params_simple(body_node, content, is_tsx)
        return_type = _extract_type_annotation(body_node, content)
        modifiers_text = _extract_modifiers(cd.get("decl", [None])[0], content) if cd.get("decl") else ""

        result.methods.append(MethodDef(
            name=func_name,
            class_name=None,
            file_path=file_path,
            start_line=body_node.start_point.row + 1,
            end_line=body_node.end_point.row + 1,
            return_type=return_type,
            parameters=params,
            is_static=False,
            is_public="export" in modifiers_text or True,
            is_constructor=False,
            content=_get_text(body_node, content),
        ))

    # --- Imports ---
    _extract_imports(tree.root_node, content, is_tsx, result)

    # --- Exports ---
    _extract_exports(tree.root_node, content, is_tsx, result)

    # --- Method calls ---
    _extract_calls(tree.root_node, content, lang, is_tsx, file_path, result, class_byte_map)

    # --- Module-level calls (top-level code outside any function) ---
    _extract_module_level_calls(tree.root_node, content, lang, is_tsx, file_path, result)

    # --- React Hooks detection ---
    _detect_react_hooks(tree.root_node, content, is_tsx, file_path, result)

    # --- HTTP endpoint extraction (for API layer files) ---
    _extract_http_endpoints(tree.root_node, content, is_tsx, file_path, result)

    # --- Local variables ---
    result.variables = _extract_variables(tree.root_node, content, is_tsx, file_path, class_byte_map)

    # --- Field accesses (minimal for TS) ---
    result.field_accesses = []

    return result


def _extract_extends(class_node: ts.Node, source: bytes) -> str | None:
    """Extract the superclass."""
    for child in class_node.children:
        if child.type == "class_heritage":
            for sub in child.children:
                if sub.type == "extends_clause":
                    for item in sub.children:
                        if item.type in ("type_identifier", "identifier", "predefined_type"):
                            return _node_text(item, source)
    return None


def _extract_interface_extends(iface_node: ts.Node, source: bytes) -> list[str]:
    """Extract interfaces that an interface extends.

    e.g. interface User extends BaseUser, Timestamped
    """
    results = []
    for child in iface_node.children:
        if child.type == "interface_heritage":
            for sub in child.children:
                if sub.type == "extends_clause":
                    for item in sub.children:
                        if item.type in ("type_identifier", "identifier", "predefined_type",
                                          "generic_type"):
                            results.append(_node_text(item, source))
    return results


def _extract_implements(class_node: ts.Node, source: bytes) -> list[str]:
    """Extract implemented interfaces."""
    results = []
    for child in class_node.children:
        if child.type == "class_heritage":
            for sub in child.children:
                if sub.type == "implements_clause":
                    for item in sub.children:
                        if item.type in ("type_identifier", "identifier", "predefined_type",
                                          "generic_type"):
                            results.append(_node_text(item, source))
    return results


def _extract_imports(root: ts.Node, source: bytes, is_tsx: bool, result: ParsedFile) -> None:
    """Extract ES module imports."""
    import_q = _get_query("ts_import", """
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
    """, is_tsx)
    cursor = ts.QueryCursor(import_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(import_q, caps)
        source_nodes = cd.get("source", [])
        if not source_nodes:
            continue
        module_name = _node_text(source_nodes[0], source).strip("'\"`")

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

    # CommonJS require
    require_q = _get_query("ts_require", """
        (call_expression
          function: (identifier) @func
          arguments: (arguments (string) @mod))
    """, is_tsx)
    cursor = ts.QueryCursor(require_q)
    seen: set[str] = set()
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(require_q, caps)
        func_nodes = cd.get("func", [])
        mod_nodes = cd.get("mod", [])
        if not func_nodes or not mod_nodes:
            continue
        if _node_text(func_nodes[0], source) != "require":
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

    # Dynamic import(): import("module.ts")
    dynamic_import_q = _get_query("ts_dynamic_import", """
        (call_expression
          function: (import)
          arguments: (arguments (string) @mod))
    """, is_tsx)
    cursor = ts.QueryCursor(dynamic_import_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(dynamic_import_q, caps)
        mod_nodes = cd.get("mod", [])
        if not mod_nodes:
            continue
        module = _node_text(mod_nodes[0], source).strip("'\"`")
        mod_name = module.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if mod_name:
            result.imports.append(ImportDecl(
                qualified_name=f"{module}.{mod_name}",
                is_wildcard=False,
                file_path=result.file_path,
            ))


def _extract_exports(root: ts.Node, source: bytes, is_tsx: bool, result: ParsedFile) -> None:
    """Extract export declarations including re-exports."""
    export_q = _get_query("ts_export", """
        (export_statement) @export
    """, is_tsx)
    cursor = ts.QueryCursor(export_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(export_q, caps)
        export_nodes = cd.get("export", [])
        if not export_nodes:
            continue
        export_node = export_nodes[0]

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

        for child in export_node.children:
            if child.type == "default":
                kind = "default"
                name = "default"
                break
            elif child.type == "class_declaration":
                kind = "class"
                for sub in child.children:
                    if sub.type == "type_identifier":
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
                # export { foo, bar } (without 'from') — named exports of existing declarations
                for sub in child.children:
                    if sub.type == "export_specifier":
                        for name_child in sub.children:
                            if name_child.type == "identifier":
                                sym_name = _node_text(name_child, source)
                                result.exports.append(ExportDecl(
                                    name=sym_name,
                                    kind="variable",
                                    file_path=result.file_path,
                                    line=name_child.start_point.row + 1,
                                ))
                continue

        if name:
            result.exports.append(ExportDecl(
                name=name,
                kind=kind,
                file_path=result.file_path,
                line=line,
            ))


def _extract_calls(
    root: ts.Node,
    source: bytes,
    lang: ts.Language,
    is_tsx: bool,
    file_path: str,
    result: ParsedFile,
    class_byte_map: list[tuple[int, int, str]],
) -> None:
    """Extract method calls from all functions and methods.

    For member expressions like `api.get()`, if `api` is an imported symbol,
    the `target_name` is set to `api.get` so the resolver can match it to
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

    call_q = _get_query("ts_call", """
        (call_expression
          function: [
            (identifier) @call_name
            (member_expression
              property: (property_identifier) @call_name)
          ]
        ) @call
    """, is_tsx)

    # Find all function bodies (statement block style)
    # Note: child patterns must appear in document order
    func_body_q = _get_query("ts_func_body", """
        [
            (function_declaration (identifier) @fname (statement_block) @body)
            (method_definition (property_identifier) @fname (statement_block) @body)
            (function_expression (identifier)? @fname (statement_block) @body)
        ]
    """, is_tsx)

    # Separate query for arrow functions with statement_block:
    # Need to capture the enclosing variable_declarator name
    arrow_block_q = _get_query("ts_arrow_block", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @fname
            value: (arrow_function (statement_block) @body)))
    """, is_tsx)

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

        type_map: dict[str, str] = _build_type_map(body_node, source, is_tsx)

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

            result.calls.append(CallSite(
                caller_method=fq_method,
                caller_class=enclosing_class or "",
                target_name=target_name,
                line=call_node.start_point.row + 1,
                receiver=receiver,
                receiver_type=receiver_type,
            ))

    # Arrow functions with statement_block (e.g. `const fn = () => { ... }`)
    # Need separate handling since the name is in the parent variable_declarator
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

        type_map: dict[str, str] = _build_type_map(body_node, source, is_tsx)

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

            result.calls.append(CallSite(
                caller_method=fq_method,
                caller_class=enclosing_class or "",
                target_name=target_name,
                line=call_node.start_point.row + 1,
                receiver=receiver,
                receiver_type=receiver_type,
            ))

    # Arrow functions with expression bodies (no statement_block):
    arrow_expr_q = _get_query("ts_arrow_expr", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @fname
            value: (arrow_function) @body)) @decl
    """, is_tsx)
    cursor = ts.QueryCursor(arrow_expr_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(arrow_expr_q, captures)
        name_nodes = cd.get("fname", [])
        body_nodes = cd.get("body", [])
        if not name_nodes or not body_nodes:
            continue

        func_name = _node_text(name_nodes[0], source)
        body_node = body_nodes[0]

        # Only process arrow functions without statement_block (expression body)
        # If it has a statement_block, it's already handled above
        has_block = any(c.type == "statement_block" for c in body_node.children)
        if has_block:
            continue

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

            # Extract receiver from member_expression
            receiver = None
            receiver_type = None
            target_name = call_name
            for child in call_node.children:
                if child.type == "member_expression":
                    obj = child.child_by_field_name("object")
                    if obj:
                        receiver = _node_text(obj, source)
                        # If receiver is an imported symbol, use full chain name
                        if receiver in imported_names:
                            target_name = f"{receiver}.{call_name}"
                    break

            result.calls.append(CallSite(
                caller_method=func_name,
                caller_class="",
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
    # to avoid double-counting with the arrow_block_q above
    processed_arrow_bodies: set[int] = set()
    # Collect byte ranges from arrow_block_q processed bodies
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

    # Arrow functions with parenthesized_expression bodies:
    # e.g. `state: () => ({ ... getItem() ... })` in Pinia stores
    # The body is a parenthesized expression containing an object literal
    paren_arrow_q = _get_query("ts_paren_arrow", """
        (arrow_function (parenthesized_expression) @pbody)
    """, is_tsx)
    paren_cursor = ts.QueryCursor(paren_arrow_q)
    for _, captures in paren_cursor.matches(root):
        pd = _captures_to_dict(paren_arrow_q, captures)
        pbody_nodes = pd.get("pbody", [])
        if not pbody_nodes:
            continue
        pbody = pbody_nodes[0]

        # Find function name by walking up to variable_declarator or property_identifier
        enclosing_func = None
        parent = pbody.parent
        while parent:
            if parent.type == "variable_declarator":
                for child in parent.children:
                    if child.type in ("identifier", "property_identifier"):
                        enclosing_func = _node_text(child, source)
                        break
                break
            elif parent.type == "pair":
                for child in parent.children:
                    if child.type in ("property_identifier", "identifier"):
                        enclosing_func = _node_text(child, source)
                        break
                break
            parent = parent.parent

        caller = enclosing_func if enclosing_func else os.path.basename(file_path)

        call_cursor = ts.QueryCursor(call_q)
        seen_paren: set[tuple[int, str]] = set()
        for _, caps in call_cursor.matches(pbody):
            call_cd = _captures_to_dict(call_q, caps)
            call_nodes = call_cd.get("call", [])
            call_name_nodes = call_cd.get("call_name", [])
            if not call_nodes or not call_name_nodes:
                continue
            call_node = call_nodes[0]
            call_name = _node_text(call_name_nodes[0], source)

            dedup_key = (call_node.start_byte, call_name)
            if dedup_key in seen_paren:
                continue
            seen_paren.add(dedup_key)

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
                caller_method=caller,
                caller_class="",
                target_name=target_name,
                line=call_node.start_point.row + 1,
                receiver=receiver,
                receiver_type=receiver_type,
            ))

    # new expressions
    new_q = _get_query("ts_new", """
        (new_expression
          constructor: (identifier) @ctor_name) @new_call
    """, is_tsx)
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


def _extract_module_level_calls(
    root: ts.Node,
    source: bytes,
    lang: ts.Language,
    is_tsx: bool,
    file_path: str,
    result: ParsedFile,
) -> None:
    """Extract calls from module-level (top-level) code outside any function body.

    Uses a synthetic caller name based on the file (e.g., 'index.ts' for router/index.ts).
    """
    module_name = os.path.basename(file_path)

    # Find all top-level nodes that are NOT function/class declarations
    # We look for call expressions that are direct children of the program root
    # or inside lexical_declaration/variable_declaration at the top level
    call_q = _get_query("ts_module_call", """
        (call_expression
          function: [
            (identifier) @call_name
            (member_expression
              property: (property_identifier) @call_name)
          ]
        ) @call
    """, is_tsx)

    # Find imported names for receiver resolution (same as _extract_calls)
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

    # Build type map from top-level variable declarations
    type_map: dict[str, str] = {}
    lv_q = _get_query("ts_lv", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @vname
            value: (_) @vtype))
    """, is_tsx)
    cursor = ts.QueryCursor(lv_q)
    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(lv_q, caps)
        name_nodes = cd.get("vname", [])
        type_nodes = cd.get("vtype", [])
        if name_nodes and type_nodes:
            val_node = type_nodes[0]
            inferred = ""
            if val_node.type == "new_expression":
                for sub in val_node.children:
                    if sub.type == "identifier":
                        inferred = _node_text(sub, source)
                        break
            elif val_node.type == "call_expression":
                for sub in val_node.children:
                    if sub.type == "identifier":
                        inferred = _node_text(sub, source)
                        break
            type_map[_node_text(name_nodes[0], source)] = inferred

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

        # Skip if this call is inside a function body, class, or method
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
                    if receiver in type_map:
                        pass  # type_map for module-level
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


def _build_type_map(body_node: ts.Node, source: bytes, is_tsx: bool) -> dict[str, str]:
    """Build a mapping of variable names to types."""
    type_map: dict[str, str] = {}
    lv_q = _get_query("ts_lv", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @vname
            value: (_) @vtype))
    """, is_tsx)
    cursor = ts.QueryCursor(lv_q)
    for _, caps in cursor.matches(body_node):
        cd = _captures_to_dict(lv_q, caps)
        name_nodes = cd.get("vname", [])
        type_nodes = cd.get("vtype", [])
        if name_nodes:
            var_name = _node_text(name_nodes[0], source)
            # Infer type from value (e.g., new Foo() -> Foo)
            inferred = ""
            if type_nodes:
                val_node = type_nodes[0]
                if val_node.type == "new_expression":
                    for sub in val_node.children:
                        if sub.type == "identifier":
                            inferred = _node_text(sub, source)
                            break
                elif val_node.type == "call_expression":
                    for sub in val_node.children:
                        if sub.type == "identifier":
                            inferred = _node_text(sub, source)
                            break
            type_map[var_name] = inferred
    return type_map


def _extract_variables(
    root: ts.Node,
    source: bytes,
    is_tsx: bool,
    file_path: str,
    class_byte_map: list[tuple[int, int, str]],
) -> list[VariableDef]:
    """Extract local variable declarations with type annotations."""
    results: list[VariableDef] = []
    lv_q = _get_query("ts_var_extract", """
        (lexical_declaration
          (variable_declarator
            name: (identifier) @vname)) @decl
    """, is_tsx)
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

    return results


# --- HTTP endpoint extraction ---

_HTTP_METHOD_MAP = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "head": "HEAD",
    "options": "OPTIONS",
}

# Recognized HTTP client variable names
_HTTP_CLIENT_NAMES = {
    "api", "http", "httpClient", "axios", "axiosInstance",
    "request", "client", "fetcher", "httpService", "apiClient",
    "axiosClient", "httpInstance", "requestClient",
}


def _extract_http_endpoints(
    root: ts.Node,
    source: bytes,
    is_tsx: bool,
    file_path: str,
    result: ParsedFile,
) -> None:
    """Extract HTTP API endpoint info from API layer files.

    Supports:
    1. api.get('/path', params) — custom HTTP client
    2. axios.get('/path') — axios instance
    3. fetch('/path', { method: 'POST' }) — native fetch
    """
    # Pattern 1 & 2: member_expression call — api.get() / axios.get()
    http_call_q = _get_query("ts_http_call", """
        (call_expression
          function: (member_expression
            property: (property_identifier) @http_method)
          arguments: (arguments
            [
              (string) @path_string
              (template_string) @path_template
            ])) @call
    """, is_tsx)

    # Pattern 3: fetch('/path', { method: 'POST', body: ... })
    fetch_call_q = _get_query("ts_fetch_call", """
        (call_expression
          function: (identifier) @fetch_name
          arguments: (arguments
            [
              (string) @path_string
              (template_string) @path_template
            ])) @call
    """, is_tsx)

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
        http_verb = _HTTP_METHOD_MAP.get(http_method)
        if not http_verb:
            continue

        # Extract path value
        path_value = None
        if path_nodes:
            path_value = _node_text(path_nodes[0], source).strip("'\"`")
        elif template_nodes:
            path_value = _node_text(template_nodes[0], source).strip("`")

        if not path_value:
            continue

        # Check if the receiver is a recognized HTTP client
        call_node = call_nodes[0] if call_nodes else None
        is_http_client = False
        if call_node:
            for child in call_node.children:
                if child.type == "member_expression":
                    obj = child.child_by_field_name("object")
                    if obj:
                        receiver_name = _node_text(obj, source)
                        if receiver_name in _HTTP_CLIENT_NAMES:
                            is_http_client = True
                    break

        if not is_http_client:
            continue

        # Find the full call_expression node
        call_expr_node = call_node

        # Extract request parameters from the second argument
        http_params = _extract_http_params(call_expr_node, source, is_tsx)

        # Find which function/method this call is inside
        call_node_for_search = cd.get("http_method", [None])[0]
        if call_node_for_search is None:
            continue
        func_node = _find_enclosing_function(call_node_for_search)
        caller_name = _find_enclosing_func_name(func_node, source)

        # Find the matching CallSite and enrich it with http info
        target_line = (path_nodes[0].start_point.row + 1 if path_nodes else
                       template_nodes[0].start_point.row + 1 if template_nodes else 0)
        for call in result.calls:
            if (caller_name and call.caller_method == caller_name) or \
               (call.line == target_line):
                # Also check receiver matches an HTTP client
                if call.receiver and call.receiver in _HTTP_CLIENT_NAMES:
                    call.http_method = http_verb
                    call.http_path = path_value
                    if http_params:
                        import json
                        call.http_params = json.dumps(http_params)
                    break

    # Pattern 3: fetch() — native fetch API
    fetch_cursor = ts.QueryCursor(fetch_call_q)
    for _, caps in fetch_cursor.matches(root):
        cd = _captures_to_dict(fetch_call_q, caps)
        fetch_name_nodes = cd.get("fetch_name", [])
        path_nodes = cd.get("path_string", [])
        template_nodes = cd.get("path_template", [])
        call_nodes = cd.get("call", [])

        if not fetch_name_nodes:
            continue
        fetch_name = _node_text(fetch_name_nodes[0], source)
        if fetch_name != "fetch":
            continue

        # Extract path value
        path_value = None
        if path_nodes:
            path_value = _node_text(path_nodes[0], source).strip("'\"`")
        elif template_nodes:
            path_value = _node_text(template_nodes[0], source).strip("`")

        if not path_value:
            continue

        # Extract HTTP method from options object
        call_node = call_nodes[0] if call_nodes else None
        http_verb = "GET"  # default for fetch
        http_params = None
        if call_node:
            args_node = None
            for child in call_node.children:
                if child.type == "arguments":
                    args_node = child
                    break
            if args_node:
                arg_children = [c for c in args_node.children if c.type not in (",", "(", ")")]
                if len(arg_children) >= 2:
                    # Second argument: { method: 'POST', ... }
                    options_arg = arg_children[1]
                    http_verb, http_params = _extract_fetch_options(options_arg, source, is_tsx)

        # Find enclosing function
        call_node_for_search = fetch_name_nodes[0]
        func_node = _find_enclosing_function(call_node_for_search)
        caller_name = _find_enclosing_func_name(func_node, source)

        target_line = (path_nodes[0].start_point.row + 1 if path_nodes else
                       template_nodes[0].start_point.row + 1 if template_nodes else 0)
        for call in result.calls:
            if (caller_name and call.caller_method == caller_name) or \
               (call.line == target_line and call.target_name == "fetch"):
                call.http_method = http_verb
                call.http_path = path_value
                if http_params:
                    import json
                    call.http_params = json.dumps(http_params)
                break


def _extract_fetch_options(
    node: ts.Node,
    source: bytes,
    is_tsx: bool,
) -> tuple[str, dict[str, str] | None]:
    """Extract HTTP method and params from fetch options object.

    Returns (http_method, params_dict).
    """
    if node.type == "object":
        method = "GET"
        params = {}
        for child in node.children:
            if child.type == "pair":
                key_node = None
                value_node = None
                for sub in child.children:
                    if sub.type in ("property_identifier", "string"):
                        key_node = sub
                    elif sub.type == ":":
                        pass
                    else:
                        value_node = sub

                if key_node and value_node:
                    key = _node_text(key_node, source).strip("'\"")
                    if key == "method":
                        method = _extract_scalar_value(value_node, source) or "GET"
                    else:
                        value = _extract_scalar_value(value_node, source)
                        if value is not None:
                            params[key] = value
        return method, params if params else None

    return "GET", None


def _extract_http_params(
    call_expr_node: ts.Node | None,
    source: bytes,
    is_tsx: bool,
) -> dict[str, str] | None:
    """Extract request parameters from an HTTP call like api.post('/path', params).

    Extracts from the second argument of the call:
    1. Object literal: `{ action: "create", key: value }` -> {"action": "create"}
    2. Variable reference: `body`, `fields` -> infer from function parameters
    3. Ternary: `cond ? {a: 1} : undefined` -> {"a": "1"} (take truthy branch)
    """
    if call_expr_node is None:
        return None

    args_node = None
    for child in call_expr_node.children:
        if child.type == "arguments":
            args_node = child
            break

    if args_node is None:
        return None

    # Get all arguments
    arg_children = [c for c in args_node.children if c.type not in (",", "(", ")")]
    if len(arg_children) < 2:
        return None

    # Second argument (index 1) is the request body/params
    body_arg = arg_children[1]

    result = _extract_param_value(body_arg, source, is_tsx)
    return result


def _extract_param_value(
    node: ts.Node,
    source: bytes,
    is_tsx: bool,
) -> dict[str, str] | None:
    """Extract parameter dict from a node."""
    # Object literal: { key: value, ... }
    if node.type == "object":
        return _extract_object_literal(node, source)

    # Variable reference: body, params, fields
    if node.type == "identifier":
        var_name = _node_text(node, source)
        # Find enclosing function and extract param type
        func_node = _find_enclosing_function(node)
        if func_node:
            param_info = _extract_function_param_type(func_node, var_name, source, is_tsx)
            if param_info:
                return param_info

    # Ternary: cond ? {a: 1} : undefined
    if node.type == "ternary_expression":
        truthy_node = None
        for child in node.children:
            if child.type != "?" and child.type != ":":
                if truthy_node is None:
                    truthy_node = child  # condition
                elif truthy_node is not None:
                    truthy_node = child  # truthy branch - we want this
                    break
        if truthy_node:
            return _extract_param_value(truthy_node, source, is_tsx)

    return None


def _extract_object_literal(
    node: ts.Node,
    source: bytes,
) -> dict[str, str]:
    """Extract key-value pairs from an object literal."""
    result = {}
    for child in node.children:
        if child.type == "pair":
            key_node = None
            value_node = None
            for sub in child.children:
                if sub.type in ("property_identifier", "string"):
                    key_node = sub
                elif sub.type == ":":
                    pass
                else:
                    value_node = sub

            if key_node and value_node:
                key = _node_text(key_node, source).strip("'\"")
                value = _extract_scalar_value(value_node, source)
                if value is not None:
                    result[key] = value

    return result


def _extract_scalar_value(node: ts.Node, source: bytes) -> str | None:
    """Extract a scalar value from a node (string, number, bool, template)."""
    if node.type == "string":
        return _node_text(node, source).strip("'\"`")
    if node.type == "number":
        return _node_text(node, source)
    if node.type == "true":
        return "true"
    if node.type == "false":
        return "false"
    if node.type == "null":
        return "null"
    if node.type == "identifier":
        return _node_text(node, source)  # variable ref
    if node.type == "template_string":
        return _node_text(node, source).strip("`")
    return None


def _find_enclosing_function(node: ts.Node) -> ts.Node | None:
    """Find the enclosing function for a node."""
    current = node.parent
    while current:
        if current.type in (
            "arrow_function", "function_declaration",
            "function_expression", "method_definition",
        ):
            return current
        current = current.parent
    return None


def _extract_function_param_type(
    func_node: ts.Node,
    param_name: str,
    source: bytes,
    is_tsx: bool,
) -> dict[str, str] | None:
    """Extract type info from a function parameter matching the given name.

    For typed TS params like `body: Partial<CronJob>`, returns {"type": "Partial<CronJob>"}.
    For params with destructuring, extracts the keys.
    """
    for child in func_node.children:
        if child.type == "formal_parameters":
            for param_child in child.children:
                if param_child.type in ("required_parameter", "optional_parameter"):
                    # Find the identifier
                    found_name = False
                    for sub in param_child.children:
                        if sub.type == "identifier" and _node_text(sub, source) == param_name:
                            found_name = True
                        elif sub.type == "type_annotation" and found_name:
                            # Get the type string
                            for ts_sub in sub.children:
                                if ts_sub.type != ":":
                                    return {"type": _node_text(ts_sub, source)}
                            break
                        elif found_name and sub.type == "object_pattern":
                            # Destructuring: { key1, key2 } = params
                            return _extract_destructure_keys(sub, source)

    return None


def _extract_destructure_keys(
    node: ts.Node,
    source: bytes,
) -> dict[str, str]:
    """Extract keys from a destructuring pattern like { key1, key2 }."""
    keys = []
    for child in node.children:
        if child.type == "shorthand_property_identifier":
            keys.append(_node_text(child, source))
        elif child.type == "pair_pattern":
            for sub in child.children:
                if sub.type == "property_identifier":
                    keys.append(_node_text(sub, source))
    return {"keys": ", ".join(keys)} if keys else None


def _find_call_expression(
    method_node: ts.Node | None,
    source: bytes,
) -> ts.Node | None:
    """Find the parent call_expression for a property_identifier node."""
    if method_node is None:
        return None
    current = method_node.parent
    while current:
        if current.type == "member_expression":
            current = current.parent
        elif current.type == "call_expression":
            return current
        else:
            current = current.parent
    return None


def _find_enclosing_func_name(node: ts.Node | None, source: bytes) -> str | None:
    """Find the name of the enclosing function for a node."""
    if node is None:
        return None

    # Check if this is a lexical_declaration wrapping
    if node.type == "arrow_function":
        parent = node.parent
        if parent and parent.type == "variable_declarator":
            for child in parent.children:
                if child.type == "identifier":
                    return _node_text(child, source)

    # For method_definition or function_declaration
    if node.type in ("function_declaration", "method_definition"):
        for child in node.children:
            if child.type in ("identifier", "property_identifier"):
                return _node_text(child, source)

    return None


# --- React Hooks detection ---

_HOOK_NAMES = {
    "useState", "useEffect", "useContext", "useReducer",
    "useCallback", "useMemo", "useRef", "useImperativeHandle",
    "useLayoutEffect", "useDebugValue", "useDeferredValue",
    "useTransition", "useId", "useSyncExternalStore",
    "useInsertionEffect",
}


def _detect_react_hooks(
    root: ts.Node,
    source: bytes,
    is_tsx: bool,
    file_path: str,
    result: ParsedFile,
) -> None:
    """Detect React Hook usage and mark custom hooks.

    1. Identifies calls to hook functions (use* pattern) as annotations
    2. Marks functions named use* as custom hooks
    """
    lang = _get_lang(is_tsx)

    # Find all hook calls within function bodies
    hook_call_q = _get_query("ts_hook_call", """
        (call_expression
          function: [
            (identifier) @hook_name
            (member_expression
              property: (property_identifier) @hook_name)
          ]) @call
    """, is_tsx)

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
        func_name = _node_text(name_nodes[0], source) if name_nodes else os.path.basename(file_path)

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
            is_known = hname in _HOOK_NAMES

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
                    deps = _extract_useeffect_deps(call_node, source)
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


def _extract_useeffect_deps(call_node: ts.Node, source: bytes) -> list[str] | None:
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


# --- Class byte range pre-computation ---

def _build_class_byte_map(root: ts.Node, source: bytes, is_tsx: bool) -> list[tuple[int, int, str]]:
    """Pre-compute all class byte ranges for a file."""
    class_q = _get_query("ts_class_map", """
        (class_declaration name: (type_identifier) @name) @cls
    """, is_tsx)
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
