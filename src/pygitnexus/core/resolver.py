"""Cross-file resolution: import targets, method call targets."""

from __future__ import annotations

import functools
import re
from pathlib import Path

from .models import (
    CallSite,
    ClassDef,
    ImportDecl,
    MethodDef,
    ParsedFile,
)

# Pre-compiled regex for camelCase splitting
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)")


@functools.lru_cache(maxsize=8192)
def _names_related(a: str, b: str) -> bool:
    """Check if two type names are plausibly related by shared word tokens."""
    a_words = set(_CAMEL_SPLIT_RE.findall(a))
    b_words = set(_CAMEL_SPLIT_RE.findall(b))
    shared = a_words & b_words - {"get", "set", "is", "the", "of", "and"}
    return bool(shared)


# Methods from test framework static imports that should be matched globally.
_STATIC_IMPORT_METHODS: set[str] = {
    # JUnit 5 Assertions
    "assertEquals", "assertNotEquals", "assertTrue", "assertFalse",
    "assertNull", "assertNotNull", "assertSame", "assertNotSame",
    "assertArrayEquals", "assertLinesMatch", "assertIterableEquals",
    "assertThrows", "assertDoesNotThrow", "assertTimeout",
    "assertTimeoutPreemptively", "fail", "assertAll",
    # JUnit 4
    "assertThat",
    # Mockito
    "verify", "when", "mock", "spy", "doReturn", "doThrow",
    "doNothing", "doAnswer", "doCallRealMethod", "never",
    "times", "atLeast", "atMost", "atLeastOnce", "only",
    "inOrder", "reset",
    # Hamcrest matchers
    "isNull", "isNotNull", "notNullValue", "nullValue",
    # Common test utility methods
    "isEmpty", "isBlank", "isNotBlank",
}

# JDK classes with static methods that should never be matched to project methods.
# Key: class simple name, Value: set of method names that are JDK-only.
_JDK_STATIC_METHODS: dict[str, set[str]] = {
    "Files": {"list", "walk", "copy", "move", "delete", "createDirectories",
              "exists", "isDirectory", "isRegularFile", "readAllBytes",
              "readAllLines", "write", "newInputStream", "newOutputStream"},
    "Paths": {"get"},
    "Path": {"of"},
    "Optional": {"get", "empty", "of", "ofNullable", "isPresent", "orElse"},
    "System": {"currentTimeMillis", "nanoTime", "getProperty", "exit",
               "arraycopy", "gc", "err", "out", "in"},
    "Objects": {"requireNonNull", "isNull", "nonNull", "equals", "hash", "toString"},
    "Collections": {"emptyList", "emptyMap", "emptySet", "singletonList",
                    "unmodifiableList", "sort"},
    "Arrays": {"asList", "sort", "stream", "copyOf", "fill", "toString"},
    "Collectors": {"toList", "toMap", "toSet", "groupingBy", "joining"},
    "UUID": {"randomUUID", "fromString"},
    "Instant": {"now", "ofEpochMilli", "parse", "MIN", "MAX"},
    "LocalDateTime": {"now", "of", "parse", "MIN", "MAX"},
    "Math": {"abs", "max", "min", "round", "floor", "ceil", "pow", "sqrt"},
}


def _is_jdk_static_call(receiver: str, method: str) -> bool:
    """Check if the call is to a known JDK static method that shouldn't match project code."""
    base = receiver.split(".")[-1]
    jdk_methods = _JDK_STATIC_METHODS.get(base)
    if jdk_methods and method in jdk_methods:
        return True
    return False


