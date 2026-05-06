#!/usr/bin/env python3
"""
Mindmap API 接口测试 — 验证 /api/mindmap 端点输出正确性

测试 8 个典型业务方法的调用链数据，确保：
1. 上游调用链包含 API 接口（前端项目）或纯 Controller（纯 Java 项目）
2. 上游调用倒序展示（页面 → API → Controller → target）
3. 下游支持 2 级展开
4. Mapper → SQL 表推断正确
5. 根节点名称正确
6. 多上游分支正确（如 paySuccess 两个支付渠道）
"""

import json
import urllib.request
import urllib.parse
import sys
from typing import Any

# 测试配置
BASE_URL = "http://127.0.0.1:8000"
REPO = "/tmp/test-projects/newbee-mall"

# 8 个测试方法及其期望值
TEST_CASES = [
    {
        "name": "getMyShoppingCartItems",
        "class": "NewBeeMallShoppingCartService",
        "impl": "NewBeeMallShoppingCartServiceImpl.getMyShoppingCartItems",
        "biz": "购物车查询",
        "upstream_count": 3,
        "downstream_count": 11,
        "has_api": True,
        "has_html": True,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 3,  # page → API → Controller → target
    },
    {
        "name": "saveOrder",
        "class": "NewBeeMallOrderService",
        "impl": "NewBeeMallOrderServiceImpl.saveOrder",
        "biz": "创建订单",
        "upstream_count": 1,
        "downstream_count": 24,
        "has_api": True,
        "has_html": True,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 3,
        # 验证 2 级展开：NumberUtil.genOrderNo → NumberUtil.genRandomNum
        "has_2level": True,
        "level2_parent": "NumberUtil.genOrderNo",
        "level2_child": "NumberUtil.genRandomNum",
    },
    {
        "name": "updateNewBeeMallGoods",
        "class": "NewBeeMallGoodsService",
        "impl": "NewBeeMallGoodsServiceImpl.updateNewBeeMallGoods",
        "biz": "商品更新",
        "upstream_count": 1,
        "downstream_count": 15,
        "has_api": False,  # 纯 Java 调用，无前端
        "has_html": False,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 2,  # Controller → target
    },
    {
        "name": "saveNewBeeMallCartItem",
        "class": "NewBeeMallShoppingCartService",
        "impl": "NewBeeMallShoppingCartServiceImpl.saveNewBeeMallCartItem",
        "biz": "添加购物车",
        "upstream_count": 1,
        "downstream_count": 9,
        "has_api": True,
        "has_html": True,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 3,
    },
    {
        "name": "paySuccess",
        "class": "NewBeeMallOrderService",
        "impl": "NewBeeMallOrderServiceImpl.paySuccess",
        "biz": "支付成功",
        "upstream_count": 1,  # After tree inversion: Controller is outermost, pages are nested as children
        "downstream_count": 8,
        "has_api": True,
        "has_html": True,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 3,
        # 验证多上游分支：pages 现在是 Controller 的子节点
        "multi_upstream": True,
        "upstream_names": ["alipay.html", "wxpay.html", "handlePayOrder"],
    },
    {
        "name": "getCategoriesForSearch",
        "class": "NewBeeMallCategoryService",
        "impl": "NewBeeMallCategoryServiceImpl.getCategoriesForSearch",
        "biz": "分类查询",
        "upstream_count": 1,
        "downstream_count": 9,
        "has_api": False,  # 纯 Java 调用
        "has_html": False,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 2,
    },
    {
        "name": "searchNewBeeMallGoods",
        "class": "NewBeeMallGoodsService",
        "impl": "NewBeeMallGoodsServiceImpl.searchNewBeeMallGoods",
        "biz": "商品搜索",
        "upstream_count": 1,
        "downstream_count": 9,
        "has_api": False,  # 纯 Java 调用
        "has_html": False,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 2,
        # 验证 2 级展开：BeanUtil.copyList → BeanUtil.copyProperties
        "has_2level": True,
        "level2_parent": "BeanUtil.copyList",
        "level2_child": "BeanUtil.copyProperties",
    },
    {
        "name": "updateUserInfo",
        "class": "NewBeeMallUserService",
        "impl": "NewBeeMallUserServiceImpl.updateUserInfo",
        "biz": "用户信息更新",
        "upstream_count": 1,  # After tree inversion: Controller is outermost, pages are nested
        "downstream_count": 11,
        "has_api": True,
        "has_html": True,
        "has_mapper": True,
        "has_sql": True,
        "min_depth": 3,
        # 验证多上游分支：pages 现在是 Controller 的子节点
        "multi_upstream": True,
        "upstream_names": ["order-settle.html", "personal.html"],
    },
]


