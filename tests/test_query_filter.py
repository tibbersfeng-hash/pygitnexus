"""Tests for MCP query tool — 3-layer accessor detection.

Accessor classification uses three signals:
1. Naming: matches ^(get|set|is)(?!Or|And)[A-Z]...
2. Structure: no outgoing CALLS edges in the knowledge graph
3. Parameters: parameterCount <= 1

A method is classified as accessor only if ALL three pass.
"""

from __future__ import annotations

import re

# Replicate the classification logic from mcp/server.py for testing
_ACCESSOR_RE = re.compile(r"^(get|set|is)(?!Or|And)[A-Z][a-zA-Z0-9]*$")


def _classify_methods(method_rows, methods_with_calls, method_param_counts):
    """Classification logic extracted for testing."""
    accessors = []
    other_methods = []
    for row in method_rows:
        sym_id = row.get("id", "")
        name = row.get("name", "")

        # Rule 1: has outgoing CALLS → not a pure accessor
        if sym_id in methods_with_calls:
            other_methods.append(row)
            continue

        # Rule 2: naming doesn't match → not an accessor
        if not _ACCESSOR_RE.match(name):
            other_methods.append(row)
            continue

        # Rule 3: parameterCount > 1 → not a simple getter/setter
        param_count = method_param_counts.get(sym_id, -1)
        if param_count > 1:
            other_methods.append(row)
            continue

        accessors.append(row)
    return accessors, other_methods


def _method(id, name, param_count=0):
    return {"id": id, "name": name, "types": {"Method": "Method"}, "parameterCount": param_count}


class TestThreeLayerClassification:
    """Test the 3-layer accessor classification logic."""

    def test_pure_getter_is_accessor(self):
        """getName() — no calls, 0 params, matches naming."""
        methods = [_method("m1", "getName", 0)]
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={"m1": 0})
        assert len(acc) == 1
        assert acc[0]["name"] == "getName"
        assert len(other) == 0

    def test_pure_setter_is_accessor(self):
        """setName(v) — no calls, 1 param, matches naming."""
        methods = [_method("m1", "setName", 1)]
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={"m1": 1})
        assert len(acc) == 1
        assert acc[0]["name"] == "setName"

    def test_boolean_getter_is_accessor(self):
        """isActive() — no calls, 0 params."""
        methods = [_method("m1", "isActive", 0)]
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={"m1": 0})
        assert len(acc) == 1

    def test_caller_getter_not_accessor(self):
        """getUserName() that calls computeName() — has CALLS."""
        methods = [_method("m1", "getUserName", 0)]
        acc, other = _classify_methods(
            methods, methods_with_calls={"m1"}, method_param_counts={"m1": 0}
        )
        assert len(acc) == 0
        assert len(other) == 1
        assert other[0]["name"] == "getUserName"

    def test_multi_param_getter_not_accessor(self):
        """getFoo(a, b) — 2 params, not a simple getter."""
        methods = [_method("m1", "getFoo", 2)]
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={"m1": 2})
        assert len(acc) == 0
        assert len(other) == 1

    def test_compound_name_not_accessor(self):
        """getOrCreate() — naming excludes 'Or'."""
        methods = [_method("m1", "getOrCreate", 2)]
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={"m1": 2})
        assert len(acc) == 0
        assert len(other) == 1

    def test_leaf_method_not_accessor(self):
        """calculate() — no calls but naming doesn't match."""
        methods = [_method("m1", "calculate", 0)]
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={"m1": 0})
        assert len(acc) == 0
        assert len(other) == 1

    def test_getuserbyid_with_calls(self):
        """getUserById() that calls DB — has CALLS, not accessor."""
        methods = [_method("m1", "getUserById", 1)]
        acc, other = _classify_methods(
            methods, methods_with_calls={"m1"}, method_param_counts={"m1": 1}
        )
        assert len(acc) == 0
        assert len(other) == 1

    def test_getuserbyid_no_calls(self):
        """getUserById() with no calls — naming matches, no calls, 1 param → accessor."""
        methods = [_method("m1", "getUserById", 1)]
        acc, other = _classify_methods(
            methods, methods_with_calls=set(), method_param_counts={"m1": 1}
        )
        # This is a borderline case: name matches, no calls, 1 param
        # Classified as accessor because all 3 signals pass
        assert len(acc) == 1

    def test_mixed_results(self):
        """Mix of accessors, business methods, and classes."""
        methods = [
            _method("m1", "validateUser", 1),
            _method("m2", "getUserName", 0),
            _method("m3", "setUserName", 1),
            _method("m4", "createUser", 2),
            _method("m5", "isActive", 0),
        ]
        # m2 has CALLS (lazy getter), m5 has no calls, m1/m3/m4 have no calls
        acc, other = _classify_methods(
            methods,
            methods_with_calls={"m2"},
            method_param_counts={"m1": 1, "m2": 0, "m3": 1, "m4": 2, "m5": 0},
        )
        accessor_names = [a["name"] for a in acc]
        other_names = [o["name"] for o in other]

        assert "validateUser" in other_names   # naming doesn't match
        assert "getUserName" in other_names    # has CALLS
        assert "createUser" in other_names     # naming doesn't match + 2 params
        assert "setUserName" in accessor_names # no calls, 1 param, matches
        assert "isActive" in accessor_names    # no calls, 0 param, matches

    def test_missing_param_count_defaults_to_accessible(self):
        """If parameterCount is missing from DB, defaults to -1 (≤ 1)."""
        methods = [_method("m1", "getName", 0)]
        # paramCount not in map → -1
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={})
        assert len(acc) == 1

    def test_no_id_method(self):
        """Method without id → cannot be looked up, naming-only fallback."""
        methods = [{"name": "getName", "types": {"Method": "Method"}, "id": ""}]
        acc, other = _classify_methods(methods, methods_with_calls=set(), method_param_counts={})
        # id is empty, not in methods_with_calls, naming matches, param_count = -1
        assert len(acc) == 1