def resolve_imports(
    parsed_files: list[ParsedFile],
) -> dict[str, str]:
    """Map fully qualified class names to file paths.

    Returns: {"com.example.Foo" -> "src/main/java/com/example/Foo.java"}
    """
    mapping: dict[str, list[str]] = {}

    for pf in parsed_files:
        for cls in pf.classes:
            fqn = cls.name
            mapping.setdefault(fqn, []).append(pf.file_path)

    # Also derive FQN from file paths (package inference)
    for pf in parsed_files:
        # Infer package from directory structure
        package = _infer_package(pf.file_path)
        if package:
            base = Path(pf.file_path).stem
            fqn = f"{package}.{base}"
            if fqn not in mapping:
                mapping[fqn] = [pf.file_path]

    # Flatten to single path (first match)
    return {k: v[0] for k, v in mapping.items()}


def resolve_calls_chunk(
    chunk: list[ParsedFile],
    class_map: dict[str, str],
) -> list[tuple[str, str, str, str, float]]:
    """Resolve call targets for a chunk of parsed files.

    This is the parallelizable unit of work for resolve_calls.
    Builds full indices from all files, then resolves calls only for this chunk.
    """
    # We need ALL parsed files to build indices, not just the chunk.
    # So the caller must pass all_files separately.
    # This function is a no-op placeholder; see _resolve_calls_parallel in pipeline.py
    # which calls the internal _resolve_chunk with pre-built indices.
    return []


def resolve_calls_chunk_with_indices(
    chunk: list[ParsedFile],
    all_parsed_files: list[ParsedFile],
    class_map: dict[str, str],
) -> list[tuple[str, str, str, str, float]]:
    """Resolve call targets for a chunk, using indices built from all files."""
    # Build indices from all files (same as resolve_calls)
    method_index: dict[str, list[MethodDef]] = {}
    for pf in all_parsed_files:
        for method in pf.methods:
            if method.class_name:
                key = f"{method.class_name}.{method.name}"
                method_index.setdefault(key, []).append(method)

    method_name_index: dict[str, list[tuple[str, MethodDef]]] = {}
    for pf in all_parsed_files:
        for method in pf.methods:
            if method.class_name:
                class_base = method.class_name.split(".")[-1]
                method_name_index.setdefault(method.name, []).append((class_base, method))

    return_type_index: dict[str, list[tuple[str, str]]] = {}
    for pf in all_parsed_files:
        for method in pf.methods:
            if method.return_type and method.return_type != "void":
                return_type_index.setdefault(method.name, []).append(
                    (method.class_name or "", method.return_type)
                )

    constructor_index: dict[str, list] = {}
    for pf in all_parsed_files:
        for ctor in pf.constructors:
            if ctor.class_name:
                constructor_index.setdefault(ctor.class_name, []).append(ctor)

    extends_map: dict[str, str] = {}
    for pf in all_parsed_files:
        for cls in pf.classes:
            if cls.extends:
                extends_map[cls.name] = cls.extends
                simple = cls.name.split(".")[-1]
                extends_map[simple] = cls.extends

    implement_map: dict[str, list[str]] = {}
    interface_set: set[str] = set()
    for pf in all_parsed_files:
        for cls in pf.classes:
            if cls.is_interface:
                interface_set.add(cls.name)
                interface_set.add(cls.name.split(".")[-1])
            for impl in cls.implements:
                impl_base = impl.split(".")[-1]
                for iface_fqn in interface_set:
                    if iface_fqn.endswith(f".{impl_base}") or iface_fqn == impl_base:
                        implement_map.setdefault(iface_fqn, []).append(cls.name)
                        break
                implement_map.setdefault(impl, []).append(cls.name)

    static_field_index: dict[str, list[tuple[str, str]]] = {}
    for pf in all_parsed_files:
        for field in pf.fields:
            if field.is_static:
                static_field_index.setdefault(field.name, []).append(
                    (field.type_name, field.class_name)
                )

    simple_to_fqn: dict[str, list[str]] = {}
    for fqn in class_map:
        simple = fqn.rsplit(".", 1)[-1]
        simple_to_fqn.setdefault(simple, []).append(fqn)

    # Resolve only calls in this chunk
    resolved: list[tuple[str, str, str, str, float]] = []
    for pf in chunk:
        for call in pf.calls:
            target_methods = _resolve_single_call(
                call, method_index, method_name_index, class_map, constructor_index,
                extends_map, static_field_index, implement_map, interface_set,
                return_type_index, simple_to_fqn,
            )
            for target_method, confidence in target_methods:
                resolved.append((
                    call.caller_method,
                    pf.file_path,
                    target_method.name,
                    target_method.file_path,
                    confidence,
                ))

    return resolved


