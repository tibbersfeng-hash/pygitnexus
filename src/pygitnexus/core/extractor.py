"""Parse Java source files using tree-sitter and extract symbols."""

from __future__ import annotations

import sys
import threading

import tree_sitter_java as tsjava
import tree_sitter as ts

from .models import (
    AnnotationDef,
    CallSite,
    ClassDef,
    ConstructorDef,
    FieldAccess,
    FieldDef,
    ImportDecl,
    MethodDef,
    ParamDef,
    ParsedFile,
    VariableDef,
)

# Module-level cached parser and queries (compiled once)
_lang: ts.Language | None = None
_parser: ts.Parser | None = None
_parser_lock = threading.Lock()

# Query pairs: (query, cursor_factory) - cursor is created per-file
_queries: dict[str, ts.Query] = {}


def _get_lang() -> ts.Language:
    global _lang
    if _lang is None:
        _lang = ts.Language(tsjava.language())
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
    """Convert tree-sitter captures. In >=0.23 captures is already a dict."""
    if isinstance(captures, dict):
        return captures
    result: dict[str, list[ts.Node]] = {}
    for capture_index, node in captures:
        name = query.capture_names[capture_index]
        result.setdefault(name, []).append(node)
    return result


# Mapping from Java functional method name to type parameter index.
# Used for Strategy 1 (receiver chain + generic propagation) to infer
# lambda parameter types from enclosing method invocation's generic arguments.
# Index 0 = first type parameter (e.g., Predicate<T> -> T)
_FUNCTIONAL_METHOD_TYPE_PARAM: dict[str, int] = {
    # Stream operations
    "filter": 0, "map": 0, "flatMap": 0, "peek": 0,
    "forEach": 0, "forEachOrdered": 0,
    "anyMatch": 0, "allMatch": 0, "noneMatch": 0,
    "findFirst": 0, "findAny": 0, "reduce": 0, "collect": 0, "sorted": 0,
    # Collection operations
    "removeIf": 0, "replaceAll": 0, "sort": 0,
    # Optional operations
    "ifPresent": 0, "ifPresentOrElse": 0, "or": 0,
    # Comparator factories
    "comparing": 0, "thenComparing": 0,
}


def _strip_generics(type_str: str) -> str:
    if "<" in type_str:
        return type_str.split("<")[0].strip()
    return type_str


def _trace_builder_chain(node: ts.Node, source: bytes, type_map: dict[str, str]) -> str | None:
    """Trace a method_invocation chain down to find the builder's target type.

    For patterns like:
      ProxySelectorDO.builder().name("x").build()
    When resolving `.build()`, the receiver is the inner method_invocation chain.
    Walk DOWN to find the root: if it starts with Type.method() where method
    is a builder factory, return the Type.

    Returns the inferred builder target type, or None.
    """
    builder_methods = {"builder", "newBuilder", "newBuilderInstance",
                       "newSelectorBuilder", "newRuleBuilder", "newConditions",
                       "newBindingData", "newDivideRuleHandle", "newUpstreamsBuilder",
                       "newCondition", "divideUpstreams"}

    # Walk DOWN the chain of nested method_invocations to find the root
    current = node
    while True:
        # Find the receiver: first child that is method_invocation, field_access, or identifier
        receiver = None
        method_name = None
        for child in current.children:
            if child.type == "argument_list":
                # The identifier before argument_list is the method name
                children_list = list(current.children)
                idx = children_list.index(child)
                if idx >= 2:
                    prev = children_list[idx - 1]
                    if prev.type == ".":
                        prev = children_list[idx - 2]
                    method_name = prev
                break

        for child in current.children:
            if child.type in ("method_invocation", "field_access"):
                # Make sure this is the receiver, not the method name
                if method_name and child.id == method_name.id:
                    continue
                receiver = child
                break
            elif child.type == "identifier" and method_name and child.id != method_name.id:
                receiver = child
                break

        if receiver is None:
            break

        if receiver.type == "method_invocation":
            current = receiver
            continue
        elif receiver.type == "field_access":
            obj = receiver.child_by_field_name("object")
            if obj and obj.type == "type_identifier":
                return _node_text(obj, source)
            elif obj and obj.type == "identifier":
                obj_name = _node_text(obj, source)
                if obj_name[0:1].isupper():
                    # Looks like a type identifier (e.g., ProxySelectorDO)
                    return obj_name
            break
        elif receiver.type == "identifier":
            recv_name = _node_text(receiver, source)
            # Check if it looks like a type (starts with uppercase, not in type_map)
            if recv_name[0:1].isupper() and recv_name not in type_map:
                return recv_name
            break

    return None


def _extract_implements(class_node: ts.Node, source: bytes) -> list[str]:
    """Extract implemented interfaces from a class node."""
    results = []

    def _walk(node: ts.Node) -> None:
        if node.type == "type_list":
            for child in node.children:
                if child.type == "type_identifier":
                    results.append(_node_text(child, source))
                elif child.type == "generic_type":
                    # generic_type wraps type_identifier + type_arguments
                    for gc in child.children:
                        if gc.type == "type_identifier":
                            results.append(_node_text(gc, source))
                            break
            return
        for child in node.children:
            _walk(child)

    _walk(class_node)
    return results


def _extract_method_return_type(method_node: ts.Node, source: bytes) -> str:
    """Extract return type from method_declaration node."""
    for child in method_node.children:
        if child.type == "type_identifier":
            return _node_text(child, source)
        elif child.type == "generic_type":
            # Generic type: extract the base type identifier (e.g., ApiResponse from ApiResponse<T>)
            for gc in child.children:
                if gc.type == "type_identifier":
                    return _node_text(gc, source)
            break
        elif child.type == "array_type":
            # Array type: extract element type + brackets (e.g., String[], int[][])
            parts: list[str] = []
            for ac in child.children:
                if ac.type == "type_identifier":
                    parts.append(_node_text(ac, source))
                elif ac.type == "bracketed_type":
                    # e.g., String[][] has nested brackets
                    parts.append("[]")
                elif ac.type == "_type" or ac.type == "integral_type":
                    parts.append(_node_text(ac, source))
            return "".join(parts) if parts else "void"
    return "void"


def _extract_parameters(formal_params_node: ts.Node, source: bytes) -> list[ParamDef]:
    """Extract parameters from a formal_parameters node."""
    params = []
    query = _get_query("params", """
        (formal_parameter
          type: (_) @ptype
          name: (identifier) @pname)
    """)
    cursor = ts.QueryCursor(query)
    for _, captures in cursor.matches(formal_params_node):
        cd = _captures_to_dict(query, captures)
        type_nodes = cd.get("ptype", [])
        name_nodes = cd.get("pname", [])
        if type_nodes and name_nodes:
            raw = _node_text(type_nodes[0], source)
            pt = _strip_generics(raw)
            pn = _node_text(name_nodes[0], source)
            params.append(ParamDef(name=pn, type_name=pt, raw_type=raw))
    return params


