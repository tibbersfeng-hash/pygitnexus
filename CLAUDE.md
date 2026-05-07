# PyGitNexus

多语言代码库知识图谱构建工具 + TestNexus 前端测试知识图谱 —— GitNexus 的高性能 Python 实现。

## 项目位置

- **代码仓库**: `/home/claude/.cc-connect/workspace/pygitnexus/`
- **GitHub**: https://github.com/tibbersfeng-hash/pygitnexus
- **打包**: PyInstaller --onefile 生成单文件可执行程序

## 快速命令

```bash
cd /home/claude/.cc-connect/workspace/pygitnexus

# 构建二进制
uv run pyinstaller pygitnexus.spec

# 本地测试（使用源码）
uv run pygitnexus --help

# 本地测试（使用二进制）
./dist/pygitnexus --help

# 查看 GitHub Actions 构建状态
# Releases: https://github.com/tibbersfeng-hash/pygitnexus/releases
```

## 核心架构

### 分析管线

```
analyze(repo_path)
  ├── [1] scan()              → 文件扫描（支持 .gitignore）
  ├── [2] parse()             → tree-sitter AST 解析（ThreadPoolExecutor 8 workers）
  ├── [3] resolve_calls()     → 跨文件调用解析（4 级策略 + 置信度）
  ├── [4] init_schema()       → KuzuDB schema 创建
  ├── [5] batch_write()       → 节点 + 关系批量写入（UNWIND + COPY FROM CSV）
  └── [6] register_repo()     → 保存到全局注册表
```

### 关键文件

| 文件 | 职责 |
|------|------|
| `src/pygitnexus/core/pipeline.py` | 分析管线编排 |
| `src/pygitnexus/core/extractor.py` | tree-sitter AST 解析 |
| `src/pygitnexus/core/resolver.py` | 跨文件调用解析（4 级策略） |
| `src/pygitnexus/graph/store.py` | KuzuDB 操作封装 |
| `src/pygitnexus/mcp/server.py` | MCP Server（6 个工具） |
| `pygitnexus.spec` | PyInstaller 打包配置 |
| `src/pygitnexus/testnexus/core/pipeline.py` | TestNexus 分析管线 |
| `src/pygitnexus/testnexus/core/llm_provider.py` | LLM provider 自动检测 |
| `src/pygitnexus/testnexus/generators/api_test.py` | 三级穷举测试生成 |
| `src/pygitnexus/testnexus/report/html_report.py` | HTML 报告生成 |

### TestNexus 子命令

所有 TestNexus 命令通过 `pygitnexus test <subcommand>` 访问，共 12 个子命令。
详见 README.md "TestNexus 子命令" 表。

### MCP Server 工具

| 工具 | 说明 |
|------|------|
| `list_repos` | 列出已索引仓库 |
| `query` | 符号搜索 |
| `context` | 符号上下文（callers/callees/imports） |
| `cypher` | 原始 Cypher 查询 |
| `impact` | 影响范围分析（BFS 遍历，按深度分组） |
| `detect_changes` | git 变更影响分析 |

## 开发流程

1. **修改代码** → 直接编辑 `src/pygitnexus/` 下的文件
2. **本地测试** → `uv run pygitnexus <command>` 或使用 `./dist/pygitnexus`
3. **打包** → `uv run pyinstaller pygitnexus.spec`
4. **验证** → 用真实项目测试所有 CLI 命令
5. **提交推送** → `git add/commit/push`
6. **打 tag** → `git tag vX.X.X && git push origin vX.X.X` 触发 GitHub Actions

## KuzuDB 注意事项

- `labels(n)[0]` **返回空字符串**，不能用来获取节点类型
- 正确做法：`labels(n) AS nodeType` → 返回字符串类型的标签名
- `LIMIT` 必须放在 `RETURN` 之后
- KuzuDB 不支持子查询，需要拆分为多步查询

## 跨平台支持（macOS / Linux / Windows）

PyGitNexus 的二进制和 CLI 命令必须同时支持三种操作系统。所有新功能或修改都需考虑三平台兼容性。

### GitHub Actions 构建矩阵

| 平台 | Runner | 架构 | 文件名 |
|------|--------|------|--------|
| Linux x86_64 | ubuntu-22.04 | x86_64 | `pygitnexus-vX.X.X-linux-x86_64` |
| Linux ARM64 | ubuntu-22.04-arm | aarch64 | `pygitnexus-vX.X.X-linux-aarch64` |
| macOS Apple Silicon | macos-14 | arm64 | `pygitnexus-vX.X.X-macos-arm64` |
| Windows | windows-latest | x86_64 | `pygitnexus-vX.X.X-windows-x86_64.exe` |

### 跨平台开发规则