def resolve_calls(
    parsed_files: list[ParsedFile],
    class_map: dict[str, str],
) -> list[tuple[str, str, str, str, float]]:
    """Resolve method call targets.

    Returns list of (caller_method_name, caller_file_path,
                     target_method_name, target_file_path, confidence).
    """
    resolved: list[tuple[str, str, str, str, float]] = []

    # Build method index: class_name.method_name -> list of MethodDef
    method_index: dict[str, list[MethodDef]] = {}
    for pf in parsed_files:
        for method in pf.methods:
            if method.class_name:
                key = f"{method.class_name}.{method.name}"
                method_index.setdefault(key, []).append(method)

    # Build method name index: method_name -> list of (class_base, MethodDef)
    # Used for fast fallback lookup without iterating entire method_index
    method_name_index: dict[str, list[tuple[str, MethodDef]]] = {}
    for pf in parsed_files:
        for method in pf.methods:
            if method.class_name:
                class_base = method.class_name.split(".")[-1]
                method_name_index.setdefault(method.name, []).append((class_base, method))

    # Build return type index: method_name -> list of (class_name, return_type)
    # Used for cross-file chained call resolution (e.g., service.getUser().getAddress())
    return_type_index: dict[str, list[tuple[str, str]]] = {}
    for pf in parsed_files:
        for method in pf.methods:
            if method.return_type and method.return_type != "void":
                return_type_index.setdefault(method.name, []).append(
                    (method.class_name or "", method.return_type)
                )

    # Build constructor index: class_name -> list of ConstructorDef
    constructor_index: dict[str, list] = {}
    for pf in parsed_files:
        for ctor in pf.constructors:
            if ctor.class_name:
                constructor_index.setdefault(ctor.class_name, []).append(ctor)

    # Build class inheritance map: class_name -> parent_class_name (FQN or simple)
    extends_map: dict[str, str] = {}
    for pf in parsed_files:
        for cls in pf.classes:
            if cls.extends:
                extends_map[cls.name] = cls.extends
                # Also map simple name
                simple = cls.name.split(".")[-1]
                extends_map[simple] = cls.extends

    # Build interface implementation map: interface_fqn -> list of implementing class fqns
    implement_map: dict[str, list[str]] = {}
    interface_set: set[str] = set()
    for pf in parsed_files:
        for cls in pf.classes:
            if cls.is_interface:
                interface_set.add(cls.name)
                interface_set.add(cls.name.split(".")[-1])  # also simple name
            for impl in cls.implements:
                impl_base = impl.split(".")[-1]
                # Try to match by simple name against known interfaces
                for iface_fqn in interface_set:
                    if iface_fqn.endswith(f".{impl_base}") or iface_fqn == impl_base:
                        implement_map.setdefault(iface_fqn, []).append(cls.name)
                        break
                # Also store by the raw implements value
                implement_map.setdefault(impl, []).append(cls.name)

    # Build static field type index: field_name -> list of (type_name, class_name)
    # Used to resolve receivers like "CACHED_HANDLE" or "signService"
    static_field_index: dict[str, list[tuple[str, str]]] = {}
    for pf in parsed_files:
        for field in pf.fields:
            if field.is_static:
                static_field_index.setdefault(field.name, []).append(
                    (field.type_name, field.class_name)
                )

    # Build simple-to-FQN reverse index for O(1) class name lookup
    simple_to_fqn: dict[str, list[str]] = {}
    for fqn in class_map:
        simple = fqn.rsplit(".", 1)[-1]
        simple_to_fqn.setdefault(simple, []).append(fqn)

    # Resolve each call site
    for pf in parsed_files:
        for call in pf.calls:
            target_methods = _resolve_single_call(
                call, method_index, method_name_index, class_map, constructor_index,
                extends_map, static_field_index, implement_map, interface_set,
                return_type_index, simple_to_fqn,
            )
            for target_method, confidence in target_methods:
                resolved.append((
                    call.caller_method,
                    pf.file_path,
                    target_method.name,
                    target_method.file_path,
                    confidence,
                ))

    return resolved


