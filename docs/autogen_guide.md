# 自动化测试生成与入库使用指南

## 概述

`pygitnexus autogen` 命令通过分析 Vue/HTML 源码，自动提取页面交互操作集（按钮、表单字段、事件处理、API 调用），生成可执行的 Playwright Python 测试脚本，并可选择性地将测试脚本和操作数据存入 KuzuDB 图谱数据库。

## 快速开始

### 1. 生成测试脚本（不入库）

```bash
cd /path/to/pygitnexus
uv run pygitnexus autogen /path/to/frontend/project -o tests/auto_generated/ -v
```

| 参数 | 说明 |
|------|------|
| `repo_path` | 前端项目根目录（必填） |
| `-o, --output` | 输出目录，默认 `tests/auto_generated` |
| `-v, --verbose` | 打印详细的操作提取信息 |

### 2. 生成脚本 + 入库

```bash
uv run pygitnexus autogen /path/to/frontend/project \
  -o tests/auto_generated/ \
  --db /path/to/.pygitnexus/kuzu \
  -v
```

| 参数 | 说明 |
|------|------|
| `-d, --db` | KuzuDB 数据库路径，指定后会将测试脚本和操作写入图谱 |

### 3. 运行生成的测试

```bash
uv run playwright install chromium
uv run pytest tests/auto_generated/ -v
```

## 生成的内容

### 每个页面生成一个测试文件

以 `Login.vue` 为例，生成 `login_test.py`：

```python
"""Auto-generated Playwright tests for Login"""
import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/login'

# 表单字段常量（从 v-model 自动提取）
FIELD_USERNAME = "[v-model='state.username']"
FIELD_PASSWORD = "[v-model='state.password']"

@pytest.fixture
def page_setup(page: Page):
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

# 每个操作生成一个测试函数
def test_op_01_onSubmit(page_setup: Page):
    """onSubmit (@submit) → POST service:login, POST service:register"""

    # Step 1: 自动填写表单字段
    page_setup.fill("[v-model='state.username']", "test_user")
    page_setup.fill("[v-model='state.password']", "test_password123")
    page_setup.fill("[v-model='state.verify']", "test_value")

    # Step 2: 提交表单并拦截 API 响应
    resp_0 = page_setup.expect_response("**/login*")
    resp_1 = page_setup.expect_response("**/register*")
    page_setup.click("form/button[type=submit]")
    response = resp_0.value
    expect(response).to_be_ok()
    response = resp_1.value
    expect(response).to_be_ok()

    # Step 3: 验证结果
    page_setup.wait_for_load_state("networkidle")
```

### 脚本特性

| 特性 | 说明 |
|------|------|
| 精确选择器 | `#id` > `[v-model]` > `.class` > `[data-testid]` |
| 表单字段填写 | 根据字段类型自动填充合理测试值 |
| API 拦截验证 | 使用 `expect_response()` 实际拦截并断言 200 |
| URL 断言 | 导航操作自动添加 `to_have_url()` 断言 |
| 多 API 支持 | 多个 API 场景使用唯一变量名 (`resp_0`, `resp_1`) |

### 字段类型与测试值映射

| 字段类型 | 测试值 |
|---------|--------|
| password | `test_password123` |
| email | `test@example.com` |
| tel/phone | `13800138000` |
| url | `https://example.com` |
| number | `42` |
| date | `2026-01-01` |
| textarea | `test text content` |
| text (name 含 "name") | `test_user` |
| text (name 含 "email") | `test@example.com` |
| 其他 | `test_value` |

## 数据库入库

### 存储结构

入库后在 KuzuDB 中新增两种节点表和四种关系：

**节点表**:

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| TestScript | 测试脚本 | name, pageFile, pageRoute, totalOps, totalFields, totalApis, specJSON |
| TestOperation | 测试操作 | opId, opType, handler, element, event, specJSON |

**关系类型**:

| 关系 | 方向 | 说明 |
|------|------|------|
| GENERATES | TestScript → TestOperation | 脚本包含的操作 |
| TESTS | TestScript → File | 脚本测试的源文件 |
| TARGETS | TestOperation → Method | 操作目标方法 |
| EXPECTS | TestOperation → API | 操作期望的 API 端点 |

### 查询示例