1. **路径处理**: 使用 `pathlib.Path`，不要拼接 `/` 或 `\`；MCP 配置中的 command 路径统一用正斜杠 `/`
2. **二进制名**: 检测 `pygitnexus` 和 `pygitnexus.exe`（Windows）
3. **编辑器目录**:
   - Linux/macOS: `~/.cursor`, `~/.claude.json`, `~/.codebuddy/`, `~/.config/opencode/`
   - Windows: `%USERPROFILE%\.cursor`, `%USERPROFILE%\.claude.json`, `%USERPROFILE%\.codebuddy\`, `%APPDATA%\opencode\`
4. **文件编码**: 始终指定 `encoding="utf-8"`（Windows 默认 GBK）
5. **可执行权限**: Unix 需要 `chmod +x`，Windows 不需要
6. **install 命令**: 自动检测平台，从 GitHub Releases 下载对应二进制，安装到系统 PATH

### 安装方式

仅支持二进制方式：
- `pygitnexus install` — 自动安装到 PATH
- 手动从 [Releases](https://github.com/tibbersfeng-hash/pygitnexus/releases) 下载对应平台二进制

## PyInstaller 注意事项

- 使用 `ThreadPoolExecutor` 而非 `ProcessPoolExecutor`（子进程会重执行 bootloader）
- tree-sitter 的 C 扩展释放 GIL，所以多线程同样并发
- `pygitnexus.spec` 中的 `hiddenimports` 需要包含所有 CLI 命令和 MCP 模块
- 二进制路径通过 `sys.argv[0]` 或 `which pygitnexus` 获取

## 测试数据

- **dashboard-backend**: `/home/claude/codespace/life-death/dashboard-backend/` — 127 Java 文件
- **shenyu**: `/home/claude/codespace/shenyu/` — 3,176 Java 文件（Java 性能基准）
- **newbee-mall-vue3-app**: `/tmp/test-projects/newbee-mall-vue3-app/` — 33 文件，20 Vue，14 页面（默认前端回归测试）

## 回归测试

| 测试文件 | 用途 | 运行命令 |
|---------|------|---------|
| `tests/regression_newbee.py` | 前端 Vue 项目回归测试（6 项） | `uv run python tests/regression_newbee.py` |
| `tests/regression_shenyu.py` | Java 项目回归测试（6 项） | `uv run python tests/regression_shenyu.py` |
| `tests/compare_calls.py` | JS/TS/Vue 调用链对比 | `python tests/compare_calls.py /path/to/project` |

### 回归测试指标（newbee）

| 测试 | 期望值 | 实际 |
|------|--------|------|
| Vue 文件 | >=20 | 20 |
| 页面 | >=10 | 14 |
| 组件 | >=15 | 20 |
| 操作 | >=15 | 18 |
| API 调用 | >=15 | 26 |
| 图谱节点 | >50 | 78 |

## 调用链验证

### 验证脚本

`tests/verify_callchain.py` — 基于 tree-sitter AST 的调用链验证工具。

**原理**: 用 tree-sitter 独立解析每个 Java 文件的方法体，提取所有 `method_invocation` 节点作为地面实况（Ground Truth），与 KuzuDB 中 PyGitNexus 的 CALLS 关系对比。

**用法**:
```bash
cd /home/claude/.cc-connect/workspace/pygitnexus
uv run python tests/verify_callchain.py /path/to/java/project [output_dir]
```

**输出**:
- `verification_summary.txt` — 指标报告（Precision / Recall / F1）
- `false_positives.tsv` — PyGitNexus 报告但 AST 中不存在的调用
- `false_negatives.tsv` — AST 中存在但 PyGitNexus 未找到的调用

**当前指标（newbee-mall, 88 Java 文件）**:

| 指标 | 值 |
|------|-----|
| Precision | 73.73% |
| Recall | 82.71% |
| F1 Score | 77.96% |
| 假阳性数 | 162 |
| 假阴性数 | 115 |

### 验证层级

1. **AST 提取** — tree-sitter 解析方法体，提取 `method_invocation` 节点
2. **方法级对比** — 对比 (caller_method → target_method) 对
3. **JDK 过滤** — 自动过滤 JDK/标准库方法（Servlet、反射、IO 等）
4. **模式分类** — 假阳性按模式分组（getter/setter/cross-file 等）

### 与 GitNexus 交叉对比

`tests/compare_calls.py` — PyGitNexus vs GitNexus 调用链对比工具。
主要用于检测两个工具共有的系统性问题。

**最新对比结果（shenyu, 3176 Java 文件）**:
- PyGitNexus 假阳性率: 0.5% vs GitNexus: 2.0%
- 双方共有: 17,095 条有效调用
- PyGitNexus 覆盖率: 106.4%

## Call Chain Mindmap 规范

### 核心概念

**思维导图 = 以目标方法为中心的完整调用链树**，上游（调用者）和下游（被调用）必须展示在**同一棵树**中，目标方法在**中心**，不是两个独立树。

**数据流向**: 目标方法 = root，upstream = 调用方（左/上），downstream = 被调用方（右/下）。所有节点通过 `children` 连接。

### API: `GET /api/mindmap`

**参数**: `target`（必填，方法名）、`class`（可选，类名）、`repo`（可选）、`group`（可选，自动检测）

**返回格式**（固定结构，不可变）:
```json
{
  "ok": true,
  "data": {
    "root": "ClassName.methodName",
    "upstream": [...],
    "downstream": [...]
  }
}
```

- `root`: 字符串，目标方法的 `ClassName.methodName`
- `upstream`: 数组，每个元素代表一个**上游分支的根节点**（如 Controller、USE_ENDPOINT 页面）
- `downstream`: 数组，每个元素代表一个**下游直接被调用方法**

**节点结构**（upstream/downstream 中每个元素）:
```json
{
  "name": "ClassName.methodName",
  "via": "CALLS",
  "is_api": false,
  "children": [...]
}
```

- `name`: 字符串，节点显示文本
- `via`: 字符串，关系类型描述（"CALLS" / "USES_ENDPOINT" / "EXPOSES" / "MAPS_TO (MyBatis)" / "SQL (MyBatis)"）
- `is_api`: 布尔值，标记 API 节点（HTTP 端点）
- `children`: 数组，递归子节点结构

### 前端渲染（dashboard.html）

**可视化库**: ECharts `tree` 类型，不是 Markmap

**布局策略**（关键，不可改为两个独立树）:
- **两个 ECharts tree series 在同一 canvas 上，共享同一个 root 节点**:
  - `upstream`: `orient: 'RL'`（右→左），root 在右，caller 向左展开，占据 `left: '10%', right: '30%'`
  - `downstream`: `orient: 'LR'`（左→右），root 在左，callee 向右展开，占据 `left: '30%', right: '10%'`
  - 两个 series 的 root 节点 name 完全相同，在画布中间重叠，视觉上呈现为中心辐射
  - 如果只有上游或只有下游，单树占满 `left: '5%', right: '10%'` 或反之
  - 如果都无，只显示 root

**颜色规范**（与图例一致，不可更改）:
- `#6366f1` 目标方法（root）
- `#0ea5e9` 上游调用者
- `#10b981` 下游被调用
- `#a78bfa` SQL / 表
- `#f59e0b` API（HTTP 端点）
- `#d946ef` MAPS_TO（MyBatis）

