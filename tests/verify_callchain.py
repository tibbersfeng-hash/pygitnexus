#!/usr/bin/env python3
"""PyGitNexus call chain verification — tree-sitter AST-based ground truth.

This script independently extracts method calls from Java source code using
tree-sitter, then compares them against PyGitNexus's CALLS relations in
KuzuDB to compute precision, recall, and identify false positives/negatives.

Usage:
    python verify_callchain.py /path/to/java/project

Output:
    - verification_summary.txt    Summary report with metrics
    - false_positives.tsv         PyGitNexus calls not in AST ground truth
    - false_negatives.tsv         AST calls not found by PyGitNexus
    - ground_truth_calls.tsv      All AST-extracted calls

Design:
    - AST method_invocation extraction as ground truth (code actually exists)
    - Method-name level comparison (no cross-file resolution dependency)
    - Auto-classification of JDK/common false positive patterns
"""

from __future__ import annotations

import sys
import os
from collections import defaultdict
from pathlib import Path

import tree_sitter_java as tsjava
import tree_sitter as ts

# ─── JDK / Common library methods that should NOT match project code ───

JDK_METHODS = {
    # Object
    "toString", "equals", "hashCode", "getClass", "notify", "notifyAll",
    "wait", "clone", "finalize",
    # String
    "length", "charAt", "substring", "startsWith", "endsWith",
    "trim", "split", "replace", "toLowerCase", "toUpperCase",
    "indexOf", "lastIndexOf", "contains", "isEmpty", "isBlank",
    # Collection / Map
    "get", "put", "remove", "contains", "containsKey", "containsValue",
    "size", "clear", "keySet", "values", "entrySet",
    "add", "addAll",
    "stream", "parallelStream", "iterator", "spliterator",
    "forEach", "replaceAll", "sort",
    "getOrDefault", "computeIfAbsent", "computeIfPresent", "merge",
    "orElse", "orElseGet", "orElseThrow", "isPresent",
    # Logger
    "error", "warn", "info", "debug", "trace",
    # Exception
    "getMessage", "getCause", "printStackTrace",
    # Comparable
    "compareTo", "compare",
    # Stream / Optional / Functional
    "filter", "map", "flatMap", "peek",
    "findFirst", "findAny", "reduce", "collect", "sorted",
    "anyMatch", "allMatch", "noneMatch",
    # Builder / factory patterns
    "builder", "build", "create", "of", "from", "newInstance",
    # Misc JDK
    "now", "thenApply", "thenAccept", "thenRun", "thenCombine",
    "whenComplete", "exceptionally", "join",
    "setName", "getId", "getValue", "setValue", "getName",
    "setType", "getType", "setDescription",
    "setProps", "getProps", "getDateCreated", "setDateCreated",
    "getDateUpdated", "setDateUpdated",
    # HttpServletRequest / HttpServletResponse
    "getRequestURL", "getRequestURI", "getParameter", "getAttribute",
    "setAttribute", "removeAttribute", "getSession", "getHeader",
    "getContentType", "setContentType", "setStatus", "setHeader",
    "setDateHeader", "getOutputStream", "getWriter", "sendRedirect",
    "getCookies", "getMethod", "getProtocol", "getScheme",
    "getServerName", "getServerPort", "getContextPath",
    "getIntHeader", "getPart", "getParts",
    # HttpSession
    "invalidate", "isNew", "getCreationTime", "getLastAccessedTime",
    "getMaxInactiveInterval", "setMaxInactiveInterval",
    # Reflection
    "invoke", "getDeclaredFields", "getDeclaredMethods",
    "getDeclaredConstructors", "getFields", "getMethods",
    "getConstructor", "getMethod", "getField",
    "setAccessible", "isAccessible", "newInstance",
    "isAnnotationPresent", "getAnnotation", "getAnnotations",
    # Class / ClassLoader
    "getSimpleName", "getName", "getCanonicalName",
    "forName", "getResource", "getResourceAsStream",
    "getClassLoader", "isInstance", "isAssignableFrom",
    "getSuperclass", "getInterfaces",
    # StringBuilder / StringBuffer
    "append", "insert", "delete", "reverse", "capacity",
    # Math / Number
    "abs", "max", "min", "round", "floor", "ceil", "sqrt",
    "pow", "random", "sin", "cos", "tan",
    "currentTimeMillis", "nanoTime",
    "parseInt", "parseLong", "parseDouble", "parseFloat",
    "valueOf", "toString",
    # Arrays / Collections utils
    "asList", "sort", "binarySearch", "fill", "copyOf",
    "equals", "deepEquals", "hashCode", "stream", "spliterator",
    "sum", "average", "count",
    # Pattern / Regex
    "compile", "matcher", "matches", "pattern", "quote",
    # Thread
    "start", "run", "stop", "interrupt", "isInterrupted",
    "sleep", "yield", "join", "setName", "getName", "setPriority",
    # Executor / Future
    "execute", "submit", "shutdown", "shutdownNow", "isShutdown",
    "isTerminated", "awaitTermination", "get", "cancel", "isDone", "isCancelled",
    # Optional
    "empty", "of", "ofNullable", "isPresent", "isEmpty",
    "ifPresent", "ifPresentOrElse", "or", "orElse", "orElseGet",
    "orElseThrow", "map", "flatMap", "filter", "stream",
    # Stream terminal ops
    "toArray", "toList", "toSet", "toMap",
    # IO
    "read", "write", "close", "flush", "available",
    "transferTo", "skip", "mark", "reset", "markSupported",
    # File / Path
    "exists", "isFile", "isDirectory", "isAbsolute",
    "createNewFile", "delete", "mkdir", "mkdirs", "renameTo",
    "list", "listFiles", "getAbsolutePath", "getName",
    "getPath", "getParent", "lastModified", "length",
    # Files (NIO)
    "readAllBytes", "readAllLines", "write", "copy", "move",
    "delete", "exists", "createDirectories", "createFile",
    "newInputStream", "newOutputStream", "newBufferedReader",
    "newDirectoryStream", "list", "walk", "find",
    # JSON / Serialization
    "writeValueAsString", "readValue", "writeValue",
    "toJson", "fromJson",
    # BigDecimal / BigInteger
    "add", "subtract", "multiply", "divide", "remainder",
    "compareTo", "setScale", "stripTrailingZeros",
    # Enum
    "name", "ordinal", "values", "valueOf",
    # Spring / Framework (common)
    "hasText", "hasLength", "isEmpty", "isBlank",
    "isNull", "isNotNull", "notNull", "notNullValue",
}

