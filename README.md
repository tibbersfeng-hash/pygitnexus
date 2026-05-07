# PyGitNexus

多语言代码库知识图谱构建工具 —— [GitNexus](https://github.com/abhigyanpatwari/GitNexus) 的高性能 Python 实现。

通过静态分析源码，将项目结构、类、接口、方法、字段、调用关系等构建为 KuzuDB 知识图谱，为 AI Agent 提供深度代码感知能力。

内置 **TestNexus** 前端测试知识图谱模块和 **Web Dashboard** 交互式可视化面板。

**支持语言**: Java、JavaScript、TypeScript、Vue SFC

## 为什么是 PyGitNexus

### Java 对比（shenyu 项目，3,176 文件）

| 能力 | GitNexus (原版) | PyGitNexus |
|------|----------------|-----------|
| 解析引擎 | tree-sitter | tree-sitter-java |
| 图数据库 | KuzuDB | KuzuDB |
| 调用解析 | 名称匹配 | 类型感知 4 级解析策略 |
| 假阳性率 | 2.0% | **0.7%** |
| 有效调用 | 22,074 | **25,946** (+17.5%) |
| 3,000 文件分析 | ~8 min | **~2.2 min** |

### JS/TS/Vue 对比（vue-pure-admin 项目，484 文件）

| 指标 | GitNexus | PyGitNexus v11 | 召回率 |
|------|----------|---------------|--------|
| 全部调用 | 880 | 2,075 | - |
| 严格匹配 | - | 815 | **92.6%** |
| 宽松匹配 | - | 861 | **97.8%** |
| Vue 调用召回 | - | 371/402 | **92.3%** |
| TS 调用召回 | - | 309/335 | **100.0%** (宽松) |
| JS 调用召回 | - | 136/143 | **100.0%** (宽松) |

**关键优势**：
- 基于 receiver 类型的精确调用解析，减少误匹配
- 跨文件 return type 传播，支持链式调用
- 接口派发解析，自动追踪 interface → implementing class
- JDK 静态方法白名单 + 常见 JDK 类黑名单，有效抑制假阳性
- 多线程并行解析 + COPY FROM 批量写入，分析速度提升 **3.7x**
- 预计算 AST 字节范围索引，避免重复 tree-sitter 查询（Parse 性能提升 68%）
- Vue SFC 完整支持：`<script>` 块 + 模板表达式调用 + 组件引用 + ref `.value` 解析

## 安装

### 方式一：一键自动安装（推荐）

```bash
pygitnexus install                  # 自动检测平台，下载最新版本到 PATH
pygitnexus install -v v1.0.0        # 安装指定版本
pygitnexus install -p /usr/local/bin  # 安装到指定目录
```

自动安装命令会：
1. 检测当前操作系统和架构（Mac/Linux/Windows）
2. 从 GitHub Releases 下载对应的预编译二进制文件
3. 安装到系统 PATH 中第一个可写的目录
4. 验证安装是否成功

### 方式二：手动下载预编译二进制文件

从 [Releases](https://github.com/tibbersfeng-hash/pygitnexus/releases) 下载对应平台的单文件可执行程序，无需安装 Python。

| 平台 | 架构 | 文件名 |
|------|------|--------|
| Linux | x86_64 | `pygitnexus-vX.X.X-linux-x86_64` |
| Linux | ARM64 | `pygitnexus-vX.X.X-linux-aarch64` |
| macOS | Apple Silicon | `pygitnexus-vX.X.X-macos-arm64` |
| Windows | x86_64 | `pygitnexus-vX.X.X-windows-x86_64.exe` |

```bash
chmod +x pygitnexus-vX.X.X-linux-x86_64
./pygitnexus-vX.X.X-linux-x86_64 --help
```

## 快速开始

### 1. 索引项目

```bash
cd your-project
pygitnexus analyze
```

### 2. 搜索和查询

```bash
# 搜索符号
pygitnexus query "UserService"

# 查看符号上下文（调用者、被调用者、import）
pygitnexus context "UserService"

# 执行 Cypher 查询
pygitnexus cypher "MATCH (n:Class) RETURN n.name, n.filePath"
```

### 3. 启动 Web Dashboard

```bash
pygitnexus web
```

浏览器访问 `http://localhost:8000`，提供：
- 仓库列表和索引状态概览
- 符号搜索（类、方法、字段）
- 思维导图调用链可视化（上游调用者 / 下游被调用者）
- 前端页面 → 后端 API 调用链追踪
- 架构分层可视化
- 数据库表和字段影响分析
- 项目分组管理

### 4. MCP / Hooks / Skills 集成

```bash
# 一键配置 Cursor / Claude Code / CodeBuddy / OpenCode / Codex（向后兼容）
pygitnexus setup

# ── MCP Server 管理 ──
pygitnexus setup mcp add context7 --command npx -a -y -a @anthropic-ai/context7 -d "Library docs"
pygitnexus setup mcp list
pygitnexus setup mcp remove context7
pygitnexus setup mcp export --editor cursor   # 导出到指定编辑器

# ── Hook 管理 ──
pygitnexus setup hook init                      # 生成示例 Hook 配置
pygitnexus setup hook add SessionStart -c ~/.pygitnexus/hooks/init.py -m startup -t 30
pygitnexus setup hook list                      # 列出所有 Hook
pygitnexus setup hook list PreToolUse           # 列出指定事件的 Hook
pygitnexus setup hook remove PreToolUse -i 0    # 按索引移除 Hook

# ── Skill 管理 ──
pygitnexus setup skill init pdf-editor -d "Handle PDF operations"
pygitnexus setup skill list
pygitnexus setup skill show pdf-editor
pygitnexus setup skill remove pdf-editor
```

| 功能 | 说明 |
|------|------|
| MCP | 模型上下文协议 Server 配置，支持 add/list/remove/export |
| Hook | 7 种事件钩子（SessionStart/End、Pre/PostToolUse、UserPromptSubmit、Stop、PreCompact） |
| Skill | 模块化能力包，包含 SKILL.md + scripts/references/assets |

### 5. TestNexus 前端测试

```bash
cd your-frontend-project
pygitnexus test analyze
pygitnexus test report --output report.html
pygitnexus test llm-analyze  # LLM 智能分析（模块推断、用户旅程）
```

## Web Dashboard

### 启动

```bash
pygitnexus web [--host 0.0.0.0 --port 8000]
```

### 功能模块

| 模块 | 路径 | 说明 |
|------|------|------|
| 仓库列表 | `/` | 所有已索引仓库概览、统计信息 |
| 符号搜索 | `/` 搜索框 | 按关键字搜索类、方法、字段、文件。支持 Group 跨库搜索，同时搜索后端图谱和前端源码 |
| 思维导图 | 搜索结果详情 | 目标方法的上下游调用链树形可视化 |
| 前端页面 | `/api/frontend-pages` | 前端页面 → 后端 API 调用链 |
| 架构分层 | `/api/architecture` | Controller → Service → Mapper 分层可视化 |
| 数据库表 | `/api/db-tables` | JPA `@Table` / MyBatis Mapper → 数据库表映射 |
| 定时任务 | `/api/scheduled` | `@Scheduled` 定时任务列表 |
| 项目分组 | `/api/groups` | 按功能分组管理仓库 |

### 思维导图调用链

### Symbol Search 跨组搜索

Symbol Search 支持 Group 模式：在搜索框旁的下拉菜单选择一个 Group 后，搜索结果将同时包含：
- **后端结果**：来自 Group 中所有 backend 仓库的知识图谱（Method、Class、Field 等）
- **前端结果**：来自 Group 中 frontend 仓库的 Vue/JS/TS 文件，包括组件名、函数名、API 路径、文件名等

搜索结果通过 `source` 和 `sourceRole` 字段区分来源，前端结果带有青色 badge。

### 思维导图调用链

点击任意方法可查看完整调用链：

- **上游调用者**：Controller → ServiceImpl → Interface → Mapper
- **下游被调用**：Mapper → SQL 表 / Helper / VO
- 支持缩放、拖拽、节点折叠
- 点击节点可跳转到对应符号详情

## CLI 命令参考

### PyGitNexus

| 命令 | 说明 | 示例 |
|------|------|------|
| `analyze [path]` | 索引项目 | `analyze /path/to/project --force` |
| `query <keyword>` | 搜索符号 | `query "UserService" --limit 50` |
| `context <name>` | 查看符号上下文 | `context "createUser"` |
| `cypher "<query>"` | 执行原始 Cypher | `cypher "MATCH (n:Method) RETURN count(n)"` |
| `list` | 列出已索引仓库 | |
| `status` | 当前目录索引状态 | |
| `clean` | 删除索引 | `clean --all --force` |
| `mcp` | 启动 MCP Server | 供 AI 编辑器调用 |
| `setup` | 一键配置 MCP（默认 auto） | `setup mcp add` / `setup hook init` / `setup skill list` |
| `install` | 自动安装二进制到 PATH | `install` / `install -v v1.0.0` / `install -f` |
| `setup auto` | 自动检测并配置 MCP | 向后兼容，同旧版 `setup` |
| `setup mcp` | MCP Server 管理 | add / list / remove / export |
| `setup hook` | Hook 事件钩子管理 | add / list / remove / init |
| `setup skill` | Skill 模块化能力包管理 | init / list / remove / show |
| `web` | 启动 Web Dashboard | `web --host 0.0.0.0 --port 8000` |
| `group` | 项目分组管理 | `group create <name> --repos r1 r2` |

### TestNexus 子命令

| 命令 | 说明 |
|------|------|
| `test analyze` | 分析前端项目，构建测试知识图谱 |
| `test report` | 生成 HTML 测试报告 |
| `test list` | 列出已分析项目 |
| `test status` | 当前目录分析状态 |
| `test clean` | 删除分析索引 |
| `test llm-analyze` | LLM 智能分析（模块推断、用户旅程） |
| `test impact` | 前端变更影响分析 |
| `test modules` | 查看业务模块推断结果 |

## MCP Server

内置 MCP Server，让 AI 编辑器直接访问知识图谱：

| 工具 | 参数 | 说明 |
|------|------|------|
| `list_repos` | 无 | 列出所有已索引仓库 |
| `query` | `query`, `limit?`, `repo?` | 搜索符号 |
| `context` | `name`, `repo?` | 查看符号的调用者、被调用者、import |
| `cypher` | `query`, `repo?` | 执行原始 Cypher 查询 |
| `impact` | `target`, `direction?`, `maxDepth?` | 影响范围分析（Blast Radius） |
| `detect_changes` | `scope?`, `base_ref?` | git 未提交变更影响分析 |

### impact 示例

```
impact(target="UserService", direction="upstream", maxDepth=3)
```

返回风险评估（LOW / MEDIUM / HIGH / CRITICAL）和按深度分组的影响链。

### detect_changes 示例

```
detect_changes(scope="unstaged")
detect_changes(scope="compare", base_ref="main")
```

返回变更符号、受影响的执行流、风险等级。

### 支持编辑器

| 编辑器 | 检测路径 |
|--------|---------|
| Cursor | `~/.cursor/mcp.json` |
| Claude Code | `~/.claude.json` |
| OpenCode | `~/.config/opencode/opencode.json` |
| Codex | `~/.codex/config.toml` |

## 调用解析策略

### Java（4 级策略 + 置信度）

| 策略 | 场景 | 置信度 |
|------|------|--------|
| S0 | 构造函数调用 | 0.85-0.9 |
| S1 | 已知 receiver 类型（局部变量类型推断） | 0.5-0.9 |
| S1.5 | 跨文件 return type 传播 | 0.6-0.8 |
| S2 | 静态调用（receiver 是类名） | 0.5-0.9 |
| S3 | 无 receiver（同类调用 / 静态导入） | 0.3-0.7 |

**JDK 假阳性抑制**：
- JDK 静态方法白名单（`Files.list`, `Optional.get` 等）
- JDK 类黑名单（`Logger`, `Map`, `List`, `Optional` 等）
- 测试框架白名单（JUnit / Mockito / Hamcrest）

### JS/TS/Vue（6 Case 策略）

| Case | 场景 |
|------|------|
| 1 | import 映射：跨文件导入解析 |
| 2 | import 别名：`as` 别名解析 |
| 3 | 同文件类引用（`new X()` / 类作为参数） |
| 4 | 变量链解析：追踪变量赋值和返回类型 |
| 5 | 模块级调用：包级函数/箭头函数 |
| 6 | Vue ref `.value` 解析 |

**Vue SFC 特殊处理**：`<script>` 块解析 + 模板表达式调用 + 组件引用 + ref 模式 + Pinia store

## 知识图谱 Schema

### 节点表

| 表名 | 说明 |
|------|------|
| `File` / `Folder` | 源文件和目录 |
| `Class` / `Interface` / `TypeAlias` / `Enum` | 类型定义 |
| `Method` / `Constructor` | 方法和构造器 |
| `Field` / `Variable` | 字段和局部变量 |
| `Annotation` / `Export` | 注解和导出声明 |

### 关系表（统一 `CodeRelation`）

| 关系 | 来源 → 目标 |
|------|------------|
| `CONTAINS` | Folder → Folder/File |
| `DEFINES` | File → Class/Method/Field/... |
| `CALLS` | Method → Method/Class |
| `IMPORTS` | File → Class/Interface |
| `EXTENDS` / `IMPLEMENTS` | Class → Class/Interface |
| `HAS_METHOD` / `HAS_PROPERTY` | Class → Method/Field |
| `HAS_ANNOTATION` | Class/Method/Field → Annotation |
| `ACCESSES` | Method → Field |
| `EXPORTS` | File → Method/Class |

### TestNexus 节点表

| 表名 | 说明 |
|------|------|
| `Page` / `Component` / `Action` | 前端页面、组件、操作 |
| `APIOperation` / `Store` / `Route` | API 调用、状态管理、路由 |
| `Module` / `Workflow` | LLM 推断的业务模块和用户旅程 |

## 分析管线

```
analyze(repo_path)
  ├── [1] scanner.scan()           → 文件扫描（支持 .gitignore）
  ├── [2] extractor.parse()        → tree-sitter AST 解析（多线程并行）
  ├── [3] resolver.resolve_calls() → 跨文件调用解析（4级策略 / 6 Case）
  ├── [4] GraphStore.init_schema() → KuzuDB schema 创建
  ├── [5] GraphStore.batch_write() → UNWIND + COPY FROM CSV 批量写入
  └── [6] register_repo()          → 保存到全局注册表
```

## 性能

### Java（shenyu，3,176 文件）

| 阶段 | GitNexus | PyGitNexus | 改善 |
|------|----------|-----------|------|
| Parse | ~161s | **52s** | -68% |
| Resolve | ~183s | **18s** | -90% |
| Graph | ~150s | **63s** | -58% |
| **总计** | **~496s** | **~133s** | **-73%** |

### JS/TS/Vue（vue-pure-admin，484 文件）

| 版本 | 严格召回率 | 宽松召回率 |
|------|-----------|-----------|
| v0 | 30.6% | - |
| v8 | 89.5% | 91.8% |
| **v11** | **92.6%** | **97.8%** |

## 存储布局

```
<repo>/.pygitnexus/
├── kuzu/       # KuzuDB 数据库文件
└── meta.json   # 索引元数据

~/.pygitnexus/
└── registry.json  # 全局仓库注册表
```

## 项目结构

```
src/pygitnexus/
├── cli/                    # CLI 命令入口
├── core/                   # 核心分析引擎
│   ├── scanner.py          # 文件扫描
│   ├── extractor*.py       # Java/JS/TS/Vue AST 解析
│   ├── resolver*.py        # 调用解析（多策略）
│   └── pipeline.py         # 管线编排
├── graph/                  # 图数据库层
│   ├── schema.py           # KuzuDB schema
│   └── store.py            # 批量写入操作
├── mcp/                    # MCP Server
├── search/                 # 查询接口
├── storage/                # 存储管理
├── web/                    # Web Dashboard
│   ├── server.py           # Starlette 后端
│   └── dashboard.html      # 前端 SPA
└── testnexus/              # TestNexus 前端测试模块
    ├── cli/                # TestNexus CLI
    ├── core/               # 核心引擎
    ├── scanners/           # Vue/React 扫描器
    ├── generators/         # 测试用例生成
    ├── graph/              # TestNexus 图数据库
    └── report/             # HTML 报告生成
```

## 版本历史

| 版本 | 主要特性 |
|------|---------|
| **v3** | ECharts Tree 思维导图、Callers 层级修正、bootcdn 源、Web Dashboard 全面优化 |
| v2 | Java 4级调用解析策略、JDK 假阳性抑制、接口派发解析 |
| v1 | JS/TS/Vue 支持、Vue SFC 完整解析、ref .value 模式 |
| v0 | 基础 Java 分析、KuzuDB 图谱、MCP Server |