```bash
# 列出所有已入库的测试脚本
uv run pygitnexus cypher "MATCH (ts:TestScript) RETURN ts.name, ts.totalOps, ts.totalApis"

# 查询某个测试脚本的所有操作
uv run pygitnexus cypher "
  MATCH (ts:TestScript)-[r:CodeRelation {type: 'GENERATES'}]->(to:TestOperation)
  WHERE ts.name = 'login_test'
  RETURN to.opId, to.opType, to.handler
"

# 查询测试某个 File 的所有脚本
uv run pygitnexus cypher "
  MATCH (ts:TestScript)-[r:CodeRelation {type: 'TESTS'}]->(f:File)
  WHERE f.name CONTAINS 'Login'
  RETURN ts.name
"

# 查询操作期望的 API 端点
uv run pygitnexus cypher "
  MATCH (to:TestOperation)-[r:CodeRelation {type: 'EXPECTS'}]->(api:API)
  WHERE to.handler = 'onSubmit'
  RETURN api.httpMethod, api.httpPath
"

# 查看完整 spec JSON
uv run pygitnexus cypher "
  MATCH (ts:TestScript) WHERE ts.name = 'login_test'
  RETURN ts.specJSON
" --json
```

## TestSpec 规范

每个 `PageOperation` 映射为一个 `TestOpSpec`，可序列化为 JSON 入库。

```python
from pygitnexus.auto_gen.test_spec import (
    TestOpSpec,
    spec_from_operation,
    spec_from_set,
    spec_to_json,
    json_to_spec,
)

# 从 PageOperation 生成单个操作规范
spec = spec_from_operation(operation, op_set)
print(spec.to_json())  # 序列化为 JSON

# 从整个 PageOperationSet 生成完整规范
spec_data = spec_from_set(op_set)
# {
#   "page_name": "Login",
#   "page_file": "/path/to/Login.vue",
#   "page_route": "/login",
#   "total_ops": 2,
#   "total_fields": 6,
#   "total_apis": 2,
#   "operations": [
#     {
#       "op_id": "op_01_onSubmit",
#       "op_type": "submit",
#       "handler": "onSubmit",
#       "element": "form/button[type=submit]",
#       "event": "@submit",
#       "form_data": [{"selector": "...", "type": "text", "test_value": "..."}],
#       "api_expectations": [{"method": "POST", "path": "/api/login"}],
#       "navigation_expect": null,
#       "steps": [
#         {"action": "navigate", "url": "{BASE_URL}/login"},
#         {"action": "fill", "selector": "...", "value": "test_user"},
#         {"action": "click", "selector": "..."},
#         {"action": "assert_response", "method": "POST", "path": "/api/login"}
#       ]
#     }
#   ]
# }

# 从 JSON 反序列化
restored = json_to_spec(json_string)
```

### Step 动作类型

| action | 参数 | 说明 |
|--------|------|------|
| navigate | url | 页面初始化导航 |
| fill | selector, value | 填写表单字段 |
| click | selector | 点击元素 |
| wait | type | 等待网络空闲 |
| assert_response | method, path | 断言 API 响应 |
| assert_url | pattern | 断言 URL 匹配 |
| assert_visible | selector | 断言元素可见 |

## 完整工作流

```
1. 分析前端项目生成图谱
   uv run pygitnexus analyze /path/to/frontend

2. 生成测试脚本 + 入库
   uv run pygitnexus autogen /path/to/frontend \
     -o tests/auto_generated/ \
     --db /path/to/.pygitnexus/kuzu \
     -v

3. 查询入库结果
   uv run pygitnexus cypher "MATCH (ts:TestScript) RETURN ts.name, ts.totalOps"

4. 运行生成的测试
   uv run playwright install chromium
   uv run pytest tests/auto_generated/ -v
```

## 相关文件

| 文件 | 职责 |
|------|------|
| `src/pygitnexus/cli/autogen.py` | CLI 入口命令 |
| `src/pygitnexus/auto_gen/playwright_generator.py` | Playwright 脚本生成器 |
| `src/pygitnexus/auto_gen/test_spec.py` | 测试规范定义 + 序列化 |
| `src/pygitnexus/auto_gen/db_writer.py` | KuzuDB 入库逻辑 |
| `src/pygitnexus/graph/schema.py` | KuzuDB Schema 定义 |
| `src/pygitnexus/core/operation_extractor.py` | 页面操作集提取器 |
