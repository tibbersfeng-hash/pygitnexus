"""Test operation specification — serializable JSON schema for Playwright tests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.operation_extractor import PageField, PageOperation, PageOperationSet


@dataclass
class TestOpSpec:
    """测试操作规范 — 可序列化入库。

    Each PageOperation maps to one TestOpSpec with explicit steps,
    form data, API expectations, and navigation assertions.
    """

    op_id: str = ""              # "op_01_onSubmit"
    op_type: str = "click"       # click/submit/input/navigation/change
    handler: str = ""            # 处理函数名
    element: str = ""            # CSS 选择器
    event: str = ""              # 触发事件
    form_data: list[dict] = field(default_factory=list)
    api_expectations: list[dict] = field(default_factory=list)
    navigation_expect: str | None = None
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TestOpSpec":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_json(cls, text: str) -> "TestOpSpec":
        return cls.from_dict(json.loads(text))


def spec_from_operation(op: PageOperation, op_set: PageOperationSet) -> TestOpSpec:
    """从 PageOperation + PageOperationSet 生成 TestOpSpec。"""
    # Build form_data from operation's fields + global fields
    form_data: list[dict] = []
    for f in op.fields or []:
        form_data.append({
            "selector": f.selector,
            "type": f.field_type,
            "test_value": _test_value_for_type(f.field_type, f.field_name),
        })

    # Build API expectations
    api_expectations: list[dict] = []
    for api in op.api_calls:
        api_expectations.append({"method": api["method"], "path": api["path"]})

    # Build steps
    steps: list[dict] = []

    # Step 1: Navigate (page fixture handles this, but record it)
    steps.append({"action": "navigate", "url": f"{{BASE_URL}}{op_set.page_route}"})

    # Step 2: Fill form fields (if any)
    for fd in form_data:
        steps.append({"action": "fill", "selector": fd["selector"], "value": fd["test_value"]})

    # Step 3: Click/submit the element
    steps.append({"action": "click", "selector": op.element})

    # Step 4: API expectations
    for api_exp in api_expectations:
        steps.append({
            "action": "assert_response",
            "method": api_exp["method"],
            "path": api_exp["path"],
        })

    # Step 5: Navigation assertion
    if op.navigation_target:
        steps.append({"action": "assert_url", "pattern": op.navigation_target})
    elif op.op_type == "navigation":
        # For navigation ops, we expect some URL change
        pass

    return TestOpSpec(
        op_id=op.op_id,
        op_type=op.op_type,
        handler=op.handler,
        element=op.element,
        event=op.event,
        form_data=form_data,
        api_expectations=api_expectations,
        navigation_expect=op.navigation_target or None,
        steps=steps,
    )


def spec_from_set(op_set: PageOperationSet) -> dict[str, Any]:
    """从 PageOperationSet 生成完整的脚本规范（包含所有操作）。"""
    specs = [spec_from_operation(op, op_set) for op in op_set.operations]
    return {
        "page_name": op_set.page_name,
        "page_file": op_set.page_file,
        "page_route": op_set.page_route,
        "total_ops": len(op_set.operations),
        "total_fields": len(op_set.all_fields),
        "total_apis": len(op_set.all_api_endpoints),
        "operations": [s.to_dict() for s in specs],
    }


def spec_to_json(spec_data: dict[str, Any]) -> str:
    """序列化规范为 JSON。"""
    return json.dumps(spec_data, ensure_ascii=False, indent=2)


def json_to_spec(text: str) -> dict[str, Any]:
    """从 JSON 反序列化规范。"""
    data = json.loads(text)
    # Restore TestOpSpec objects
    data["operations"] = [
        TestOpSpec.from_dict(op) for op in data.get("operations", [])
    ]
    return data


# ── Helper ──

def _test_value_for_type(field_type: str, field_name: str = "") -> str:
    """根据字段类型生成合理的测试值。"""
    name_lower = (field_name or "").lower()

    if field_type == "password":
        return "test_password123"
    if field_type in ("email",):
        return "test@example.com"
    if field_type in ("tel", "phone"):
        return "13800138000"
    if field_type == "url":
        return "https://example.com"
    if field_type == "number":
        return "42"
    if field_type == "date":
        return "2026-01-01"
    if field_type == "textarea":
        return "test text content"
    if field_type == "checkbox":
        return "true"
    if field_type == "radio":
        return "option"
    if field_type == "select":
        return "option_value"
    if field_type == "file":
        return "test_file.txt"

    # Guess from name
    if "email" in name_lower or "mail" in name_lower:
        return "test@example.com"
    if "phone" in name_lower or "tel" in name_lower or "mobile" in name_lower:
        return "13800138000"
    if "password" in name_lower or "pwd" in name_lower:
        return "test_password123"
    if "name" in name_lower:
        return "test_user"
    if "address" in name_lower:
        return "test address"
    if "amount" in name_lower or "price" in name_lower or "money" in name_lower:
        return "100"

    return "test_value"