def _infer_package(file_path: str) -> str | None:
    """Infer Java package from file path (convention: src/main/java/com/example/Foo.java)."""
    path = Path(file_path)
    parts = path.parts

    # Common source roots to strip
    source_roots = {"src", "main", "java", "test", "generated"}
    start_idx = None
    for i, part in enumerate(parts):
        if part == "java" and i > 0 and parts[i - 1] in {"main", "test", "src"}:
            start_idx = i + 1
            break

    if start_idx is None:
        # Try to find any directory that looks like a package
        for i, part in enumerate(parts[:-1]):
            if "." not in part and not part.startswith("."):
                # Check if subsequent parts form a package-like path
                remaining = parts[i:-1]
                if len(remaining) >= 2 and all(
                    p.isidentifier() for p in remaining
                ):
                    start_idx = i
                    break

    if start_idx is None:
        return None

    pkg_parts = parts[start_idx:-1]  # exclude filename
    return ".".join(pkg_parts) if pkg_parts else None


# Common JDK method names that appear everywhere — skip in fallback matching.
_COMMON_JDK_METHODS: set[str] = {
    "get", "set", "is", "has", "add", "put", "remove", "contains",
    "size", "isEmpty", "clear", "iterator", "toArray", "stream",
    "forEach", "filter", "map", "flatMap", "collect", "reduce",
    "findFirst", "findAny", "anyMatch", "allMatch", "noneMatch",
    "equals", "hashCode", "toString", "clone", "compareTo",
    "close", "flush", "write", "read", "open", "create",
    "info", "debug", "warn", "error", "log",
    "of", "from", "to", "build", "parse", "format",
    # JDK-only methods commonly matched to project code
    "getMessage", "getCause", "getStackTrace", "printStackTrace",
    "getData", "isEmpty", "keySet", "values", "entrySet",
    "getOrDefault", "compute", "computeIfAbsent", "merge",
    "isPresent", "orElse", "orElseGet", "orElseThrow", "ifPresent",
    "asText", "get", "path", "has", "hasNonNull",
}

# Known JDK class simple names whose method calls should NOT be matched to project code
# via fallback name matching.
_JDK_CLASSES: set[str] = {
    "Logger", "LoggerFactory",
    "Map", "HashMap", "LinkedHashMap", "TreeMap", "ConcurrentHashMap",
    "List", "ArrayList", "LinkedList", "CopyOnWriteArrayList",
    "Set", "HashSet", "LinkedHashSet", "TreeSet", "CopyOnWriteArraySet",
    "Collection", "Collections",
    "Optional", "OptionalInt", "OptionalLong", "OptionalDouble",
    "Stream", "IntStream", "LongStream", "DoubleStream",
    "JsonNode", "ObjectNode", "ArrayNode", "TextNode",
    "String", "StringBuilder", "StringBuffer",
    "Exception", "RuntimeException", "Throwable", "Error",
    "Object", "Class",
    "System", "Runtime",
    "Files", "Paths", "Path",
    "Math", "StrictMath",
    "UUID", "Instant", "LocalDateTime", "LocalDate", "LocalTime",
    "Date", "Calendar", "TimeZone",
    "URI", "URL",
    "Arrays", "Objects",
    "Collectors", "Collector",
}


