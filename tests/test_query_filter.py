"""Tests for search.query — getter/setter filtering."""

from __future__ import annotations

import pytest

from pygitnexus.search.query import (
    _is_getter_or_setter,
    _filter_getter_setters,
)


class TestIsGetterOrSetter:
    """Test getter/setter detection regex."""

    def test_standard_getter(self):
        assert _is_getter_or_setter("getUserName") is True
        assert _is_getter_or_setter("getId") is True
        assert _is_getter_or_setter("getStatus") is True

    def test_standard_setter(self):
        assert _is_getter_or_setter("setUserName") is True
        assert _is_getter_or_setter("setId") is True
        assert _is_getter_or_setter("setStatus") is True

    def test_boolean_getter(self):
        assert _is_getter_or_setter("isActive") is True
        assert _is_getter_or_setter("isEnabled") is True
        assert _is_getter_or_setter("isValid") is True

    def test_not_getter_setter(self):
        assert _is_getter_or_setter("validateUser") is False
        assert _is_getter_or_setter("login") is False
        assert _is_getter_or_setter("processOrder") is False
        assert _is_getter_or_setter("main") is False
        assert _is_getter_or_setter("UserService") is False

    def test_edge_cases(self):
        # "get" without capital following letter should not match
        assert _is_getter_or_setter("get") is False
        assert _is_getter_or_setter("set") is False
        assert _is_getter_or_setter("is") is False
        # lowercase after get should not match the pattern
        assert _is_getter_or_setter("getuser") is False

    def test_object_methods(self):
        # Common Object overrides that are trivial
        assert _is_getter_or_setter("toString") is True
        assert _is_getter_or_setter("equals") is True
        assert _is_getter_or_setter("hashCode") is True
        assert _is_getter_or_setter("clone") is True


class TestFilterGetterSetters:
    """Test result filtering."""

    def _method_row(self, name: str) -> dict:
        return {
            "types": {"Method": "Method"},
            "name": name,
            "className": "UserService",
            "filePath": "src/UserService.java",
            "startLine": 10,
        }

    def _class_row(self, name: str) -> dict:
        return {
            "types": {"Class": "Class"},
            "name": name,
            "className": "",
            "filePath": "src/UserService.java",
            "startLine": 5,
        }

    def test_filters_method_getters(self):
        rows = [
            self._method_row("getUserName"),
            self._method_row("validateUser"),
            self._method_row("setUserName"),
            self._method_row("login"),
        ]
        result = _filter_getter_setters(rows)
        names = [r["name"] for r in result]
        assert "getUserName" not in names
        assert "setUserName" not in names
        assert "validateUser" in names
        assert "login" in names

    def test_does_not_filter_class_names(self):
        """Classes named 'getSomething' should not be filtered (unlikely but safe)."""
        rows = [self._class_row("GetUserData")]
        result = _filter_getter_setters(rows)
        assert len(result) == 1
        assert result[0]["name"] == "GetUserData"

    def test_string_types(self):
        """types as string should also be filtered."""
        rows = [
            {"types": "Method", "name": "getName", "className": "X", "filePath": "", "startLine": 1},
            {"types": "Method", "name": "doWork", "className": "X", "filePath": "", "startLine": 2},
        ]
        result = _filter_getter_setters(rows)
        assert len(result) == 1
        assert result[0]["name"] == "doWork"

    def test_empty_list(self):
        assert _filter_getter_setters([]) == []

    def test_no_methods(self):
        rows = [self._class_row("UserService")]
        result = _filter_getter_setters(rows)
        assert len(result) == 1