class TestResult:
    """单个测试用例的结果"""

    def __init__(self, name: str, biz: str):
        self.name = name
        self.biz = biz
        self.passed = 0
        self.failed = 0
        self.details: list[str] = []

    def ok(self, check: str, detail: str = ""):
        self.passed += 1
        self.details.append(f"  ✓ {check}" + (f" — {detail}" if detail else ""))

    def fail(self, check: str, detail: str = ""):
        self.failed += 1
        self.details.append(f"  ✗ {check}" + (f" — {detail}" if detail else ""))

    def __str__(self) -> str:
        status = "PASS" if self.failed == 0 else "FAIL"
        lines = [f"\n[{status}] {self.name} ({self.biz})"]
        lines.extend(self.details)
        return "\n".join(lines)


def fetch_mindmap(method: str, cls: str) -> dict[str, Any] | None:
    """调用 /api/mindmap 接口"""
    url = f"{BASE_URL}/api/mindmap?target={urllib.parse.quote(method)}&class={urllib.parse.quote(cls)}&repo={urllib.parse.quote(REPO)}"
    try:
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return data["data"]
            print(f"  API error: {data.get('error', 'unknown')}")
            return None
    except Exception as e:
        print(f"  HTTP error: {e}")
        return None


def check_upstream(data: dict, tc: dict, result: TestResult):
    """验证上游调用链"""
    # 1. 上游分支数量
    upstream = data.get("upstream", [])
    if len(upstream) == tc["upstream_count"]:
        result.ok(f"上游分支数量: {len(upstream)}/{tc['upstream_count']}")
    else:
        result.fail(f"上游分支数量: {len(upstream)}/{tc['upstream_count']}")

    # 2. 检查 API 接口存在性
    def has_api_node(items):
        for item in items:
            if item.get("is_api"):
                return True
            if item.get("children"):
                if has_api_node(item["children"]):
                    return True
        return False

    if tc["has_api"]:
        if has_api_node(upstream):
            result.ok("API 接口存在 (前端项目)")
        else:
            result.fail("API 接口缺失 (前端项目应有 API 节点)")

    # 3. 检查 HTML 页面或 Vue 组件函数存在性
    if tc["has_html"]:
        html_pages = [u["name"] for u in upstream if u["name"].endswith(".html")]
        vue_funcs = [u["name"] for u in upstream if not u["name"].endswith(".html")]
        if html_pages or vue_funcs:
            pages_str = ", ".join(html_pages) if html_pages else ""
            vue_str = ", ".join(vue_funcs) if vue_funcs else ""
            sep = ", " if pages_str and vue_str else ""
            result.ok(f"前端页面/组件存在: {pages_str}{sep}{vue_str}".strip())
        else:
            result.fail("前端页面缺失 (前端项目应有 .html 页面或 Vue 组件)")

    # 4. 验证上游调用倒序：页面/组件 → API → Controller → target
    if tc["has_html"] and tc["has_api"]:
        for branch in upstream:
            path = [branch["name"]]

            def trace_path(node, indent=0):
                for child in node.get("children", []):
                    path.append(child["name"])
                    if child.get("children"):
                        trace_path(child, indent + 1)

            trace_path(branch)

            # 验证路径顺序：page/vue_func → API → Controller
            # HTML 页面以 .html 结尾，Vue 组件函数是 camelCase 名称
            has_page = any(p.endswith(".html") for p in path) or any(
                p[0].islower() and not p.startswith(("GET ", "POST ", "PUT ", "DELETE ")) for p in path
            )
            has_api_in_path = any(
                p.startswith(("GET ", "POST ", "PUT ", "DELETE ")) for p in path
            )
            has_controller = any("Controller" in p for p in path)

            if has_page and has_api_in_path and has_controller:
                result.ok(
                    f"上游倒序正确: {' → '.join(path[:3])}"
                )
            else:
                result.fail(
                    f"上游顺序错误: {' → '.join(path[:3])} "
                    f"(page={has_page}, api={has_api_in_path}, controller={has_controller})"
                )

    # 5. 验证纯 Java 调用的上游（无前端）
    if not tc["has_html"] and not tc["has_api"]:
        for branch in upstream:
            if "Controller" in branch["name"]:
                result.ok(f"纯 Java 上游: {branch['name']}")
            else:
                result.fail(f"纯 Java 上游应包含 Controller: {branch['name']}")

    # 6. 验证多上游分支（现在 pages 嵌套在 Controller 子节点中）
    if tc.get("multi_upstream"):
        # 收集所有层级的节点名称
        all_names = []
        def collect_names(items, indent=0):
            for item in items:
                all_names.append(item["name"])
                if item.get("children"):
                    collect_names(item["children"], indent + 1)
        collect_names(upstream)

        expected = tc["upstream_names"]
        if all(e in all_names for e in expected):
            result.ok(f"多上游分支: {', '.join(sorted(set(n for n in all_names if n in expected)))}")
        else:
            result.fail(f"多上游分支缺失: 期望 {expected}, 实际 {all_names}")