def _is_jdk_class(name: str) -> bool:
    """Check if a type name (simple or FQN) refers to a known JDK class."""
    base = name.split(".")[-1]
    return base in _JDK_CLASSES or name in _JDK_CLASSES


def _resolve_single_call(
    call: CallSite,
    method_index: dict[str, list[MethodDef]],
    method_name_index: dict[str, list[tuple[str, MethodDef]]],
    class_map: dict[str, str],
    constructor_index: dict[str, list],
    extends_map: dict[str, str],
    static_field_index: dict[str, list[tuple[str, str]]],
    implement_map: dict[str, list[str]] = None,
    interface_set: set[str] = None,
    return_type_index: dict[str, list[tuple[str, str]]] = None,
    simple_to_fqn: dict[str, list[str]] = None,
) -> list[tuple[MethodDef, float]]:
    """Resolve a call site to target MethodDef with confidence scores.

    Uses type-aware resolution: the receiver's declared type is used to
    narrow down the search to only methods of that class. No fuzzy fallback.
    """
    best: dict[str, tuple[MethodDef, float]] = {}

    def _add(method: MethodDef, confidence: float) -> None:
        key = f"{method.name}:{method.file_path}"
        if key not in best or confidence > best[key][1]:
            best[key] = (method, confidence)

    # Strategy 0: Constructor call — target_name matches a class name (indicating a constructor call)
    # Only applies when the call target is the class itself (e.g., new X() or class-level reference),
    # NOT when it's a regular method call like obj.someMethod() on that class.
    if call.receiver_type:
        # Check if target_name looks like a constructor call (matches the class name)
        simple_name = call.receiver_type.split(".")[-1]
        is_ctor_call = (call.target_name == simple_name or call.target_name == call.receiver_type)

        if is_ctor_call and call.receiver_type in constructor_index:
            for ctor in constructor_index[call.receiver_type]:
                ctor_method = MethodDef(
                    name=ctor.class_name,
                    class_name=ctor.class_name,
                    file_path=ctor.file_path,
                    start_line=ctor.start_line,
                    end_line=ctor.end_line,
                    return_type="",
                    parameters=ctor.parameters,
                    is_static=False,
                    is_public=ctor.is_public,
                    is_constructor=True,
                    content=ctor.content,
                )
                _add(ctor_method, 0.9)
            if best:
                return list(best.values())

        # Fallback: no explicit constructor, match to Class node itself
        # Only for constructor-like target names
        if is_ctor_call:
            for fqn in simple_to_fqn.get(call.receiver_type, []):
                for key, methods in method_index.items():
                    if key.startswith(f"{fqn}."):
                        for method in methods:
                            ctor_method = MethodDef(
                                name=fqn.split(".")[-1],
                                class_name=fqn,
                                file_path=method.file_path,
                                start_line=0,
                                end_line=0,
                                return_type="",
                                is_static=False,
                                is_public=True,
                                is_constructor=True,
                            )
                            _add(ctor_method, 0.85)
                if best:
                    return list(best.values())

    # Strategy 1: receiver_type is known (from local variable type inference)
    if call.receiver_type:
        # Try exact class name match first (highest confidence)
        key = f"{call.receiver_type}.{call.target_name}"
        if key in method_index:
            for method in method_index[key]:
                _add(method, 0.9)
        if best:
            return list(best.values())

        # Interface dispatch: if receiver_type is an interface, find all
        # implementing classes and add calls to their methods.
        if implement_map and interface_set:
            rt = call.receiver_type
            iface_key = rt
            if rt not in implement_map:
                # Try FQN matching against interface_set
                for iface_fqn in interface_set:
                    if iface_fqn.endswith(f".{rt}") or iface_fqn == rt:
                        iface_key = iface_fqn
                        break

            if iface_key in implement_map:
                for impl_class in implement_map[iface_key]:
                    full_key = f"{impl_class}.{call.target_name}"
                    if full_key in method_index:
                        for method in method_index[full_key]:
                            _add(method, 0.6)
                if best:
                    return list(best.values())

        # Builder pattern: if target is build/builder, try X.Builder / XBuilder
        if call.target_name in ("build", "builder", "newBuilder"):
            rt_base = call.receiver_type.split(".")[-1]
            builder_candidates = [
                f"{call.receiver_type}.Builder",
                f"{call.receiver_type}.{rt_base}Builder",
                f"{rt_base}Builder",
            ]
            for candidate in builder_candidates:
                full_key = f"{candidate}.{call.target_name}"
                if full_key in method_index:
                    for method in method_index[full_key]:
                        _add(method, 0.85)
            if best:
                return list(best.values())

        # Try matching by class name suffix (e.g., "UserService" matches "com.x.UserService")
        for fqn in simple_to_fqn.get(call.receiver_type, []):
            full_key = f"{fqn}.{call.target_name}"
            if full_key in method_index:
                for method in method_index[full_key]:
                    _add(method, 0.8)
        if best:
            return list(best.values())

        # Fallback: receiver_type is external (e.g., OSProcess, JdbcTemplate).
        # Match by method name across project classes with lower confidence,
        # but only when the target class name is similar to the receiver type name.
        target = call.target_name
        rt_base = call.receiver_type.split(".")[-1]  # simple name part

        # Skip JDK static utility classes (Files, Paths, etc.)
        if _is_jdk_static_call(rt_base, target):
            return list(best.values())

        # Skip calls on known JDK classes — these should not match project code
        if _is_jdk_class(call.receiver_type):
            return list(best.values())

        # Skip ultra-common JDK method names in fallback matching
        if target in _COMMON_JDK_METHODS:
            return list(best.values())

        # Use method_name_index for O(1) lookup instead of iterating all methods
        for class_base, method in method_name_index.get(target, []):
            if _names_related(rt_base, class_base):
                _add(method, 0.5)
        return list(best.values())

    # Strategy 1.5: Cross-file return type propagation.
    # When receiver_type is unknown but receiver name matches a known method,
    # use that method's return type to resolve the call.
    # e.g., service.getUser().getAddress() — getAddress() has receiver="getUser"
    # which returns User, so resolve getAddress() against User class.
    if not call.receiver_type and call.receiver and return_type_index:
        receiver_base = call.receiver.split(".")[-1]
        if receiver_base in return_type_index:
            for _cls, ret_type in return_type_index[receiver_base]:
                full_key = f"{ret_type}.{call.target_name}"
                if full_key in method_index:
                    for method in method_index[full_key]:
                        _add(method, 0.55)

                # Also try matching return type by class name suffix
                for fqn in simple_to_fqn.get(ret_type, []):
                    full_key = f"{fqn}.{call.target_name}"
                    if full_key in method_index:
                        for method in method_index[full_key]:
                            _add(method, 0.45)
                    break

            if best:
                return list(best.values())

    # Strategy 2: receiver is a known class/interface name (e.g., static call "SomeClass.method()")
    if call.receiver:
        # First check if receiver is a static field name — use its declared type
        if call.receiver in static_field_index:
            for field_type, field_class in static_field_index[call.receiver]:
                # Try exact match: field_type.method_name
                full_key = f"{field_type}.{call.target_name}"
                if full_key in method_index:
                    for method in method_index[full_key]:
                        _add(method, 0.75)
                # Try FQN lookup
                if not best:
                    for fqn in simple_to_fqn.get(field_type, []):
                        full_key = f"{fqn}.{call.target_name}"
                        if full_key in method_index:
                            for method in method_index[full_key]:
                                _add(method, 0.7)
                        break
            if best:
                return list(best.values())

        # Try exact class name match
        key = f"{call.receiver}.{call.target_name}"
        if key in method_index:
            for method in method_index[key]:
                _add(method, 0.9)
        if best:
            return list(best.values())

        # Try matching by class name suffix
        for fqn in simple_to_fqn.get(call.receiver, []):
            full_key = f"{fqn}.{call.target_name}"
            if full_key in method_index:
                for method in method_index[full_key]:
                    _add(method, 0.8)
        if best:
            return list(best.values())

        # Receiver is an external class — try matching method name across project
        # Only for class-like receivers (starts with uppercase)
        if call.receiver and call.receiver[0].isupper():
            # Skip known JDK utility classes with static methods that shouldn't
            # be matched to project methods (e.g., Files.list, Paths.get, Optional.get)
            if _is_jdk_static_call(call.receiver, call.target_name):
                return list(best.values())

            # Skip calls on known JDK classes
            if _is_jdk_class(call.receiver):
                return list(best.values())

            # Skip ultra-common JDK method names in fallback matching
            if call.target_name in _COMMON_JDK_METHODS:
                return list(best.values())

            # Use method_name_index for O(1) lookup
            for class_base, method in method_name_index.get(call.target_name, []):
                _add(method, 0.5)
        return list(best.values())

    # Strategy 3: no receiver (same-class call like "this.method()" or bare "method()")
    # Use caller_class field (set by extractor from enclosing class detection)
    if call.caller_class:
        lookup_key = f"{call.caller_class}.{call.target_name}"
        if lookup_key in method_index:
            for method in method_index[lookup_key]:
                _add(method, 0.7)

        # Inheritance: look up parent class methods
        if not best:
            parent = extends_map.get(call.caller_class)
            while parent and not best:
                parent_key = f"{parent}.{call.target_name}"
                if parent_key in method_index:
                    for method in method_index[parent_key]:
                        _add(method, 0.65)
                # Also try FQN matching
                if not best:
                    for fqn in simple_to_fqn.get(parent, []):
                        full_key = f"{fqn}.{call.target_name}"
                        if full_key in method_index:
                            for method in method_index[full_key]:
                                _add(method, 0.6)
                        break
                # Walk up further
                parent = extends_map.get(parent)

        if best:
            return list(best.values())

        # Try FQN matching: if caller_class is simple, look for FQN classes ending with it
        for fqn in simple_to_fqn.get(call.caller_class, []):
            full_key = f"{fqn}.{call.target_name}"
            if full_key in method_index:
                for method in method_index[full_key]:
                    _add(method, 0.6)
        if best:
            return list(best.values())

        # Global name fallback: for bare method calls from known test-framework
        # static imports (JUnit, Mockito, etc.), match by method name across all
        # project methods. Restricted to a whitelist to avoid false positives on
        # common names like get/set/start.
        if not best and call.target_name in _STATIC_IMPORT_METHODS:
            for _, method in method_name_index.get(call.target_name, []):
                _add(method, 0.3)
        if best:
            return list(best.values())

    # Strategy 4: fallback to caller_method parsing (legacy: "ClassName.methodName")
    if not best and call.caller_method and "." in call.caller_method:
        # Skip ultra-common JDK methods that shouldn't be matched via fallback
        if call.target_name in _COMMON_JDK_METHODS:
            return list(best.values())

        caller_class = call.caller_method.rsplit(".", 1)[0]
        lookup_key = f"{caller_class}.{call.target_name}"
        if lookup_key in method_index:
            for method in method_index[lookup_key]:
                _add(method, 0.6)

    # No match
    return list(best.values())


def _make_method_id(method_name: str, file_path: str, start_line: int = 0) -> str:
    """Create a unique method ID."""
    # Sanitize: replace dots and slashes
    safe_name = method_name.replace(".", "_").replace("/", "_")
    safe_path = file_path.replace("/", "_").replace(".", "_")
    return f"Method_{safe_path}_{safe_name}_{start_line}"