# Methods that are nearly always project-specific (good signal)
PROJECT_SPECIFIC_HINTS = {
    "login", "logout", "register", "authenticate", "authorize",
    "save", "update", "delete", "findById", "findAll", "findBy",
    "create", "process", "handle", "execute", "validate",
    "parse", "format", "convert", "transform",
    "send", "receive", "publish", "subscribe",
    "init", "start", "stop", "shutdown",
    "getById", "getByName", "getList", "getPage",
}


# ─── AST Ground Truth Extraction ───

def _get_parser() -> ts.Parser:
    lang = ts.Language(tsjava.language())
    return ts.Parser(lang)


def extract_ground_truth(project_path: str) -> list[dict]:
    """Extract all method calls from Java files using tree-sitter.

    Returns list of dicts with keys:
        caller_class, caller_method, target_method, line, file_path
    """
    parser = _get_parser()
    lang = ts.Language(tsjava.language())

    root = Path(project_path)
    skip_dirs = {"node_modules", "dist", "build", "vendor", ".git", "target", "build"}
    java_files = list(root.rglob("*.java"))

    # Pre-compile queries
    method_q = ts.Query(lang, """
        [
            (method_declaration
              name: (identifier) @mname
              body: (block) @body)
            (constructor_declaration
              name: (identifier) @mname
              body: (constructor_body) @body)
        ]
    """)
    call_q = ts.Query(lang, """
        (method_invocation
          name: (identifier) @call_name
        ) @call
    """)
    class_q = ts.Query(lang, """
        (class_declaration
          name: (identifier) @cname
          body: (class_body) @cbody)
    """)

    all_calls: list[dict] = []
    parsed_count = 0

    for fp in java_files:
        if any(skip in str(fp) for skip in skip_dirs):
            continue

        try:
            source = fp.read_bytes()
        except (OSError, PermissionError):
            continue

        try:
            tree = parser.parse(source)
        except Exception:
            continue

        if not tree.root_node:
            continue

        parsed_count += 1

        rel_path = str(fp.relative_to(root))

        # Build class -> body map for this file
        class_bodies = []
        class_cursor = ts.QueryCursor(class_q)
        for _, caps in class_cursor.matches(tree.root_node):
            cname_list = caps.get("cname", [])
            cbody_list = caps.get("cbody", [])
            if cname_list and cbody_list:
                cname = source[cname_list[0].start_byte:cname_list[0].end_byte].decode("utf-8", errors="replace")
                class_bodies.append((cname, cbody_list[0]))

        # Find all methods and their calls
        method_cursor = ts.QueryCursor(method_q)
        method_matches = method_cursor.matches(tree.root_node)

        for _, captures in method_matches:
            mname_nodes = captures.get("mname", [])
            body_nodes = captures.get("body", [])
            if not mname_nodes or not body_nodes:
                continue

            mname = source[mname_nodes[0].start_byte:mname_nodes[0].end_byte].decode("utf-8", errors="replace")

            # Extract class name by checking which class body contains this method
            class_name = "Unknown"
            for cname, cbody in class_bodies:
                if cbody.start_byte <= mname_nodes[0].start_byte <= cbody.end_byte:
                    class_name = cname
                    break

            # Find all calls within this method body
            body = body_nodes[0]
            call_cursor = ts.QueryCursor(call_q)
            call_matches = call_cursor.matches(body)

            for _, call_captures in call_matches:
                call_nodes = call_captures.get("call", [])
                call_name_nodes = call_captures.get("call_name", [])
                if not call_nodes or not call_name_nodes:
                    continue

                call_name = source[call_name_nodes[0].start_byte:call_name_nodes[0].end_byte].decode("utf-8", errors="replace")
                line = call_nodes[0].start_point.row + 1

                all_calls.append({
                    "caller_class": class_name,
                    "caller_method": mname,
                    "target_method": call_name,
                    "line": line,
                    "file_path": rel_path,
                })

    print(f"Parsed {parsed_count} Java files, extracted {len(all_calls)} raw calls")
    return all_calls