**交互功能**:
- 点击非 root 节点 → `showSymbolDetail()` 跳转到该方法的详情
- 右键节点 → 复制节点名到剪贴板
- 全屏按钮 → overlay 全屏展示

### 后端构建规则

**上游构建** (`upstream` 数组):
1. **Interface 分支**: 查找 CALLS 指向 Interface 方法的 Controller → 向上找 USES_ENDPOINT 页面
2. **MAPS_TO 分支**: MyBatis Mapper 方法 → 向上追溯 ServiceImpl → Controller → 页面
3. **纯 Java CALLS**: 直接 CALLS 指向 target 的方法
4. **group 模式**: 额外查询前端 repo 的 CALLS 关系，通过模糊路径匹配连接到后端 API

**下游构建** (`downstream` 数组):
1. 查询 target 方法的直接 CALLS（最多 30 个）
2. **2 级展开**: 批量查询所有 callee 的子调用（最多 200 个）
3. **Mapper → Table 推断**: 名称含 "Mapper" 或 "DAO" 的节点自动添加 SQL 子节点

**去重**: 所有节点通过 `name` 去重，避免环路

### 关键约束（禁止违反）

1. **root 必须是一个方法**，不能是 Interface/Class/API
2. **使用两个 tree series 在同一 canvas 上**，upstream (RL) 和 downstream (LR)，root 节点 name 完全相同，在中间重叠
3. **upstream 和 downstream 都从同一个 root 展开**，上游向左，下游向右
4. **API 节点（is_api=true）必须出现在页面和 Controller 之间**：page → API → Controller → target
5. **节点 name 格式必须是 `ClassName.methodName`**（SQL/Table 节点除外）
6. **下游必须支持 2 级展开**，不是只查 1 级
7. **group 模式自动检测**：如果 repo 属于有 frontend 角色的 group，自动查询前端调用
8. **模糊路径匹配**：前端 `/user/login` 应匹配后端 `/login`（去掉 `/user/` 前缀）

### 相关文件

| 文件 | 职责 |
|------|------|
| `src/pygitnexus/web/server.py` | `api_mindmap` 端点 + 树构建逻辑 |
| `src/pygitnexus/web/dashboard.html` | ECharts 渲染 + 交互 |

---

## README 更新规则

每次功能变更后，必须更新 `README.md` 中的对应章节，包括：
- CLI 命令表（如有新命令）
- 项目结构图（如有新模块）
- 依赖表（如有新依赖）
- 新增功能的使用说明和示例