def _extract_modifiers(node: ts.Node, source: bytes) -> str:
    """Extract modifier text from a node."""
    query = _get_query("modifiers", "(modifiers) @mod")
    cursor = ts.QueryCursor(query)
    text = ""
    for _, mod_caps in cursor.matches(node):
        mod_cd = _captures_to_dict(query, mod_caps)
        for mn in mod_cd.get("mod", []):
            text += _node_text(mn, source) + " "
    return text


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse a single Java file and extract all symbols."""
    # Tree-sitter query cursor recursively walks the AST — raise limit to
    # accommodate deeply nested files (e.g. XmlToJson with 100+ if/else levels).
    sys.setrecursionlimit(10000)
    lang = _get_lang()
    with _parser_lock:
        parser = _get_parser()
        tree = parser.parse(content)

    result = ParsedFile(file_path=file_path)
    package = _extract_package(content, lang, tree.root_node)

    # Pre-compute class and method byte ranges for fast lookups
    class_byte_map = _build_class_byte_map(tree.root_node, content)
    method_byte_map = _build_method_byte_map(tree.root_node, content)

    # --- Classes ---
    class_q = _get_query("class", """
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
        fqn = f"{package}.{class_name}" if package else class_name

        modifiers_text = _extract_modifiers(class_node, content)

        extends = None
        ext_q = _get_query("extends", "(superclass (type_identifier) @ext)")
        ext_cursor = ts.QueryCursor(ext_q)
        for _, ext_caps in ext_cursor.matches(class_node):
            ext_cd = _captures_to_dict(ext_q, ext_caps)
            if ext_cd.get("ext"):
                extends = _node_text(ext_cd["ext"][0], content)
                break
        implements = _extract_implements(class_node, content)

        result.classes.append(ClassDef(
            name=fqn,
            file_path=file_path,
            start_line=class_node.start_point.row + 1,
            end_line=class_node.end_point.row + 1,
            is_public="public" in modifiers_text,
            is_abstract="abstract" in modifiers_text,
            is_interface=False,
            extends=extends,
            implements=implements,
            content=_get_text(class_node, content),
        ))

    # --- Interfaces ---
    iface_q = _get_query("interface", """
        (interface_declaration
          name: (identifier) @name
        ) @interface
    """)
    cursor = ts.QueryCursor(iface_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(iface_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        iface_node = cd["interface"][0]
        iface_name = _node_text(name_nodes[0], content)
        fqn = f"{package}.{iface_name}" if package else iface_name

        modifiers_text = _extract_modifiers(iface_node, content)

        # Extract interface extends (Java: interface Foo extends Bar, Baz)
        # The extends clause appears as type_list child of interface_declaration
        iface_extends: list[str] = []
        for iface_child in iface_node.children:
            if iface_child.type == "type_list":
                iface_extends = _extract_implements(iface_child, content)
                break

        result.classes.append(ClassDef(
            name=fqn,
            file_path=file_path,
            start_line=iface_node.start_point.row + 1,
            end_line=iface_node.end_point.row + 1,
            is_public="public" in modifiers_text,
            is_abstract=False,
            is_interface=True,
            extends=iface_extends[0] if iface_extends else None,
            implements=[],
            content=_get_text(iface_node, content),
        ))

    # --- Methods ---
    method_q = _get_query("method", """
        (method_declaration
          name: (identifier) @name
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

        return_type = _extract_method_return_type(method_node, content)

        params: list[ParamDef] = []
        for child in method_node.children:
            if child.type == "formal_parameters":
                params = _extract_parameters(child, content)
                break

        modifiers_text = _extract_modifiers(method_node, content)
        owner = _find_enclosing_class_fast(method_node, content, class_byte_map)

        result.methods.append(MethodDef(
            name=method_name,
            class_name=owner,
            file_path=file_path,
            start_line=method_node.start_point.row + 1,
            end_line=method_node.end_point.row + 1,
            return_type=return_type,
            parameters=params,
            is_static="static" in modifiers_text,
            is_public="public" in modifiers_text,
            is_constructor=False,
            content=_get_text(method_node, content),
        ))

    # --- Constructors ---
    ctor_q = _get_query("ctor", """
        (constructor_declaration
          name: (identifier) @name
        ) @ctor
    """)
    cursor = ts.QueryCursor(ctor_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(ctor_q, captures)
        name_nodes = cd.get("name", [])
        if not name_nodes:
            continue
        ctor_node = cd["ctor"][0]
        ctor_name = _node_text(name_nodes[0], content)

        params = []
        for child in ctor_node.children:
            if child.type == "formal_parameters":
                params = _extract_parameters(child, content)
                break

        owner = _find_enclosing_class_fast(ctor_node, content, class_byte_map)
        ctor_modifiers = _extract_modifiers(ctor_node, content)

        result.constructors.append(ConstructorDef(
            name=ctor_name,
            class_name=owner or "",
            file_path=file_path,
            start_line=ctor_node.start_point.row + 1,
            end_line=ctor_node.end_point.row + 1,
            parameters=params,
            is_public="public" in ctor_modifiers,
            content=_get_text(ctor_node, content),
        ))

    # --- Fields ---
    field_q = _get_query("field", """
        (field_declaration
          type: (_) @field_type
          declarator: (variable_declarator
            name: (identifier) @field_name
          )
        ) @field
    """)
    cursor = ts.QueryCursor(field_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(field_q, captures)
        type_nodes = cd.get("field_type", [])
        name_nodes = cd.get("field_name", [])
        if not type_nodes or not name_nodes:
            continue
        field_node = cd["field"][0]
        field_type = _strip_generics(_node_text(type_nodes[0], content))
        field_name = _node_text(name_nodes[0], content)
        owner = _find_enclosing_class_fast(field_node, content, class_byte_map)

        field_modifiers = _extract_modifiers(field_node, content)
        is_static = "static" in field_modifiers

        result.fields.append(FieldDef(
            name=field_name,
            type_name=field_type,
            class_name=owner or "",
            file_path=file_path,
            start_line=field_node.start_point.row + 1,
            end_line=field_node.end_point.row + 1,
            is_static=is_static,
            is_public="public" in field_modifiers,
        ))

    # --- Imports ---
    import_q = _get_query("import", """
        (import_declaration
          (scoped_identifier) @import_name
        ) @import
    """)
    cursor = ts.QueryCursor(import_q)
    for _, captures in cursor.matches(tree.root_node):
        cd = _captures_to_dict(import_q, captures)
        name_nodes = cd.get("import_name", [])
        if not name_nodes:
            continue
        qualified = _node_text(name_nodes[0], content)
        result.imports.append(ImportDecl(
            qualified_name=qualified,
            is_wildcard=False,
            file_path=file_path,
        ))

    # --- Method calls ---
    # Build type map per-method for accurate receiver type resolution.
    _extract_calls_per_method(
        tree.root_node, content, lang, file_path, result,
        result.imports, class_byte_map, method_byte_map,
    )

    # --- Local variables ---
    result.variables = _extract_variables(tree.root_node, content, lang, file_path, class_byte_map, method_byte_map)

    # --- Field accesses ---
    # Build field name set for this file
    local_fields = {f.name for f in result.fields}
    result.field_accesses = _extract_field_accesses(
        tree.root_node, content, lang, file_path, local_fields, class_byte_map, method_byte_map,
    )

    # --- Annotations ---
    result.annotations = _extract_annotations(tree.root_node, content, lang, file_path)

    return result


def _extract_package(source: bytes, lang: ts.Language, root: ts.Node) -> str | None:
    query = _get_query("package", "(package_declaration (scoped_identifier) @pkg)")
    cursor = ts.QueryCursor(query)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(query, captures)
        pkg_nodes = cd.get("pkg", [])
        if pkg_nodes:
            return _node_text(pkg_nodes[0], source)
    return None


def _extract_calls_per_method(
    root: ts.Node,
    source: bytes,
    lang: ts.Language,
    file_path: str,
    result: ParsedFile,
    imports: list[ImportDecl],
    class_byte_map: list[tuple[int, int, str]],
    method_byte_map: list[tuple[int, int, str]],
) -> None:
    """Extract method calls using per-method type maps.

    Builds a type_map for each method body (fields + params + local vars)
    to avoid cross-method variable shadowing issues.
    """
    call_q = _get_query("per_method_call", """
        (method_invocation
          name: (identifier) @call_name
        ) @call
    """)
    method_q = ts.Query(lang, """
        [
            (method_declaration
              name: (identifier) @mname
              parameters: (formal_parameters) @params
              body: (block) @body)
            (constructor_declaration
              name: (identifier) @mname
              parameters: (formal_parameters) @params
              body: (constructor_body) @body)
        ]
    """)

    # Collect field names and types for this file
    field_type_map: dict[str, str] = {}
    for field in result.fields:
        field_type_map[field.name] = field.type_name

    cursor = ts.QueryCursor(method_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(method_q, captures)
        name_nodes = cd.get("mname", [])
        param_nodes_list = cd.get("params", [])
        body_nodes = cd.get("body", [])
        if not name_nodes or not body_nodes:
            continue

        method_name = _node_text(name_nodes[0], source)
        body_node = body_nodes[0]

        # Find enclosing class for this method
        enclosing_class = _find_enclosing_class_fast(name_nodes[0], source, class_byte_map)
        fq_method_name = f"{enclosing_class}.{method_name}" if enclosing_class else method_name

        # Build per-method type map: fields + params + local vars
        type_map: dict[str, str] = {}
        type_map.update(field_type_map)

        # Also add known method return types from the same file as simple name mappings.
        # This allows chained calls like getStatus().getData() where getStatus() returns
        # ApiResponse, to resolve getData() to the ApiResponse class.
        for method in result.methods:
            if method.return_type and method.return_type not in (
                "void", "int", "long", "double", "float", "boolean", "byte", "char", "short",
            ):
                # Add both simple name and any alias
                type_map[method.name] = _strip_generics(method.return_type)

        # Extract method parameters
        if param_nodes_list:
            param_node = param_nodes_list[0]
            pp_q = _get_query("per_method_pp", """
                (formal_parameter
                  type: (_) @ptype
                  name: (identifier) @pname)
            """)
            pp_cursor = ts.QueryCursor(pp_q)
            for _, pp_caps in pp_cursor.matches(param_node):
                pp_cd = _captures_to_dict(pp_q, pp_caps)
                pt_nodes = pp_cd.get("ptype", [])
                pn_nodes = pp_cd.get("pname", [])
                if pt_nodes and pn_nodes:
                    type_map[_node_text(pn_nodes[0], source)] = _strip_generics(
                        _node_text(pt_nodes[0], source)
                    )

        # Extract local variables within method body
        lv_q = _get_query("per_method_lv", """
            (local_variable_declaration
              type: (_) @vtype
              declarator: (variable_declarator
                name: (identifier) @vname))
        """)
        lv_cursor = ts.QueryCursor(lv_q)
        for _, lv_caps in lv_cursor.matches(body_node):
            lv_cd = _captures_to_dict(lv_q, lv_caps)
            vt_nodes = lv_cd.get("vtype", [])
            vn_nodes = lv_cd.get("vname", [])
            if vt_nodes and vn_nodes:
                var_name = _node_text(vn_nodes[0], source)
                var_type = _strip_generics(_node_text(vt_nodes[0], source))
                type_map[var_name] = var_type

        # Extract enhanced for-loop variables: for (Type var : collection)
        efor_q = _get_query("per_method_efor", """
            (enhanced_for_statement
              type: (_) @ftype
              name: (identifier) @fname)
        """)
        efor_cursor = ts.QueryCursor(efor_q)
        for _, efor_caps in efor_cursor.matches(body_node):
            efor_cd = _captures_to_dict(efor_q, efor_caps)
            ft_nodes = efor_cd.get("ftype", [])
            fn_nodes = efor_cd.get("fname", [])
            if ft_nodes and fn_nodes:
                type_map[_node_text(fn_nodes[0], source)] = _strip_generics(
                    _node_text(ft_nodes[0], source)
                )

        # Extract typed lambda parameters: (Type x) -> ... or (Type x, Type y) -> ...
        # These are skipped by the untyped lambda inference, so we need to add them explicitly.
        lparam_q = _get_query("typed_lambda_param", """
            (lambda_expression
              parameters: (formal_parameters
                (formal_parameter
                  type: (_) @lptype
                  name: (identifier) @lpname)))
        """)
        lparam_cursor = ts.QueryCursor(lparam_q)
        for _, lparam_caps in lparam_cursor.matches(body_node):
            lparam_cd = _captures_to_dict(lparam_q, lparam_caps)
            lt_nodes = lparam_cd.get("lptype", [])
            ln_nodes = lparam_cd.get("lpname", [])
            if lt_nodes and ln_nodes:
                lp_name = _node_text(ln_nodes[0], source)
                lp_type = _strip_generics(_node_text(lt_nodes[0], source))
                type_map[lp_name] = lp_type

        # Infer lambda parameter types (must be after type_map build, before call extraction)
        _infer_lambda_param_types(body_node, source, type_map, result.methods, lang)

        # Extract calls within method body
        call_cursor = ts.QueryCursor(call_q)
        seen: set[int] = set()
        for _, caps in call_cursor.matches(body_node):
            call_cd = _captures_to_dict(call_q, caps)
            call_nodes = call_cd.get("call", [])
            if not call_nodes:
                continue
            call_node = call_nodes[0]

            call_name_nodes = call_cd.get("call_name", [])
            if not call_name_nodes:
                continue
            call_name = _node_text(call_name_nodes[0], source)

            # Dedup by (start_byte, call_name) to handle chained calls
            # that share the same start byte but call different methods
            # (e.g., foo().bar().baz() — bar and baz share start_byte with foo).
            dedup_key = (call_node.start_byte, call_name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            receiver = None
            for child in call_node.children:
                if child.type in ("field_access", "identifier"):
                    candidate = _node_text(child, source)
                    if candidate != call_name:
                        receiver = candidate
                    break
                elif child.type == "method_invocation":
                    # Chained call: foo().bar() — receiver is the inner method name
                    for inner_child in child.children:
                        if inner_child.type == "identifier":
                            receiver = _node_text(inner_child, source)
                            break
                    break

            # Resolve receiver type using per-method type map
            receiver_type = None
            if receiver:
                receiver_type = type_map.get(receiver)

            # For Type.method() patterns where Type is not in type_map (class name),
            # use the receiver directly as the type if it looks like a class name.
            if receiver_type is None and receiver:
                if receiver[0:1].isupper() and receiver not in type_map:
                    receiver_type = receiver

            # For chained calls like X.builder()...build(), trace down through
            # the chain to find the original builder type.
            if receiver_type is None and call_node:
                chain_type = _trace_builder_chain(call_node, source, type_map)
                if chain_type:
                    receiver_type = chain_type

            result.calls.append(CallSite(
                caller_method=fq_method_name,
                caller_class=enclosing_class or "",
                target_name=call_name,
                line=call_node.start_point.row + 1,
                receiver=receiver,
                receiver_type=receiver_type,
            ))

        # Also extract method references (OSProcess::getUser)
        _extract_method_references(
            body_node, source, type_map,
            fq_method_name, enclosing_class or "", result, imports,
        )

        # Also extract constructor calls: new SomeClass()
        _extract_constructor_calls(
            body_node, source, type_map,
            fq_method_name, enclosing_class or "", result, imports,
        )

        # Also extract explicit constructor invocations: this() / super()
        _extract_explicit_ctor_invocations(
            body_node, source, type_map,
            fq_method_name, enclosing_class or "", result, imports,
        )


# --- Lambda parameter type inference ---


def _extract_lambda_param_names(lambda_node: ts.Node, source: bytes) -> list[str]:
    """Extract lambda parameter names.

    Handles three tree-sitter patterns:
    1. Single parameter without parens: x -> ...  (direct identifier child)
    2. Multiple parameters without types: (x, y) -> ...  (inferred_parameters)
    3. Parameters with types: (Type x, Type y) -> ...  (formal_parameters)
    """
    names = []
    for child in lambda_node.children:
        if child.type == "identifier":
            names.append(_node_text(child, source))
        elif child.type == "inferred_parameters":
            for p in child.children:
                if p.type == "identifier":
                    names.append(_node_text(p, source))
        elif child.type == "formal_parameters":
            for p in child.children:
                if p.type == "formal_parameter":
                    for name_child in p.children:
                        if name_child.type == "identifier":
                            names.append(_node_text(name_child, source))
                            break
    return names


def _extract_first_generic_arg_from_node(generic_node: ts.Node, source: bytes) -> str:
    """Extract first generic type argument from a generic_type AST node.

    e.g., List<AlertHistory.Alert> -> "AlertHistory.Alert"
    """
    for child in generic_node.children:
        if child.type == "type_arguments":
            for arg in child.children:
                if arg.type in ("type_argument", "type_identifier", "scoped_type_identifier", "generic_type"):
                    return _node_text(arg, source)
    return ""


def _find_base_variable_with_type(
    call_node: ts.Node, type_map: dict[str, str], source: bytes, body_node: ts.Node = None
) -> str | None:
    """In a chain like obj.method1().method2(), find the first variable that has a known type."""
    for child in call_node.children:
        if child.type == "identifier":
            var_name = _node_text(child, source)
            if var_name in type_map:
                # Try full generic type from AST first
                if body_node:
                    full = _lookup_full_type_in_body(var_name, body_node, source)
                    if full:
                        return full
                return type_map[var_name]
    return None


def _strategy1_receiver_chain(
    lambda_node: ts.Node,
    source: bytes,
    type_map: dict[str, str],
    method_name: str,
    param_index: int,
) -> list[tuple[str, str]]:
    """Strategy 1: Walk up AST from lambda to enclosing method_invocation.

    Returns list of (param_name, inferred_type) pairs.
    """
    if method_name not in _FUNCTIONAL_METHOD_TYPE_PARAM:
        return []

    node = lambda_node
    while node:
        if node.type == "method_invocation":
            receiver_node = None
            call_name = None
            for child in node.children:
                if child.type == "field_access":
                    receiver_node = child
                elif child.type == "identifier":
                    call_name = _node_text(child, source)

            if call_name != method_name:
                node = node.parent
                continue

            inferred_type: str | None = None

            if receiver_node and receiver_node.type == "field_access":
                object_node = receiver_node.child_by_field_name("object")
                if object_node:
                    if object_node.type == "identifier":
                        obj_name = _node_text(object_node, source)
                        obj_type = type_map.get(obj_name)
                        if obj_type:
                            inferred_type = obj_type
                    elif object_node.type == "method_invocation":
                        inferred_type = _find_base_variable_with_type(
                            object_node, type_map, source
                        )
                    elif object_node.type == "generic_type":
                        inferred_type = _extract_first_generic_arg_from_node(
                            object_node, source
                        )

            if inferred_type:
                param_names = _extract_lambda_param_names(lambda_node, source)
                if param_index < len(param_names):
                    return [(param_names[param_index], inferred_type)]

            break
        node = node.parent
    return []


def _disambiguate_by_name_similarity(
    param_name: str, candidates: list[str], method_name: str
) -> str | None:
    """Disambiguate among multiple candidate classes."""
    param_lower = param_name.lower()

    for c in candidates:
        class_lower = c.lower()
        if param_lower in class_lower or class_lower in param_lower:
            return c
        short_name = c.rsplit(".", 1)[-1] if "." in c else c
        if param_lower == short_name[0].lower() and len(param_lower) == 1:
            return c

    return None


def _strategy2_body_lookup(
    lambda_node: ts.Node,
    source: bytes,
    type_map: dict[str, str],
    method_to_classes: dict[str, list[str]],
    body_node: ts.Node,
) -> list[tuple[str, str]]:
    """Strategy 2: Find method calls on lambda params in the body.

    Returns list of (param_name, inferred_type) pairs.
    """
    param_names = _extract_lambda_param_names(lambda_node, source)
    if not param_names:
        return []

    param_name_set = set(param_names)
    results: list[tuple[str, str]] = []

    call_q = _get_query("s2_call", """
        (method_invocation
          name: (identifier) @s2call_name
        ) @s2call
    """)

    # Query only within the method body, not the entire file AST
    receiver_calls: dict[str, set[str]] = {}

    cursor = ts.QueryCursor(call_q)
    for _, caps in cursor.matches(body_node):
        cd = _captures_to_dict(call_q, caps)
        call_nodes = cd.get("s2call", [])
        name_nodes = cd.get("s2call_name", [])
        if not call_nodes or not name_nodes:
            continue
        call_node = call_nodes[0]

        if not (_is_descendant_of(call_node, lambda_node) and call_node.id != lambda_node.id):
            continue

        call_name = _node_text(name_nodes[0], source)
        receiver = None
        for child in call_node.children:
            if child.type in ("field_access", "identifier"):
                candidate = _node_text(child, source)
                if candidate != call_name:
                    receiver = candidate
                break

        if receiver and receiver in param_name_set:
            receiver_calls.setdefault(receiver, set()).add(call_name)

    for pname, called_methods in receiver_calls.items():
        if pname in type_map:
            continue

        candidate_classes: set[str] = set()
        all_have_candidate = True

        for mname in called_methods:
            classes = method_to_classes.get(mname)
            if not classes:
                all_have_candidate = False
                break
            if not candidate_classes:
                candidate_classes = set(classes)
            else:
                candidate_classes &= set(classes)

        if not all_have_candidate or not candidate_classes:
            continue

        if len(candidate_classes) == 1:
            inferred_type = list(candidate_classes)[0]
            results.append((pname, inferred_type))
        else:
            candidate_list = list(candidate_classes)
            primary_method = max(called_methods, key=lambda m: len(method_to_classes.get(m, [])))
            resolved = _disambiguate_by_name_similarity(
                pname, candidate_list, primary_method
            )
            if resolved:
                results.append((pname, resolved))

    return results


def _is_descendant_of(node: ts.Node, ancestor: ts.Node) -> bool:
    """Check if node is a descendant (child, grandchild, etc.) of ancestor."""
    parent = node.parent
    while parent:
        if parent.id == ancestor.id:
            return True
        parent = parent.parent
    return False


def _build_method_to_classes(methods: list) -> dict[str, list[str]]:
    """Build a reverse index: method_name -> list of class names that define it."""
    index: dict[str, list[str]] = {}
    for method in methods:
        if method.class_name:
            index.setdefault(method.name, []).append(method.class_name)
    return index


def _find_strategy1_results(
    lambda_node: ts.Node,
    source: bytes,
    type_map: dict[str, str],
    body_node: ts.Node,
) -> list[tuple[str, str]]:
    """Strategy 1: Look at the enclosing method_invocation for the lambda.

    Walk the parent chain to find the nearest method_invocation that uses
    this lambda as an argument, then extract receiver type.
    """
    parent_chain: list[ts.Node] = []
    node = lambda_node.parent
    while node:
        parent_chain.append(node)
        node = node.parent

    target_invocation = None
    for p in parent_chain:
        if p.type == "method_invocation":
            if _lambda_is_argument_of(lambda_node, p):
                target_invocation = p
                break

    if target_invocation is None:
        return []

    call_name = None
    # Find the method name: it's the identifier immediately before the argument_list
    children_list = list(target_invocation.children)
    for i, child in enumerate(children_list):
        if child.type == "argument_list" and i > 0:
            # The identifier before argument_list is the method name
            prev = children_list[i - 1]
            # Skip '.' separator
            if prev.type == "." and i > 1:
                prev = children_list[i - 2]
            if prev.type == "identifier":
                call_name = _node_text(prev, source)
                break
    # Fallback: last identifier child
    if call_name is None:
        for child in reversed(target_invocation.children):
            if child.type == "identifier":
                call_name = _node_text(child, source)
                break

    if call_name is None:
        return []

    if call_name not in _FUNCTIONAL_METHOD_TYPE_PARAM:
        return []

    param_index = _FUNCTIONAL_METHOD_TYPE_PARAM[call_name]

    inferred_type: str | None = None
    for child in target_invocation.children:
        if child.type == "field_access":
            object_node = child.child_by_field_name("object")
            if object_node:
                inferred_type = _resolve_node_type(object_node, source, type_map, body_node)
                break
        elif child.type == "method_invocation":
            # Chained call: alerts.stream().filter(...) — receiver is another method_invocation
            inferred_type = _resolve_node_type(child, source, type_map, body_node)
            if inferred_type:
                break
        elif child.type == "identifier":
            # Direct identifier receiver: sessions.sort(...)
            # Make sure it's not the method name (which is the identifier before argument_list)
            children_list = list(target_invocation.children)
            method_name_idx = None
            for i, c in enumerate(children_list):
                if c.type == "argument_list" and i > 0:
                    prev = children_list[i - 1]
                    if prev.type == "." and i > 1:
                        method_name_idx = i - 2
                    else:
                        method_name_idx = i - 1
                    break
            if method_name_idx is not None and children_list.index(child) != method_name_idx:
                var_name = _node_text(child, source)
                # Try full generic type from AST first
                full = _lookup_full_type_in_body(var_name, body_node, source)
                if full:
                    inferred_type = full
                else:
                    inferred_type = type_map.get(var_name)
                if inferred_type:
                    break

    if inferred_type:
        param_names = _extract_lambda_param_names(lambda_node, source)
        if param_index < len(param_names):
            pname = param_names[param_index]
            if pname not in type_map:
                return [(pname, inferred_type)]

    # Strategy 1b: If invocation is RHS of an assignment, use the variable's declared type
    if inferred_type is None:
        assigned_type = _find_assignment_target_type(target_invocation, body_node, source)
        if assigned_type:
            # Extract first generic parameter from the assigned variable's type
            # e.g., Comparator<ProcessInfo> -> ProcessInfo
            param_names = _extract_lambda_param_names(lambda_node, source)
            if param_index < len(param_names):
                pname = param_names[param_index]
                if pname not in type_map:
                    generic_arg = _extract_first_generic_arg_from_type(assigned_type)
                    if generic_arg:
                        return [(pname, generic_arg)]
                    # Fall back to the base type
                    return [(pname, _strip_generics(assigned_type))]

    return []


def _find_assignment_target_type(invocation: ts.Node, body_node: ts.Node, source: bytes) -> str | None:
    """If invocation is the RHS of an assignment, return the declared type of the LHS variable."""
    parent = invocation.parent
    while parent and parent.id != body_node.id:
        if parent.type == "assignment_expression":
            # Find the left-hand side
            lhs = parent.child_by_field_name("left")
            if lhs and lhs.type == "identifier":
                var_name = _node_text(lhs, source)
                # Look up the full type from the body's AST
                full = _lookup_full_type_in_body(var_name, body_node, source)
                if full:
                    return full
        parent = parent.parent
    return None


def _extract_first_generic_arg_from_type(type_str: str) -> str | None:
    """Extract first generic type argument from a type string.

    e.g., 'Comparator<ProcessInfo>' -> 'ProcessInfo'
    """
    if "<" not in type_str:
        return None
    inner = type_str.split("<", 1)[1].rstrip(">")
    # Handle nested generics by finding the first top-level comma or closing >
    depth = 0
    result = []
    for ch in inner:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "," and depth == 0:
            break
        result.append(ch)
    return "".join(result).strip() or None


def _lambda_is_argument_of(lambda_node: ts.Node, invocation: ts.Node) -> bool:
    """Check if lambda_node is a direct argument of the method invocation."""
    for child in invocation.children:
        if child.type == "argument_list":
            return _is_descendant_of(lambda_node, child)
    return False


def _resolve_node_type(node: ts.Node, source: bytes, type_map: dict[str, str], body_node: ts.Node) -> str | None:
    """Resolve the type of a node: identifier -> type_map, method_invocation -> return type."""
    if node.type == "identifier":
        name = _node_text(node, source)
        # First try stripped type_map
        stripped = type_map.get(name)
        if stripped:
            # Try to find full generic type from AST
            full = _lookup_full_type_in_body(name, body_node, source)
            if full:
                return full
            return stripped
    elif node.type == "field_access":
        field_node = node.child_by_field_name("field")
        if field_node:
            field_name = _node_text(field_node, source)
            stripped = type_map.get(field_name)
            if stripped:
                full = _lookup_full_type_in_body(field_name, body_node, source)
                if full:
                    return full
                return stripped
        object_node = node.child_by_field_name("object")
        if object_node and object_node.type == "identifier":
            obj_name = _node_text(object_node, source)
            obj_type = type_map.get(obj_name)
            if obj_type:
                return obj_type
    elif node.type == "method_invocation":
        return _find_base_variable_with_type(node, type_map, source, body_node)
    elif node.type == "generic_type":
        return _extract_first_generic_arg_from_node(node, source)
    return None


def _lookup_full_type_in_body(var_name: str, body_node: ts.Node, source: bytes) -> str | None:
    """Look up the full (unstripped) type of a variable from the method body's AST."""
    lv_q = _get_query("full_type_lv", """
        (local_variable_declaration
          type: (_) @vtype
          declarator: (variable_declarator
            name: (identifier) @vname))
    """)
    cursor = ts.QueryCursor(lv_q)
    for _, lv_caps in cursor.matches(body_node):
        lv_cd = _captures_to_dict(lv_q, lv_caps)
        vt_nodes = lv_cd.get("vtype", [])
        vn_nodes = lv_cd.get("vname", [])
        if vt_nodes and vn_nodes and _node_text(vn_nodes[0], source) == var_name:
            return _node_text(vt_nodes[0], source)
    return None


def _infer_lambda_param_types(
    body_node: ts.Node,
    source: bytes,
    type_map: dict[str, str],
    methods: list,
    lang: ts.Language,
) -> None:
    """Main entry point: infer lambda parameter types and add to type_map.

    Must be called after type_map is built from fields, params, locals, and
    enhanced for-loop variables, but before call extraction.

    Uses two strategies:
    1. Receiver chain + generic propagation (primary)
    2. Lambda body method lookup + reverse index (fallback)
    """
    method_to_classes = _build_method_to_classes(methods)

    lambda_q = _get_query("lambda_expr", "(lambda_expression) @lambda")
    cursor = ts.QueryCursor(lambda_q)

    for _, caps in cursor.matches(body_node):
        cd = _captures_to_dict(lambda_q, caps)
        lambda_nodes = cd.get("lambda", [])
        for lambda_node in lambda_nodes:
            has_typed_params = False
            for child in lambda_node.children:
                if child.type == "formal_parameters":
                    has_typed_params = True
                    break
            if has_typed_params:
                continue

            param_names = _extract_lambda_param_names(lambda_node, source)
            if all(n in type_map for n in param_names):
                continue

            s1_results = _find_strategy1_results(lambda_node, source, type_map, body_node)
            for pname, ptype in s1_results:
                if pname not in type_map:
                    type_map[pname] = ptype

            s2_results = _strategy2_body_lookup(
                lambda_node, source, type_map, method_to_classes, body_node
            )
            for pname, ptype in s2_results:
                if pname not in type_map:
                    type_map[pname] = ptype


def _extract_method_references(
    body_node: ts.Node,
    source: bytes,
    type_map: dict[str, str],
    caller_method: str,
    caller_class: str,
    result: ParsedFile,
    imports: list,
) -> None:
    """Extract method references (e.g., OSProcess::getUser) as call sites.

    Handles patterns like:
    - Type::method  →  call on instance of Type (e.g., OSProcess::getUser)
    - Class::staticMethod  →  static method call
    """
    ref_q = _get_query("method_ref", "(method_reference) @ref")
    root = body_node
    while root.parent:
        root = root.parent

    cursor = ts.QueryCursor(ref_q)
    seen: set[tuple[int, str]] = set()

    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(ref_q, caps)
        ref_nodes = cd.get("ref", [])
        if not ref_nodes:
            continue
        ref_node = ref_nodes[0]

        # Only process references within this method body
        if ref_node.start_byte < body_node.start_byte or ref_node.end_byte > body_node.end_byte:
            continue

        # method_reference children: identifier, '::', identifier
        # e.g., OSProcess::getUser
        children = [c for c in ref_node.children if c.type == "identifier"]
        if len(children) < 2:
            continue

        receiver_name = _node_text(children[0], source)
        method_name = _node_text(children[1], source)

        dedup_key = (ref_node.start_byte, method_name)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Resolve receiver type: check type_map first, then imports
        receiver_type = type_map.get(receiver_name)
        if receiver_type is None:
            # Try to find from imports - match the last segment
            for imp in imports:
                imp_name = imp.qualified_name
                if imp_name.split(".")[-1] == receiver_name:
                    receiver_type = receiver_name
                    break

        if receiver_type is None:
            # Still record the call even without type resolution
            receiver_type = receiver_name

        result.calls.append(CallSite(
            caller_method=caller_method,
            caller_class=caller_class,
            target_name=method_name,
            line=ref_node.start_point.row + 1,
            receiver=receiver_name,
            receiver_type=receiver_type,
        ))


def _extract_constructor_calls(
    body_node: ts.Node,
    source: bytes,
    type_map: dict[str, str],
    caller_method: str,
    caller_class: str,
    result: ParsedFile,
    imports: list,
) -> None:
    """Extract constructor calls: new SomeClass() patterns.

    Handles:
    - Simple: new ProcessInfo()
    - Qualified: new com.example.Foo()
    """
    obj_q = _get_query("object_creation", """
        (object_creation_expression
          type: (type_identifier) @obj_type) @obj_call
    """)
    root = body_node
    while root.parent:
        root = root.parent

    cursor = ts.QueryCursor(obj_q)
    seen: set[int] = set()

    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(obj_q, caps)
        call_nodes = cd.get("obj_call", [])
        type_nodes = cd.get("obj_type", [])
        if not call_nodes or not type_nodes:
            continue
        call_node = call_nodes[0]

        # Only process within this method body
        if call_node.start_byte < body_node.start_byte or call_node.end_byte > body_node.end_byte:
            continue

        if call_node.start_byte in seen:
            continue
        seen.add(call_node.start_byte)

        type_name = _node_text(type_nodes[0], source)

        # Try to resolve the type: check imports for FQN
        resolved_type = None
        for imp in imports:
            imp_name = imp.qualified_name
            if imp_name.split(".")[-1] == type_name:
                resolved_type = imp_name
                break

        result.calls.append(CallSite(
            caller_method=caller_method,
            caller_class=caller_class,
            target_name=type_name,
            line=call_node.start_point.row + 1,
            receiver=type_name,
            receiver_type=resolved_type or type_name,
        ))


def _extract_explicit_ctor_invocations(
    body_node: ts.Node,
    source: bytes,
    type_map: dict[str, str],
    caller_method: str,
    caller_class: str,
    result: ParsedFile,
    imports: list,
) -> None:
    """Extract explicit constructor invocations: this() and super().

    Handles:
    - this(args) — delegates to another constructor in the same class
    - super(args) — calls parent class constructor
    """
    ctor_q = _get_query("explicit_ctor", """
        (explicit_constructor_invocation
          (super) @ctor_kind) @ctor_call
        (explicit_constructor_invocation
          (this) @ctor_kind) @ctor_call
    """)
    root = body_node
    while root.parent:
        root = root.parent

    cursor = ts.QueryCursor(ctor_q)
    seen: set[int] = set()

    for _, caps in cursor.matches(root):
        cd = _captures_to_dict(ctor_q, caps)
        call_nodes = cd.get("ctor_call", [])
        if not call_nodes:
            continue
        call_node = call_nodes[0]

        # Only process within this method body
        if call_node.start_byte < body_node.start_byte or call_node.end_byte > body_node.end_byte:
            continue

        if call_node.start_byte in seen:
            continue
        seen.add(call_node.start_byte)

        # Determine if this() or super()
        kind_nodes = cd.get("ctor_kind", [])
        if not kind_nodes:
            continue
        kind_text = _node_text(kind_nodes[0], source)

        if kind_text == "this":
            # this() — target is the same class
            target_name = caller_class
            receiver_type = caller_class
        else:
            # super() — target is the parent class
            # Try to find the parent class from the current class's extends
            parent_class = _find_parent_class_for(caller_class, result)
            target_name = parent_class or "Object"
            receiver_type = parent_class or "Object"

        result.calls.append(CallSite(
            caller_method=caller_method,
            caller_class=caller_class,
            target_name=target_name,
            line=call_node.start_point.row + 1,
            receiver=target_name,
            receiver_type=receiver_type,
        ))


def _find_parent_class_for(class_name: str, result: ParsedFile) -> str | None:
    """Find the parent class name for a given class by scanning parsed files."""
    for pf_cls in result.classes:
        # Match by FQN or simple name
        simple_name = pf_cls.name.split(".")[-1]
        if (pf_cls.name == class_name or simple_name == class_name) and pf_cls.extends:
            return pf_cls.extends
    return None


# Pre-compiled queries for _find_enclosing_class/method (used per-node)
_enclosing_class_q = _get_query("enclosing_class", """
    [
        (class_declaration name: (identifier) @name)
        (interface_declaration name: (identifier) @name)
    ] @cls
""")

_enclosing_method_q = _get_query("enclosing_method", "(method_declaration name: (identifier) @name) @method")


def _build_class_byte_map(root: ts.Node, source: bytes) -> list[tuple[int, int, str]]:
    """Pre-compute all class/interface byte ranges for a file.

    Returns sorted list of (start_byte, end_byte, qualified_name).
    """
    cursor = ts.QueryCursor(_enclosing_class_q)
    classes: list[tuple[int, int, str]] = []
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(_enclosing_class_q, captures)
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
    """Find enclosing class using pre-computed byte ranges. O(n) where n = #classes."""
    enclosers: list[tuple[int, int, str]] = []
    for start, end, name in class_byte_map:
        if node.start_byte >= start and node.end_byte <= end:
            enclosers.append((start, end - start, name))
    if not enclosers:
        return None
    enclosers.sort(key=lambda x: x[0])
    return ".".join(name for _, _, name in enclosers)


def _build_method_byte_map(root: ts.Node, source: bytes) -> list[tuple[int, int, str]]:
    """Pre-compute all method byte ranges for a file."""
    cursor = ts.QueryCursor(_enclosing_method_q)
    methods: list[tuple[int, int, str]] = []
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(_enclosing_method_q, captures)
        method_nodes = cd.get("method", [])
        name_nodes = cd.get("name", [])
        if method_nodes and name_nodes:
            name = _node_text(name_nodes[0], source)
            methods.append((method_nodes[0].start_byte, method_nodes[0].end_byte, name))
    return methods


def _find_enclosing_method_fast(
    node: ts.Node,
    source: bytes,
    method_byte_map: list[tuple[int, int, str]],
    class_byte_map: list[tuple[int, int, str]],
) -> str | None:
    """Find enclosing method using pre-computed byte ranges."""
    best: tuple[int, str] | None = None
    for start, end, name in method_byte_map:
        if node.start_byte >= start and node.end_byte <= end:
            span = end - start
            if best is None or span < best[0]:
                owner = _find_enclosing_class_fast(node, source, class_byte_map)
                best = (span, f"{owner}.{name}" if owner else name)
    return best[1] if best else None


def _find_enclosing_class(node: ts.Node, source: bytes, lang: ts.Language) -> str | None:
    """Find the class or interface that contains the given node by byte range comparison.

    For nested classes, returns the qualified name like "Outer.Inner".

    Deprecated: use _find_enclosing_class_fast with a pre-built class_byte_map.
    """
    cursor = ts.QueryCursor(_enclosing_class_q)
    root = node
    while root.parent:
        root = root.parent

    # Collect all enclosing class names by byte range
    enclosers: list[tuple[int, int, str]] = []
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(_enclosing_class_q, captures)
        cls_nodes = cd.get("cls", [])
        name_nodes = cd.get("name", [])
        if cls_nodes and name_nodes:
            cls_node = cls_nodes[0]
            if node.start_byte >= cls_node.start_byte and node.end_byte <= cls_node.end_byte:
                name = _node_text(name_nodes[0], source)
                span = cls_node.end_byte - cls_node.start_byte
                enclosers.append((cls_node.start_byte, span, name))

    if not enclosers:
        return None

    # Sort by start_byte (outermost first) to get proper nesting order
    enclosers.sort(key=lambda x: x[0])
    # Join names with '.' to form qualified name
    return ".".join(name for _, _, name in enclosers)


def _find_enclosing_method(node: ts.Node, source: bytes, lang: ts.Language) -> str | None:
    """Find the method that contains the given node by byte range comparison."""
    cursor = ts.QueryCursor(_enclosing_method_q)
    best: tuple[int, int, str] | None = None
    root = node
    while root.parent:
        root = root.parent
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(_enclosing_method_q, captures)
        method_nodes = cd.get("method", [])
        name_nodes = cd.get("name", [])
        if method_nodes and name_nodes:
            method_node = method_nodes[0]
            if node.start_byte >= method_node.start_byte and node.end_byte <= method_node.end_byte:
                name = _node_text(name_nodes[0], source)
                span = method_node.end_byte - method_node.start_byte
                if best is None or span < best[1]:
                    owner = _find_enclosing_class(method_node, source, lang)
                    best = (method_node.start_byte, span, f"{owner}.{name}" if owner else name)
    return best[2] if best else None


# Pre-compiled queries for type map building
_local_var_q: ts.Query | None = None
_annotation_q: ts.Query | None = None


def _build_type_map(root: ts.Node, source: bytes, lang: ts.Language) -> dict[str, str]:
    """Build a mapping of local variable/field/param names to their types.

    This is used for receiver type resolution in method calls.
    Collects: local variable declarations, method parameters, constructor parameters, and fields.
    """
    type_map: dict[str, str] = {}

    # Local variables
    local_q = _get_query("local_var", """
        (local_variable_declaration
          type: (_) @vtype
          declarator: (variable_declarator
            name: (identifier) @vname))
    """)
    cursor = ts.QueryCursor(local_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(local_q, captures)
        type_nodes = cd.get("vtype", [])
        name_nodes = cd.get("vname", [])
        if type_nodes and name_nodes:
            var_name = _node_text(name_nodes[0], source)
            var_type = _strip_generics(_node_text(type_nodes[0], source))
            type_map[var_name] = var_type

    # Method parameters
    method_param_q = _get_query("method_param", """
        (method_declaration
          parameters: (formal_parameters
            (formal_parameter
              type: (_) @mtype
              name: (identifier) @mname)))
    """)
    cursor = ts.QueryCursor(method_param_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(method_param_q, captures)
        type_nodes = cd.get("mtype", [])
        name_nodes = cd.get("mname", [])
        if type_nodes and name_nodes:
            param_name = _node_text(name_nodes[0], source)
            param_type = _strip_generics(_node_text(type_nodes[0], source))
            type_map[param_name] = param_type

    # Constructor parameters
    ctor_param_q = _get_query("ctor_param", """
        (constructor_declaration
          parameters: (formal_parameters
            (formal_parameter
              type: (_) @ctype
              name: (identifier) @cname)))
    """)
    cursor = ts.QueryCursor(ctor_param_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(ctor_param_q, captures)
        type_nodes = cd.get("ctype", [])
        name_nodes = cd.get("cname", [])
        if type_nodes and name_nodes:
            param_name = _node_text(name_nodes[0], source)
            param_type = _strip_generics(_node_text(type_nodes[0], source))
            type_map[param_name] = param_type

    # Fields (for 'this.field' or direct field access)
    field_q = _get_query("field_in_type_map", """
        (field_declaration
          type: (_) @ftype
          declarator: (variable_declarator
            name: (identifier) @fname))
    """)
    cursor = ts.QueryCursor(field_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(field_q, captures)
        type_nodes = cd.get("ftype", [])
        name_nodes = cd.get("fname", [])
        if type_nodes and name_nodes:
            field_name = _node_text(name_nodes[0], source)
            field_type = _strip_generics(_node_text(type_nodes[0], source))
            type_map[field_name] = field_type

    return type_map


def _extract_variables(
    root: ts.Node,
    source: bytes,
    lang: ts.Language,
    file_path: str,
    class_byte_map: list[tuple[int, int, str]],
    method_byte_map: list[tuple[int, int, str]],
) -> list[VariableDef]:
    """Extract local variable declarations from method bodies."""
    results: list[VariableDef] = []

    local_q = _get_query("local_var_extract", """
        (local_variable_declaration
          type: (_) @vtype
          declarator: (variable_declarator
            name: (identifier) @vname))
    """)
    cursor = ts.QueryCursor(local_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(local_q, captures)
        type_nodes = cd.get("vtype", [])
        name_nodes = cd.get("vname", [])
        decl_nodes = cd.get("local_var_extract", []) or []

        if not type_nodes or not name_nodes:
            continue

        var_type = _strip_generics(_node_text(type_nodes[0], source))
        var_name = _node_text(name_nodes[0], source)
        line = name_nodes[0].start_point.row + 1

        enclosing_method = _find_enclosing_method_fast(name_nodes[0], source, method_byte_map, class_byte_map)
        enclosing_class = _find_enclosing_class_fast(name_nodes[0], source, class_byte_map)

        is_final = False
        if decl_nodes:
            mod_text = _extract_modifiers(decl_nodes[0], source)
            is_final = "final" in mod_text

        results.append(VariableDef(
            name=var_name,
            type_name=var_type,
            method_name=enclosing_method or "unknown",
            class_name=enclosing_class or "unknown",
            file_path=file_path,
            line=line,
            is_final=is_final,
        ))

    return results


def _extract_annotations(
    root: ts.Node, source: bytes, lang: ts.Language, file_path: str
) -> list[AnnotationDef]:
    """Extract annotations from classes, methods, fields, and constructors."""
    results: list[AnnotationDef] = []

    # First pass: marker annotations (no arguments)
    marker_q = _get_query("marker_ann", """
        (marker_annotation name: (_) @aname) @marker
    """)
    cursor = ts.QueryCursor(marker_q)
    seen_marker_starts: set[int] = set()
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(marker_q, captures)
        name_nodes = cd.get("aname", [])
        ann_nodes = cd.get("marker", [])
        if not name_nodes or not ann_nodes:
            continue
        ann_node = ann_nodes[0]
        if ann_node.start_byte in seen_marker_starts:
            continue
        seen_marker_starts.add(ann_node.start_byte)

        ann_name = _node_text(name_nodes[0], source)
        if ann_name.startswith("@"):
            ann_name = ann_name[1:]
        line = name_nodes[0].start_point.row + 1
        target_type, target_name = _find_annotation_target(name_nodes[0], source)

        results.append(AnnotationDef(
            name=ann_name,
            target_type=target_type,
            target_name=target_name,
            file_path=file_path,
            line=line,
        ))

    # Second pass: annotations with arguments
    ann_q = _get_query("full_ann", """
        (annotation name: (_) @aname) @full
    """)
    cursor = ts.QueryCursor(ann_q)
    seen_full_starts: set[int] = set()
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(ann_q, captures)
        name_nodes = cd.get("aname", [])
        ann_nodes = cd.get("full", [])
        if not name_nodes or not ann_nodes:
            continue
        ann_node = ann_nodes[0]
        if ann_node.start_byte in seen_full_starts:
            continue
        seen_full_starts.add(ann_node.start_byte)

        ann_name = _node_text(name_nodes[0], source)
        if ann_name.startswith("@"):
            ann_name = ann_name[1:]
        line = name_nodes[0].start_point.row + 1
        target_type, target_name = _find_annotation_target(name_nodes[0], source)
        attrs = _extract_annotation_attributes(ann_node, source)

        results.append(AnnotationDef(
            name=ann_name,
            target_type=target_type,
            target_name=target_name,
            file_path=file_path,
            line=line,
            attributes=attrs,
        ))

    # Deduplicate by (name, target_type, target_name, line) to avoid
    # same annotation appearing multiple times (e.g., same ann on different params at same line)
    seen_keys: set[tuple] = set()
    unique_results: list[AnnotationDef] = []
    for ann in results:
        key = (ann.name, ann.target_type, ann.target_name, ann.line)
        if key not in seen_keys:
            seen_keys.add(key)
            unique_results.append(ann)

    return unique_results


def _find_annotation_target(annotation_node: ts.Node, source: bytes) -> tuple[str, str]:
    """Determine what element an annotation is attached to."""
    parent = annotation_node.parent
    while parent:
        if parent.type in ("class_declaration", "interface_declaration"):
            name_node = _find_child_by_type(parent, "identifier")
            return ("class", _node_text(name_node, source) if name_node else "unknown")
        elif parent.type == "method_declaration":
            name_node = _find_child_by_type(parent, "identifier")
            return ("method", _node_text(name_node, source) if name_node else "unknown")
        elif parent.type == "constructor_declaration":
            name_node = _find_child_by_type(parent, "identifier")
            return ("constructor", _node_text(name_node, source) if name_node else "unknown")
        elif parent.type == "field_declaration":
            decl_q = _get_query("field_in_ann", "(variable_declarator name: (identifier) @fname)")
            cursor = ts.QueryCursor(decl_q)
            for _, caps in cursor.matches(parent):
                fcd = _captures_to_dict(decl_q, caps)
                fn = fcd.get("fname", [])
                if fn:
                    return ("field", _node_text(fn[0], source))
            return ("field", "unknown")
        parent = parent.parent
    return ("unknown", "unknown")


def _find_child_by_type(node: ts.Node, type_name: str) -> ts.Node | None:
    """Find the first direct child of a given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _extract_annotation_attributes(annotation_node: ts.Node, source: bytes) -> dict[str, str]:
    """Extract attributes from an annotation node.

    Handles both styles:
    1. element_value_pair: @PostMapping(value = "/tasks") → {"value": "/tasks"}
    2. Direct string argument: @GetMapping("/cron") → {"path": "/cron"}
    """
    attrs: dict[str, str] = {}
    for child in annotation_node.children:
        if child.type == "annotation_argument_list":
            for arg in child.children:
                if arg.type == "element_value_pair":
                    key_node = _find_child_by_type(arg, "identifier")
                    if key_node:
                        key = _node_text(key_node, source)
                        idx = arg.children.index(key_node)
                        # element_value_pair structure: identifier = value
                        # Skip the '=' (idx+1) and take the actual value (idx+2)
                        if idx + 2 < len(arg.children):
                            value_node = arg.children[idx + 2]
                            value = _node_text(value_node, source)
                            if value_node.type == "string_literal":
                                value = _strip_string_quotes(value)
                            attrs[key] = value
                elif arg.type == "string_literal":
                    # Direct string argument (e.g., @GetMapping("/path"))
                    raw = _node_text(arg, source)
                    attrs["path"] = _strip_string_quotes(raw)
    return attrs


def _strip_string_quotes(s: str) -> str:
    """Strip surrounding quotes from a Java string literal."""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _extract_field_accesses(
    root: ts.Node,
    source: bytes,
    lang: ts.Language,
    file_path: str,
    local_fields: set[str],
    class_byte_map: list[tuple[int, int, str]],
    method_byte_map: list[tuple[int, int, str]],
) -> list[FieldAccess]:
    """Extract field accesses (read/write) from method bodies.

    Detects:
    1. `this.field` field_access patterns
    2. Bare identifiers matching field names (direct field access)
    3. Assignment expressions to field names
    """
    results: list[FieldAccess] = []
    seen: set[tuple[str, int, bool]] = set()  # (field_name, line, is_write)

    if not local_fields:
        return results

    # Collect local variable and parameter names to exclude from field matching
    local_names: set[str] = set()
    local_var_q = _get_query("local_names_var", """
        (local_variable_declaration
          declarator: (variable_declarator
            name: (identifier) @vname))
    """)
    cursor = ts.QueryCursor(local_var_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(local_var_q, captures)
        for n in cd.get("vname", []):
            local_names.add(_node_text(n, source))

    # Method parameters
    method_param_q = _get_query("local_names_param", """
        (formal_parameter name: (identifier) @pname)
    """)
    cursor = ts.QueryCursor(method_param_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(method_param_q, captures)
        for n in cd.get("pname", []):
            local_names.add(_node_text(n, source))

    # 1. Detect `this.field` and `object.field` patterns where field matches a local field
    field_access_q = _get_query("field_access_extract", """
        (field_access
          object: (_) @obj
          field: (identifier) @field)
    """)
    cursor = ts.QueryCursor(field_access_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(field_access_q, captures)
        obj_nodes = cd.get("obj", [])
        field_nodes = cd.get("field", [])
        if not obj_nodes or not field_nodes:
            continue

        field_name = _node_text(field_nodes[0], source)
        obj_text = _node_text(obj_nodes[0], source)

        if field_name not in local_fields:
            continue

        # Capture `this.field` accesses (both reads and writes)
        if obj_text != "this":
            continue

        line = field_nodes[0].start_point.row + 1

        # Check if this is a write (parent is assignment and our node is the left side)
        is_write = _is_assignment_target(field_nodes[0])

        key = (field_name, line, is_write)
        if key in seen:
            continue
        seen.add(key)

        enclosing_method = _find_enclosing_method_fast(field_nodes[0], source, method_byte_map, class_byte_map)
        enclosing_class = _find_enclosing_class_fast(field_nodes[0], source, class_byte_map)

        if enclosing_method:
            results.append(FieldAccess(
                field_name=field_name,
                method_name=enclosing_method,
                class_name=enclosing_class or "",
                file_path=file_path,
                line=line,
                is_write=is_write,
            ))

    # Also detect chained `this.field.method()` — the `this.field` part is an access
    chained_access_q = _get_query("chained_field_access", """
        (field_access
          object: (field_access
            object: (identifier) @obj
            field: (identifier) @inner_field)
          field: (identifier) @outer_field)
    """)
    cursor = ts.QueryCursor(chained_access_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(chained_access_q, captures)
        obj_nodes = cd.get("obj", [])
        inner_nodes = cd.get("inner_field", [])
        if not obj_nodes or not inner_nodes:
            continue

        obj_text = _node_text(obj_nodes[0], source)
        inner_name = _node_text(inner_nodes[0], source)

        if obj_text != "this" or inner_name not in local_fields:
            continue

        line = inner_nodes[0].start_point.row + 1
        key = (inner_name, line, False)  # read access (part of chain)
        if key in seen:
            continue
        seen.add(key)

        enclosing_method = _find_enclosing_method_fast(inner_nodes[0], source, method_byte_map, class_byte_map)
        enclosing_class = _find_enclosing_class_fast(inner_nodes[0], source, class_byte_map)

        if enclosing_method:
            results.append(FieldAccess(
                field_name=inner_name,
                method_name=enclosing_method,
                class_name=enclosing_class or "",
                file_path=file_path,
                line=line,
                is_write=False,
            ))

    # 2. Detect bare identifier field accesses — only when used as assignment targets
    #    (field writes without `this.` prefix, e.g., `status = "active"`)
    assignment_q = _get_query("field_assignment_bare", """
        (assignment_expression
          left: (identifier) @lhs)
    """)
    cursor = ts.QueryCursor(assignment_q)
    for _, captures in cursor.matches(root):
        cd = _captures_to_dict(assignment_q, captures)
        lhs_nodes = cd.get("lhs", [])
        if not lhs_nodes:
            continue

        id_node = lhs_nodes[0]
        id_text = _node_text(id_node, source)

        if id_text not in local_fields:
            continue

        # Skip if it's a local variable or parameter
        if id_text in local_names:
            continue

        # Skip if parent is a field_access (already captured)
        parent = id_node.parent
        if parent and parent.type == "field_access":
            continue

        # Skip if it's in a local variable declaration
        grand = parent
        is_decl = False
        while grand:
            if grand.type == "local_variable_declaration":
                is_decl = True
                break
            grand = grand.parent
        if is_decl:
            continue

        line = id_node.start_point.row + 1

        key = (id_text, line, True)
        if key in seen:
            continue
        seen.add(key)

        enclosing_method = _find_enclosing_method_fast(id_node, source, method_byte_map, class_byte_map)
        enclosing_class = _find_enclosing_class_fast(id_node, source, class_byte_map)

        if enclosing_method:
            results.append(FieldAccess(
                field_name=id_text,
                method_name=enclosing_method,
                class_name=enclosing_class or "",
                file_path=file_path,
                line=line,
                is_write=True,
            ))

    return results


def _is_assignment_target(node: ts.Node) -> bool:
    """Check if an identifier/field node is on the left side of an assignment."""
    parent = node.parent
    while parent:
        if parent.type == "assignment_expression":
            # Check if our node is the left-hand side
            left = parent.child_by_field_name("left")
            if left and left.id == node.id:
                return True
            # Also check by byte position
            if parent.children and parent.children[0].start_byte == node.start_byte:
                return True
            break
        # Stop if we hit a statement boundary
        if parent.type in ("expression_statement", "return_statement", "if_statement",
                          "for_statement", "while_statement", "block"):
            break
        parent = parent.parent
    return False
