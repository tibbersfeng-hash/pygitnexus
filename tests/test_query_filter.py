"""Tests for MCP query tool — getter/setter summarization.

The query function no longer filters results at the data layer.
Instead, accessor methods (getXxx, setXxx, isXxx) are summarized
in a compact line at the bottom of the output, while other symbols
are shown with full details.
"""

from __future__ import annotations

import re


def _format_query_results(results: list[dict], keyword: str) -> str:
    """Copy of the formatting logic from mcp/server.py query tool."""
    _ACCESSOR_RE = re.compile(
        r"^(get|set|is)(?!Or|And)[A-Z][a-zA-Z0-9]*$"
    )

    def _is_accessor(name: str) -> bool:
        return bool(_ACCESSOR_RE.match(name))

    accessors = []
    others = []
    for row in results:
        types_raw = row.get("types", "")
        is_method = isinstance(types_raw, dict) and "Method" in types_raw
        if is_method and _is_accessor(row.get("name", "")):
            accessors.append(row)
        else:
            others.append(row)

    lines = [f"Found {len(results)} symbol(s) matching '{keyword}':\n"]

    for row in others:
        types = row.get("types", "unknown")
        name = row.get("name", "")
        path = row.get("filePath", "")
        line_num = row.get("startLine", "")
        line_str = f":{line_num}" if line_num else ""
        lines.append(f"  [{types}] {name}")
        lines.append(f"    {path}{line_str}")

    if accessors:
        accessor_names = [r.get("name", "") for r in accessors]
        lines.append(f"\n  Accessor methods ({len(accessors)}): {', '.join(accessor_names)}")

    return "\n".join(lines)


class TestMcpQueryAccessorSummary:
    """Test that MCP query output summarizes accessor methods."""

    def _row(self, name: str, type_label: str = "Method", **kw):
        return {
            "types": {type_label: type_label},
            "name": name,
            "filePath": "src/Test.java",
            "startLine": 10,
            "className": "Test",
            **kw,
        }

    def test_separates_accessors(self):
        """Accessors should be summarized, others shown with full details."""
        results = [
            self._row("validateUser"),
            self._row("getUserName"),
            self._row("setUserName"),
            self._row("isActive"),
            self._row("UserService", type_label="Class", className=""),
        ]

        out = _format_query_results(results, "User")

        # Non-accessors get full entries
        assert "validateUser" in out
        assert "src/Test.java:10" in out
        assert "UserService" in out

        # Accessors get compact summary
        assert "Accessor methods (3)" in out
        assert "getUserName" in out
        assert "setUserName" in out
        assert "isActive" in out

    def test_no_accessors_no_summary(self):
        """When no accessors present, output should not mention them."""
        results = [
            self._row("validateUser"),
            self._row("UserService", type_label="Class", className=""),
        ]
        out = _format_query_results(results, "User")
        assert "Accessor methods" not in out

    def test_all_accessors(self):
        """When all results are accessors, still show the summary."""
        results = [
            self._row("getName"),
            self._row("setName"),
        ]
        out = _format_query_results(results, "Name")
        assert "Accessor methods (2)" in out
        assert "getName" in out
        assert "setName" in out

    def test_tool_methods_not_summarized(self):
        """getOrCreate, getOrDefault etc. should be full entries."""
        results = [
            self._row("getOrCreate"),
            self._row("getOrDefault"),
        ]
        out = _format_query_results(results, "get")
        assert "getOrCreate" in out
        assert "getOrDefault" in out
        assert "Accessor methods" not in out

    def test_output_order(self):
        """Non-accessors should appear before accessor summary."""
        results = [
            self._row("login"),
            self._row("getToken"),
            self._row("setToken"),
        ]
        out = _format_query_results(results, "Token")
        login_pos = out.index("login")
        accessor_pos = out.index("Accessor methods")
        assert login_pos < accessor_pos, "Non-accessors should appear before accessor summary"
