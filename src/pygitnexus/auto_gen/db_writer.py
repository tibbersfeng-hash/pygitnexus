"""Write test scripts and operations to KuzuDB."""

from __future__ import annotations

import json

from ..core.operation_extractor import PageOperationSet
from ..graph.store import GraphStore
from .test_spec import spec_from_operation, spec_from_set

# Schema for test-related tables — created on-demand if not present
_TEST_SCHEMA = [
    """CREATE NODE TABLE TestScript (
        id STRING,
        name STRING,
        pageFile STRING,
        pageRoute STRING,
        totalOps INT64,
        totalFields INT64,
        totalApis INT64,
        specJSON STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE TestOperation (
        id STRING,
        opId STRING,
        opType STRING,
        handler STRING,
        element STRING,
        event STRING,
        specJSON STRING,
        PRIMARY KEY (id)
    )""",
]


def _ensure_test_schema(store: GraphStore) -> None:
    """Create test-related tables if they don't already exist."""
    # Check if TestScript table exists
    try:
        store.query("MATCH (n:TestScript) RETURN count(*) LIMIT 1")
        return  # Table already exists
    except Exception:
        pass

    # Create test tables
    for q in _TEST_SCHEMA:
        try:
            store._execute(q)
        except Exception:
            # Table might have been created by another process — ignore errors
            pass

    # Add test relation types to CodeRelation table
    # CodeRelation is a single table that accepts any type STRING,
    # so no schema migration needed for new relation types.


def write_to_db(
    op_sets: list[PageOperationSet],
    db_path: str,
    verbose: bool = False,
) -> int:
    """写入测试脚本到 KuzuDB。

    For each PageOperationSet:
    1. Create TestScript node
    2. Create TestOperation nodes
    3. Create GENERATES relations (TestScript → TestOperation)
    4. Create TESTS relations (TestScript → File)
    5. Create TARGETS relations (TestOperation → Method)
    6. Create EXPECTS relations (TestOperation → API)

    Returns number of TestScript nodes created.
    """
    store = GraphStore(db_path)
    _ensure_test_schema(store)
    total_scripts = 0

    for op_set in op_sets:
        if not op_set.operations:
            continue

        script_id = _script_id(op_set)
        spec_data = spec_from_set(op_set)
        spec_json = json.dumps(spec_data, ensure_ascii=False)

        # 1. Create TestScript node
        store.insert_node("TestScript", {
            "id": script_id,
            "name": _script_name(op_set),
            "pageFile": op_set.page_file,
            "pageRoute": op_set.page_route,
            "totalOps": len(op_set.operations),
            "totalFields": len(op_set.all_fields),
            "totalApis": len(op_set.all_api_endpoints),
            "specJSON": spec_json,
        })
        total_scripts += 1

        # 2. Create TestOperation nodes + GENERATES relations
        for op in op_set.operations:
            op_id = f"{script_id}__{op.op_id}"
            op_spec = spec_from_operation(op, op_set)
            op_json = op_spec.to_json()

            store.insert_node("TestOperation", {
                "id": op_id,
                "opId": op.op_id,
                "opType": op.op_type,
                "handler": op.handler,
                "element": op.element,
                "event": op.event,
                "specJSON": op_json,
            })

            # 3. GENERATES: TestScript → TestOperation
            store.insert_relation(
                "TestScript", "TestOperation",
                script_id, op_id, "GENERATES",
                confidence=1.0,
                reason="test script generates operation",
            )

        # 4. TESTS: TestScript → File
        # Find the File node matching the page file path
        file_id = _file_id_from_path(op_set.page_file)
        if file_id:
            store.insert_relation(
                "TestScript", "File",
                script_id, file_id, "TESTS",
                confidence=1.0,
                reason="test script tests this file",
            )

        # 5. TARGETS: TestOperation → Method
        for op in op_set.operations:
            if op.handler:
                method_id = _method_id_from_handler(op.handler, op_set.page_file)
                if method_id:
                    op_id = f"{script_id}__{op.op_id}"
                    store.insert_relation(
                        "TestOperation", "Method",
                        op_id, method_id, "TARGETS",
                        confidence=0.8,
                        reason="operation targets method by name",
                    )

        # 6. EXPECTS: TestOperation → API
        for op in op_set.operations:
            for api_call in op.api_calls:
                api_path = api_call.get("path", "")
                api_method = api_call.get("method", "")
                if api_path and not api_path.startswith("service:"):
                    # Direct API path — find matching API node
                    api_id = _api_id(api_method, api_path)
                    if api_id:
                        op_id = f"{script_id}__{op.op_id}"
                        store.insert_relation(
                            "TestOperation", "API",
                            op_id, api_id, "EXPECTS",
                            confidence=0.8,
                            reason="operation expects this API endpoint",
                        )

    store.close()
    return total_scripts


# ── ID helpers ──

def _script_id(op_set: PageOperationSet) -> str:
    """Generate a unique ID for a TestScript."""
    # Use page file path as basis for stable ID
    name = op_set.page_file.replace("/", "_").replace(".", "_").replace("\\", "_")
    return f"testscript_{name}"


def _script_name(op_set: PageOperationSet) -> str:
    """Generate a readable test script name."""
    page_name = op_set.page_name
    return f"{page_name.lower()}_test"


def _file_id_from_path(file_path: str) -> str | None:
    """Derive a File node ID from a file path.

    File IDs in PyGitNexus are typically: file_<path_with_underscores>
    """
    if not file_path:
        return None
    name = file_path.replace("/", "_").replace(".", "_").replace("\\", "_")
    return f"file_{name}"


def _method_id_from_handler(handler: str, file_path: str) -> str | None:
    """Derive a Method node ID from a handler name and file.

    Method IDs: method_<file>_<handler>
    """
    if not handler:
        return None
    file_part = file_path.replace("/", "_").replace(".", "_").replace("\\", "_")
    return f"method_{file_part}_{handler}"


def _api_id(method: str, path: str) -> str | None:
    """Derive an API node ID from method and path.

    API IDs: api_<method>_<path_escaped>
    """
    if not path:
        return None
    path_part = path.replace("/", "_").replace("{", "").replace("}", "").replace(".", "_")
    return f"api_{method}_{path_part}"