def _extract_package(source: bytes) -> str:
    """Extract package declaration from Java source."""
    text = source.decode("utf-8", errors="replace")
    for line in text.splitlines()[:10]:
        line = line.strip()
        if line.startswith("package "):
            return line[len("package "):].rstrip(";").strip()
    return ""


# ─── PyGitNexus KuzuDB Extraction ───

def extract_pygitnexus_calls(project_path: str) -> list[dict]:
    """Read CALLS relations from PyGitNexus KuzuDB."""
    try:
        from pygitnexus.graph.store import GraphStore
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from pygitnexus.graph.store import GraphStore

    db_path = Path(project_path) / ".pygitnexus" / "kuzu"
    if not db_path.exists():
        print(f"KuzuDB not found at {db_path}, run 'pygitnexus analyze' first")
        return []

    store = GraphStore(db_path)
    try:
        rows = store.query("""
            MATCH (caller:Method)-[r:CodeRelation {type: 'CALLS'}]->(target:Method)
            RETURN caller.name AS caller, caller.className AS callerClass,
                   target.name AS target, target.className AS targetClass,
                   r.confidence AS confidence
        """)
        return list(rows)
    finally:
        store.close()


# ─── Comparison & Metrics ───

def compare_calls(ground_truth: list[dict], py_calls: list[dict]) -> dict:
    """Compare AST ground truth with PyGitNexus results.

    Comparison strategy (per-method level):
    - For each method in AST, collect all method names it calls (ground truth)
    - For the same method in PyGitNexus, collect all resolved targets
    - If PyGitNexus says A->B where B's method name exists in AST's calls from A,
      it's a match (cross-file resolution is correct)
    - If PyGitNexus says A->B where B is NOT in AST's calls from A AND B is not
      a JDK method, it might be a false positive or a correct cross-file resolution

    We also do a per-file comparison for more precision:
    - For each Java file, compare AST calls vs PyGitNexus calls
    - This filters out JS/TS/Vue sources
    """
    # Filter ground truth to Java files only
    gt_java = [c for c in ground_truth if c.get("file_path", "").endswith(".java")]

    # Build ground truth index: caller_method -> set of target_method (Java only)
    gt_by_method: dict[str, set[str]] = defaultdict(set)
    for call in gt_java:
        gt_by_method[call["caller_method"]].add(call["target_method"])

    # Filter PyGitNexus to Java callers only (exclude minified JS, empty class names)
    py_java = [c for c in py_calls
               if c.get("callerClass", "") not in ("", "None")
               and c.get("callerClass", "") is not None
               # Exclude minified/obfuscated class names (single or two chars)
               and len(c.get("callerClass", "")) >= 3]

    # Build PyGitNexus index: caller -> set of target (Java only)
    py_by_method: dict[str, set[str]] = defaultdict(set)
    py_java_details: list[dict] = []
    for call in py_java:
        caller = call.get("caller", "")
        target = call.get("target", "")
        py_by_method[caller].add(target)
        py_java_details.append(call)

    # Metrics
    true_positives = 0
    false_positives = []
    fn_set: set[tuple[str, str]] = set()

    # Check PyGitNexus calls against ground truth (Java only)
    for call in py_java_details:
        caller = call.get("caller", "")
        target = call.get("target", "")

        ast_targets = gt_by_method.get(caller, set())
        if target in ast_targets:
            true_positives += 1
        elif target in JDK_METHODS:
            continue  # Skip JDK methods
        else:
            false_positives.append(call)

    # Check ground truth calls that PyGitNexus missed (Java only)
    for caller_method, targets in gt_by_method.items():
        py_targets = py_by_method.get(caller_method, set())
        for target_method in targets:
            if target_method not in py_targets and target_method not in JDK_METHODS:
                fn_set.add((caller_method, target_method))

    # Calculate metrics
    total_py_java = len(py_java_details)
    precision = true_positives / total_py_java if total_py_java > 0 else 0
    recall = true_positives / (true_positives + len(fn_set)) if (true_positives + len(fn_set)) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    # Build false negative list
    false_negatives = []
    for caller_method, target_method in fn_set:
        # Find file path from ground truth
        for c in gt_java:
            if c["caller_method"] == caller_method and c["target_method"] == target_method:
                false_negatives.append({
                    "caller_class": c.get("caller_class", "Unknown"),
                    "caller_method": caller_method,
                    "target_method": target_method,
                    "file_path": c.get("file_path", ""),
                })
                break

    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "total_py_calls": total_py_java,
        "total_gt_calls": true_positives + len(fn_set),
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