def check_downstream(data: dict, tc: dict, result: TestResult):
    """验证下游调用链"""
    downstream = data.get("downstream", [])

    # 1. 下游直接调用数量
    if len(downstream) == tc["downstream_count"]:
        result.ok(f"下游调用数量: {len(downstream)}/{tc['downstream_count']}")
    else:
        result.fail(f"下游调用数量: {len(downstream)}/{tc['downstream_count']}")

    # 2. 检查 Mapper 存在性
    if tc["has_mapper"]:
        mappers = [d["name"] for d in downstream if "Mapper" in d["name"] or "DAO" in d["name"]]
        if mappers:
            result.ok(f"Mapper 存在: {len(mappers)} 个")
        else:
            result.fail("Mapper 缺失")

    # 3. 检查 SQL 表推断
    if tc["has_sql"]:
        sql_tables = []
        for d in downstream:
            for child in d.get("children", []):
                if child.get("via") and "SQL" in child.get("via", ""):
                    sql_tables.append(child["name"])
        if sql_tables:
            result.ok(f"SQL 表推断: {len(sql_tables)} 个")
        else:
            result.fail("SQL 表推断缺失")

    # 4. 验证 2 级展开
    if tc.get("has_2level"):
        parent = tc["level2_parent"]
        child = tc["level2_child"]
        found = False
        for d in downstream:
            if d["name"] == parent:
                children = [c["name"] for c in d.get("children", [])]
                if child in children:
                    found = True
                    break
        if found:
            result.ok(f"2 级展开: {parent} → {child}")
        else:
            result.fail(f"2 级展开缺失: {parent} → {child}")


def check_root(data: dict, tc: dict, result: TestResult):
    """验证根节点"""
    root = data.get("root", "")
    expected = tc["impl"]
    if root == expected:
        result.ok(f"根节点: {root}")
    else:
        result.fail(f"根节点错误: 期望 {expected}, 实际 {root}")


def check_structure(data: dict, result: TestResult):
    """验证 API 返回结构"""
    # 1. 检查 ok 字段
    if "root" in data and "upstream" in data and "downstream" in data:
        result.ok("API 结构正确 (root/upstream/downstream)")
    else:
        result.fail(f"API 结构错误: 缺少字段 {[k for k in ['root', 'upstream', 'downstream'] if k not in data]}")

    # 2. 检查上游节点结构
    for u in data.get("upstream", []):
        if "name" in u:
            result.ok(f"上游节点结构正确: {u['name']}")
            break
        else:
            result.fail("上游节点缺少 name 字段")

    # 3. 检查下游节点结构
    for d in data.get("downstream", []):
        if "name" in d and "via" in d:
            result.ok(f"下游节点结构正确: {d['name']}")
            break
        else:
            result.fail("下游节点缺少 name 或 via 字段")


def run_tests():
    """运行所有测试"""
    print("=" * 60)
    print("Mindmap API 接口测试")
    print(f"Base URL: {BASE_URL}")
    print(f"Repository: {REPO}")
    print("=" * 60)

    total_passed = 0
    total_failed = 0
    results: list[TestResult] = []

    for tc in TEST_CASES:
        result = TestResult(tc["name"], tc["biz"])

        # 调用 API
        data = fetch_mindmap(tc["name"], tc["class"])
        if not data:
            result.fail("API 调用失败", "无法获取数据")
            results.append(result)
            total_failed += 1
            print(result)
            continue

        # 验证
        check_root(data, tc, result)
        check_structure(data, result)
        check_upstream(data, tc, result)
        check_downstream(data, tc, result)

        results.append(result)
        total_passed += result.passed
        total_failed += result.failed
        print(result)

    # 汇总
    print("\n" + "=" * 60)
    print(f"测试汇总: {total_passed} passed, {total_failed} failed, {len(TEST_CASES)} test cases")
    print("=" * 60)

    if total_failed > 0:
        print("\n❌ 部分测试失败，请检查上述 FAIL 项")
        sys.exit(1)
    else:
        print("\n✅ 所有测试通过")
        sys.exit(0)


if __name__ == "__main__":
    run_tests()