# ─── Report Generation ───

def generate_report(results: dict, output_dir: str) -> None:
    """Generate verification report files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Summary
    fp = results["false_positives"]
    fn = results["false_negatives"]

    # Classify false positives
    fp_patterns = defaultdict(int)
    for call in fp:
        target = call.get("target", "")
        caller = call.get("caller", "")
        caller_class = call.get("callerClass", "")

        # Check for common patterns
        if target.startswith("get") and len(target) > 3 and target[3:4].isupper():
            fp_patterns["Getter-style method"] += 1
        elif target.startswith("set") and len(target) > 3 and target[3:4].isupper():
            fp_patterns["Setter-style method"] += 1
        elif target == caller:
            fp_patterns["Self-call (constructor/same-name)"] += 1
        elif caller_class and caller_class == "None":
            fp_patterns["Non-Java source (JS/TS/Vue)"] += 1
        elif target.startswith("_") or target.startswith("is"):
            fp_patterns["Private/internal method"] += 1
        else:
            fp_patterns["Cross-file resolved call"] += 1

    with open(out / "verification_summary.txt", "w") as f:
        f.write("=" * 70 + "\n")
        f.write("PyGitNexus Call Chain Verification Report\n")
        f.write("=" * 70 + "\n\n")

        f.write("## Metrics\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| True Positives | {results['true_positives']} |\n")
        f.write(f"| False Positives | {len(fp)} |\n")
        f.write(f"| False Negatives | {len(fn)} |\n")
        f.write(f"| Precision | {results['precision']:.2%} |\n")
        f.write(f"| Recall | {results['recall']:.2%} |\n")
        f.write(f"| F1 Score | {results['f1']:.2%} |\n")
        f.write(f"| Total PyGitNexus CALLS | {results['total_py_calls']} |\n\n")

        f.write("## False Positive Patterns\n\n")
        f.write("| Pattern | Count |\n")
        f.write("|---------|-------|\n")
        for pattern, count in sorted(fp_patterns.items(), key=lambda x: -x[1]):
            f.write(f"| {pattern} | {count} |\n")
        f.write("\n")

        f.write("## Top 20 False Positives (calls not in AST)\n\n")
        f.write("| Caller | Target | Confidence |\n")
        f.write("|--------|--------|-----------|\n")
        for call in fp[:20]:
            caller = f"{call.get('callerClass', '?')}.{call.get('caller', '?')}"
            target = call.get("target", "?")
            conf = f"{call.get('confidence', 0):.2f}"
            f.write(f"| {caller} | {target} | {conf} |\n")
        f.write("\n")

        f.write("## Top 20 False Negatives (missed calls)\n\n")
        f.write("| Caller | Target | File |\n")
        f.write("|--------|--------|------|\n")
        for call in fn[:20]:
            caller = f"{call['caller_class']}.{call['caller_method']}"
            target = call["target_method"]
            fpath = call.get("file_path", "?")
            f.write(f"| {caller} | {target} | {fpath} |\n")

    # False positives TSV
    with open(out / "false_positives.tsv", "w") as f:
        f.write("caller_class\tcaller_method\ttarget_method\tconfidence\n")
        for call in fp:
            f.write(f"{call.get('callerClass', '')}\t{call.get('caller', '')}\t{call.get('target', '')}\t{call.get('confidence', 0):.2f}\n")

    # False negatives TSV
    with open(out / "false_negatives.tsv", "w") as f:
        f.write("caller_class\tcaller_method\ttarget_method\n")
        for call in fn:
            f.write(f"{call['caller_class']}\t{call['caller_method']}\t{call['target_method']}\n")

    print(f"\nReport written to {output_dir}/")
    print(f"  - verification_summary.txt")
    print(f"  - false_positives.tsv ({len(fp)} entries)")
    print(f"  - false_negatives.tsv ({len(fn)} entries)")


# ─── Main ───

def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_callchain.py /path/to/java/project [output_dir]")
        print("\nExtracts method calls from Java source code using tree-sitter")
        print("and compares against PyGitNexus's CALLS relations in KuzuDB.")
        sys.exit(1)

    project_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./verification_output"

    print("=" * 70)
    print("PyGitNexus Call Chain Verification")
    print("=" * 70)
    print(f"Project: {project_path}")
    print(f"Output:  {output_dir}\n")

    # Step 1: Extract ground truth from AST
    print("[1/3] Extracting ground truth calls from AST...")
    ground_truth = extract_ground_truth(project_path)

    # Step 2: Extract PyGitNexus calls from KuzuDB
    print("[2/3] Extracting PyGitNexus calls from KuzuDB...")
    py_calls = extract_pygitnexus_calls(project_path)
    if not py_calls:
        print("No PyGitNexus calls found. Exiting.")
        sys.exit(1)
    print(f"  Found {len(py_calls)} CALLS relations")

    # Step 3: Compare
    print("[3/3] Comparing and generating report...")
    results = compare_calls(ground_truth, py_calls)

    # Print summary to stdout
    print("\n" + "=" * 50)
    print(f"True Positives:  {results['true_positives']}")
    print(f"False Positives: {len(results['false_positives'])}")
    print(f"False Negatives: {len(results['false_negatives'])}")
    print(f"Precision:       {results['precision']:.2%}")
    print(f"Recall:          {results['recall']:.2%}")
    print(f"F1 Score:        {results['f1']:.2%}")
    print("=" * 50)

    # Generate report files
    generate_report(results, output_dir)


if __name__ == "__main__":
    main()
