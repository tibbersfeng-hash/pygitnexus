# PyGitNexus

多语言代码库知识图谱构建工具 —— [GitNexus](https://github.com/abhigyanpatwari/GitNexus) 的高性能 Python 实现。

通过静态分析源码，将项目结构、类、接口、方法、字段、调用关系等构建为 KuzuDB 知识图谱，为 AI Agent 提供深度代码感知能力。

内置 **TestNexus** 模块：支持前端（Vue/React）测试知识图谱构建、LLM 业务模块推断、用户旅程自动生成、HTML 报告生成。

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
- 多进程并行解析 + COPY FROM 批量写入，分析速度提升 **3.7x**
- 预计算 AST 字节范围索引，避免重复 tree-sitter 查询（Parse 性能提升 68%）
- Vue SFC 完整支持：`<script>` 块 + 模板表达式调用 + 组件引用 + ref `.value` 解析

## 安装

### 方式一：下载预编译二进制文件（推荐）

从 [Releases](https://github.com/tibbersfeng-hash/pygitnexus/releases) 下载对应平台的单文件可执行程序，无需安装 Python。

| 平台 | 架构 | 文件名 |
|------|------|--------|
| Linux | x86_64 | `pygitnexus-vX.X.X-linux-x86_64` |
| Linux | ARM64 | `pygitnexus-vX.X.X-linux-aarch64` |
| macOS | Apple Silicon | `pygitnexus-vX.X.X-macos-arm64` |
| Windows | x86_64 | `pygitnexus-vX.X.X-windows-x86_64.exe` |

下载后添加执行权限即可使用：

```bash
# Linux / macOS
chmod +x pygitnexus-vX.X.X-linux-x86_64
./pygitnexus-vX.X.X-linux-x86_64 --help

# Windows
pygitnexus-vX.X.X-windows-x86_64.exe --help
```

### 方式二：从源码安装

```bash
# 需要 Python 3.12+
uv sync
```

### 依赖

#### PyGitNexus（核心）

| 包 | 版本 | 用途 |
|---|------|------|
| tree-sitter | 0.23+ | AST 解析引擎 |
| tree-sitter-java | 0.23+ | Java 语法文件 |
| tree-sitter-javascript | 0.25+ | JavaScript 语法文件 |
| tree-sitter-typescript | 0.23+ | TypeScript/TSX 语法文件 |
| kuzu | 0.11+ | 嵌入式图数据库 |
| click | 8.1+ | CLI 框架 |
| mcp | 1.26+ | Model Context Protocol SDK |

#### TestNexus（前端测试，可选依赖）

| 包 | 版本 | 用途 |
|---|------|------|
| pyyaml | 6.0+ | Vue/React 项目配置解析 |
| httpx | 0.27+ | LLM API 调用 |
| anthropic | 0.39+ | Anthropic SDK 模式 LLM（可选） |

可选依赖安装：
```bash
uv sync --all-extras  # 包含 anthropic
```

## 快速开始

### PyGitNexus（后端知识图谱）

```bash
# 索引一个项目（自动识别 Java/JS/TS/Vue）
cd your-project
uv run pygitnexus analyze

# 搜索符号
uv run pygitnexus query "UserService"

# 查看符号的完整上下文（调用者、被调用者、import）
uv run pygitnexus context "UserService"

# 执行原始 Cypher 查询
uv run pygitnexus cypher "MATCH (n:Class) RETURN n.name, n.filePath"

# 查看已索引仓库
uv run pygitnexus list

# 查看当前仓库索引状态
uv run pygitnexus status

# 删除索引
uv run pygitnexus clean
```

### TestNexus（前端测试知识图谱）

```bash
cd your-frontend-project

# 分析前端项目（自动识别 Vue/React）
uv run pygitnexus test analyze

# 生成 HTML 测试报告
uv run pygitnexus test report --output report.html

# 查看已分析项目
uv run pygitnexus test list

# 查看分析状态
uv run pygitnexus test status

# LLM 智能分析（自动识别 Anthropic/OpenAI）
uv run pygitnexus test llm-analyze

# 影响分析
uv run pygitnexus test impact
```

## CLI 命令参考

| 命令 | 说明 | 示例 |
|------|------|------|
| `analyze [path]` | 索引项目，默认当前目录 | `analyze /path/to/project --force` |
| `analyze --force` | 强制全量重建 | |
| `query <keyword>` | 搜索符号 | `query "UserService" --limit 50` |
| `context <name>` | 查看符号的调用者、被调用者、import | `context "createUser"` |
| `cypher "<query>"` | 执行原始 Cypher 查询 | `cypher "MATCH (n:Method) WHERE n.isStatic RETURN count(n)"` |
| `cypher --json` | JSON 格式输出 | |
| `list` | 列出已索引仓库 | |
| `status` | 当前目录索引状态 | |
| `clean` | 删除当前目录索引 | `clean --all --force` 删除所有 |
| `mcp` | 启动 MCP Server (stdio) | 供 AI 编辑器调用 |
| `setup` | 一键配置 AI 编辑器的 MCP | 自动检测 Cursor/Claude Code/OpenCode/Codex |
| `test` | TestNexus 前端测试子命令组 | 见下方 TestNexus 命令 |

### TestNexus 子命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `test analyze` | 分析前端项目，构建测试知识图谱 | `test analyze /path/to/vue-project` |
| `test report` | 生成 HTML 测试报告 | `test report --output report.html` |
| `test list` | 列出已分析的前端项目 | |
| `test status` | 当前目录分析状态 | |
| `test clean` | 删除分析索引 | `test clean --all --force` |
| `test llm-analyze` | LLM 智能分析（模块推断、用户旅程） | 自动识别 Anthropic/OpenAI provider |
| `test impact` | 前端变更影响分析 | |
| `test modules` | 查看业务模块推断结果 | |

## 输出示例

```
$ pygitnexus analyze my-project
Analyzing /path/to/my-project...
  [  5%] Scanning for source files...
  [ 10%] Found 150 files (80 Java, 50 TypeScript, 20 Vue)
  [ 15%] Parsing files (concurrent)...
  [ 55%] Parsed 150/150 files
  [ 60%] Resolving cross-file relations...
  [ 70%] Resolved 1,234 calls in 0.8s
  [ 70%] Building knowledge graph...
  [100%] Analysis complete (parse: 3.2s, resolve: 0.8s, graph: 2.1s)

Analysis complete!
  Files:    150
  Classes:  120
  Methods:  890
  Fields:   345
  Calls:    1,234
  Imports:  210
```

```
$ pygitnexus query "User"
Found 8 symbol(s) matching 'User':

  [Class] com.example.User
    src/main/java/com/example/User.java:3
  [Method] User
    src/main/java/com/example/User.java:7
  [Class] UserStore (TypeScript)
    src/stores/userStore.ts:12
  [File] User.vue
    src/views/User.vue
```

```
$ pygitnexus context "main"
Context for 'main':

Callers (0):
  (none)

Callees (3):
  -> UserService.createDefault (confidence: 0.50)
  -> UserService.validate (confidence: 0.50)
  -> UserService.getName (confidence: 0.50)
```

## MCP Server

PyGitNexus 内置 MCP (Model Context Protocol) Server，让 AI 编辑器（Cursor、Claude Code、OpenCode、Codex）直接访问知识图谱，无需额外安装。

### 快速启用

```bash
# 一键配置（自动检测已安装的编辑器）
pygitnexus setup

# 或手动启动
pygitnexus mcp
```

### MCP 工具列表

| 工具 | 必需参数 | 可选参数 | 说明 |
|------|----------|----------|------|
| `list_repos` | 无 | 无 | 列出所有已索引仓库 |
| `query` | `query` | `limit`, `repo` | 搜索符号（类、方法、文件） |
| `context` | `name` | `repo` | 查看符号的调用者、被调用者、import |
| `cypher` | `query` | `repo` | 执行原始 Cypher 查询 |
| `impact` | `target` | `direction`, `maxDepth`, `relationTypes`, `repo` 等 | 分析修改的影响范围（Blast Radius） |
| `detect_changes` | 无 | `scope`, `base_ref`, `repo` | 分析 git 未提交变更的影响 |

### impact 工具

分析修改某个符号后的连锁影响（影响范围分析）：

```
impact(target="UserService", direction="upstream", maxDepth=3)
```

返回结果包含：
- **风险评估**：LOW / MEDIUM / HIGH / CRITICAL
- **按深度分组**：
  - `d=1`: WILL BREAK（直接调用者）
  - `d=2`: LIKELY AFFECTED（间接影响）
  - `d=3`: MAY NEED TESTING（需要测试的范围）
- 支持 `relationTypes` 过滤（CALLS、IMPORTS、EXTENDS、IMPLEMENTS 等）
- 支持 `direction` 方向（upstream=谁依赖我，downstream=我依赖谁）

### detect_changes 工具

分析 git 未提交变更对执行流的影响：

```
detect_changes(scope="unstaged")        # 默认：未暂存变更
detect_changes(scope="staged")          # 已暂存变更
detect_changes(scope="all")             # 所有未提交变更
detect_changes(scope="compare", base_ref="main")  # 与指定分支对比
```

返回：变更的符号、受影响的执行流程、风险等级。

### 支持编辑器

| 编辑器 | 配置文件 | 安装路径检测 |
|--------|---------|-------------|
| Cursor | `~/.cursor/mcp.json` | `~/.cursor/` 目录存在 |
| Claude Code | `~/.claude.json` | `~/.claude/` 目录存在 |
| OpenCode | `~/.config/opencode/opencode.json` | `~/.config/opencode/` 目录存在 |
| Codex | `~/.codex/config.toml` | `~/.codex/` 目录存在 |

## TestNexus 前端测试

TestNexus 是 PyGitNexus 内置的前端测试知识图谱构建模块，支持 Vue 和 React 项目的静态分析，自动提取页面、API 操作、组件交互关系，并生成穷举测试用例。

### 核心能力

| 能力 | 说明 |
|------|------|
| Vue SFC 扫描 | 解析 `<script>` 块、路由定义、Pinia store、API 调用 |
| React 扫描 | 解析路由配置、Redux store、API 调用、组件引用 |
| 操作集提取 | 自动识别 HTTP 请求、组件交互、事件处理器 |
| LLM 模块推断 | 调用 LLM 智能识别业务模块和用户旅程 |
| 穷举测试生成 | 单页 CRUD → 流程内串联 → 跨流程组合，三级递进 |
| HTML 报告 | 含页面操作集、测试用例、覆盖率统计 |
| 浏览器录制 | Playwright 录制 + 重放真实用户操作 |

### 知识图谱 Schema（TestNexus）

#### 节点表

| 表名 | 字段 | 说明 |
|------|------|------|
| `Page` | id, name, path, filePath, componentName, framework | Vue/React 页面组件 |
| `APIOperation` | id, httpMethod, apiPath, componentName, functionName | HTTP API 调用 |
| `Component` | id, name, filePath, framework, kind | 组件定义 |
| `Action` | id, name, componentName, type | 用户交互操作（click, input 等） |
| `Store` | id, name, filePath, framework | 状态管理（Pinia/Redux） |
| `Route` | id, path, name, componentPath | 路由定义 |
| `Module` | id, name, description | LLM 推断的业务模块 |
| `Workflow` | id, name, description, steps | LLM 推断的用户旅程 |

#### 关系表

| 关系 | 来源 → 目标 | 说明 |
|------|------------|------|
| `CONTAINS_API` | Page → APIOperation | 页面包含的 API 调用 |
| `CONTAINS_ACTION` | Page → Action | 页面包含的交互操作 |
| `USES_STORE` | Page → Store | 页面使用的状态管理 |
| `HAS_ROUTE` | Page → Route | 页面对应的路由 |
| `INFERS_MODULE` | Page → Module | LLM 推断页面所属模块 |
| `BELONGS_FLOW` | Module → Workflow | 模块所属用户旅程 |

### 测试用例生成策略

TestNexus 使用三级穷举策略生成测试用例：

| 级别 | 策略 | 生成数量示例 |
|------|------|-------------|
| Level 1 | 单页 CRUD：每个页面的操作集穷举 | ~4 个（每个页面 ≥2 个 fetch 操作） |
| Level 2 | 流程内串联：同一业务流内的页面链 | ~3 个（如 cart→order→pay） |
| Level 3 | 跨流程组合：多个业务流的组合 | ~4 个（如 auth+cart+order） |

### LLM 智能分析

```bash
# 自动检测 provider（Anthropic SDK / OpenAI 兼容 HTTP）
uv run pygitnexus test llm-analyze

# 支持的环境变量
ANTHROPIC_BASE_URL=<url>   # Anthropic SDK 模式
OPENAI_API_KEY=<key>       # OpenAI 兼容模式
```

Provider 自动检测逻辑：
1. 检查 URL hostname：`api.anthropic.com` → Anthropic SDK
2. 检查 URL 路径：包含 `/anthropic` → Anthropic SDK
3. 其他情况 → OpenAI 兼容 HTTP

### 输出示例

```
$ pygitnexus test analyze my-vue-app
Analyzing my-vue-app (Vue project)...
  [ 10%] Scanning for Vue/TS/JS files...
  [ 30%] Found 56 files (42 Vue, 10 TS, 4 JS)
  [ 60%] Extracting pages, routes, stores...
  [ 80%] Building test knowledge graph...
  [100%] Analysis complete

Analysis complete!
  Pages:         14
  API Operations: 48
  Actions:       23
  Components:    56
  Stores:        4
  Routes:        12
```

```
$ pygitnexus test generate
Generating test cases...
  Level 1: Single-page CRUD — 4 journeys
  Level 2: Intra-flow chains — 3 journeys
  Level 3: Cross-flow combos — 4 journeys
  Total: 11 user journeys written to graph
```

## 调用解析策略

### Java 调用解析（4 级策略）

PyGitNexus 使用 4 级递进策略解析方法调用目标，每级附带置信度评分：

#### Strategy 0: 构造函数调用
当调用目标名称与 receiver 类型名一致时，识别为构造函数调用。
- 精确构造器匹配 → 置信度 0.9
- 类节点回退 → 置信度 0.85

#### Strategy 1: 已知 receiver 类型（来自局部变量类型推断）
- 精确类名.方法名匹配 → 置信度 0.9
- Builder 模式识别 (`build/builder/newBuilder`) → 置信度 0.85
- 接口派发 (interface → implementing class) → 置信度 0.6
- 类名后缀匹配 → 置信度 0.8
- 方法名 + 类名相似度匹配 → 置信度 0.5

#### Strategy 1.5: 跨文件 return type 传播
当 receiver 类型未知但 receiver 名称匹配已知方法时，用该方法的返回类型解析调用。
```java
// service.getUser().getAddress() — getAddress() 的 receiver "getUser" 返回 User
// 因此将 getAddress() 解析到 User 类
```

#### Strategy 2: receiver 是已知类名（静态调用）
- 静态字段类型推断 → 置信度 0.75
- 精确类名匹配 → 置信度 0.9
- 类名后缀匹配 → 置信度 0.8
- 方法名全局匹配（仅大写开头的类名）→ 置信度 0.5

#### Strategy 3: 无 receiver（同类调用 / 静态导入）
- 同类方法匹配 → 置信度 0.7
- 继承链追溯 → 置信度 0.65
- 测试框架静态导入白名单 (JUnit/Mockito/Hamcrest) → 置信度 0.3

#### JDK 假阳性抑制

| 机制 | 说明 |
|------|------|
| JDK 静态方法白名单 | `Files.list`, `Paths.get`, `Optional.get` 等不会匹配项目方法 |
| JDK 类黑名单 | `Logger`, `Map`, `List`, `Optional`, `Stream`, `JsonNode` 等不会参与回退匹配 |
| 常见 JDK 方法过滤 | `get/set/add/isEmpty/toString/equals` 等在回退匹配中被跳过 |
| 测试框架白名单 | 仅 JUnit/Mockito/Hamcrest 的断言方法允许全局名称匹配 |

### JS/TS/Vue 调用解析（6 Case 策略）

JS/TS/Vue 采用基于 import/export 映射 + 变量类型推断的 6 Case 解析策略：

| Case | 场景 | 示例 |
|------|------|------|
| 1 | import 映射：跨文件导入解析 | `import { foo } from './utils'` → `foo()` |
| 2 | import 别名：`as` 别名解析 | `import { foo as bar } from './utils'` → `bar()` |
| 3 | 同文件类引用：`new X()` 和类作为值 | `new ImageCapture()` → `ImageCapture` |
| 3b | 同文件类引用：类名作为函数参数 | `register(PureHttp)` → `PureHttp` |
| 4 | 变量链解析：追踪变量赋值和返回类型 | `const user = getUser(); user.getName()` |
| 5 | 模块级调用：包级函数/箭头函数调用 | `setupStore()` 在模块级别调用 |
| 6 | Vue ref `.value` 解析：`h(component, { ref })` 模式 | `formRef.value.getRef()` → 组件方法 |

#### Vue SFC 特殊处理

- **`<script>` 块解析**：提取组件名、props、setup 函数、computed、watch
- **模板表达式调用**：`@click="handleSubmit"`、`{{ formatName() }}`
- **组件引用**：`<component :is="DynamicComponent">`
- **ref 模式**：`h(Button, { ref: btnRef })` → `btnRef.value.focus()`
- **Pinia store**：`state: () => ({...})` 中箭头函数正确命名

## 知识图谱 Schema

### 节点表

| 表名 | 字段 | 说明 |
|------|------|------|
| `File` | id, name, filePath, content | 源文件（Java/JS/TS/Vue） |
| `Folder` | id, name, filePath | 文件夹 |
| `Class` | id, name, filePath, startLine, endLine, isPublic, isAbstract, isInterface, content | 类/组件 |
| `Interface` | id, name, filePath, startLine, endLine, isPublic, content | 接口 |
| `TypeAlias` | id, name, filePath, startLine, endLine, content | TypeScript 类型别名 |
| `Enum` | id, name, filePath, startLine, endLine, content | TypeScript 枚举 |
| `Method` | id, name, className, filePath, startLine, endLine, returnType, parameterCount, isStatic, isPublic, isConstructor, content | 方法/函数 |
| `Field` | id, name, typeName, className, filePath, startLine, endLine, isStatic, isPublic | 字段/属性 |
| `Constructor` | id, name, className, filePath, startLine, endLine, parameterCount, isPublic, content | 构造器 |
| `Variable` | id, name, typeName, methodName, className, filePath, line, isFinal | 局部变量 |
| `Annotation` | id, name, targetType, targetName, filePath, line, attributes | 注解/装饰器 |
| `Export` | id, name, filePath, kind | 导出声明（JS/TS） |

### 关系表

统一 `CodeRelation` 表，包含以下关系类型：

| 关系 | 来源 → 目标 | 说明 |
|------|------------|------|
| `CONTAINS` | Folder → Folder/File | 目录结构 |
| `DEFINES` | File → Class/Method/Field/Constructor/Variable/Annotation | 文件定义的元素 |
| `CALLS` | Method/Constructor → Method/Constructor/Class | 方法调用 |
| `IMPORTS` | File → Class/Interface | import 语句 |
| `EXTENDS` | Class → Class, Interface → Interface | 继承 |
| `IMPLEMENTS` | Class → Interface | 接口实现 |
| `HAS_METHOD` | Class/Interface → Method | 类包含的方法 |
| `HAS_PROPERTY` | Class/Interface → Field | 类包含的字段 |
| `HAS_CONSTRUCTOR` | Class → Constructor | 类包含的构造器 |
| `HAS_ANNOTATION` | Class/Method/Field → Annotation | 注解关联 |
| `ACCESSES` | Method/Constructor → Field | 字段访问 |
| `EXPORTS` | File → Method/Class/Variable | 导出关系（JS/TS） |

## 分析管线

```
analyze(repo_path)
  ├── [1] scanner.scan()              → List[SourceFile]     文件扫描（支持 .gitignore）
  │       ├── .java → language="java"
  │       ├── .js/.jsx → language="js"
  │       ├── .ts/.tsx → language="ts"
  │       └── .vue → language="vue"
  ├── [2] extractor.parse()           → ParsedFile           tree-sitter AST 解析
  │       ├── Java extractor: 类/接口/方法/字段/构造器/变量/调用/注解
  │       ├── JS extractor: 函数/类/箭头函数/导入导出/调用/require
  │       ├── TS extractor: JS 全部 + interface/type/enum/装饰器/类型注解
  │       └── Vue extractor: <script> 块 + 模板表达式 + 组件引用 + ref 模式
  ├── [3] resolver.resolve_calls()    → 跨文件调用关系       多语言解析策略 + 置信度
  │       ├── Java: 4 级类型感知策略
  │       └── JS/TS/Vue: 6 Case import/export + 变量链 + ref 解析
  ├── [4] GraphStore.init_schema()    → 创建 KuzuDB schema
  ├── [5] GraphStore.batch_write()    → 写入节点和关系
  │       ├── UNWIND 批量节点插入
  │       └── COPY FROM CSV 批量关系插入
  └── [6] register_repo()             → 保存到全局注册表
```

## 性能

### Java 性能（shenyu 项目，3,176 文件）

| 阶段 | GitNexus 原版 | 首次优化 | 当前版本 | 总改善 |
|------|--------------|---------|---------|--------|
| Parse (AST 解析) | ~161s | 78s | **52s** | **-68%** |
| Resolve (调用解析) | ~183s | 22s | **18s** | **-90%** |
| Graph (知识图谱) | ~150s | 93s | **63s** | **-58%** |
| **总计** | **~496s (~8.3 min)** | **~194s (~3.2 min)** | **~133s (~2.2 min)** | **-73%** |

### JS/TS/Vue 覆盖率（vue-pure-admin 项目，484 文件）

| 版本 | 整体召回率 | Vue 召回率 | TS 召回率 | JS 召回率 | 调用数 |
|------|-----------|-----------|----------|----------|--------|
| v0 | 30.6% | 0% | 30.4% | - | 112 |
| v7 | 80.3% | 80.3% | 80.3% | - | 664 |
| v8 | 89.5% | 91.8% | 89.3% | 83.9% | 1,944 |
| **v11** | **92.6%** | **92.3%** | **92.2%** | **95.1%** | **2,075** |
| **宽松** | **97.8%** | **95.3%** | **100.0%** | **100.0%** | - |

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
├── cli/                    # CLI 命令入口（PyGitNexus）
│   ├── main.py             # 主命令组 (click group) + TestNexus 子命令组
│   ├── analyze.py          # 索引命令
│   ├── query.py            # 符号搜索
│   ├── context.py          # 符号上下文
│   ├── cypher.py           # 原始 Cypher 查询
│   ├── list.py             # 列出仓库
│   ├── status.py           # 仓库状态
│   ├── clean.py            # 删除索引
│   ├── mcp.py              # MCP Server 启动命令
│   ├── setup.py            # 编辑器 MCP 配置
│   └── group.py            # 项目分组命令
├── core/                   # 核心分析引擎（PyGitNexus）
│   ├── scanner.py          # 文件扫描（支持 .gitignore）
│   ├── extractor.py        # Java tree-sitter AST 解析
│   ├── extractor_js.py     # JavaScript AST 解析
│   ├── extractor_ts.py     # TypeScript/TSX AST 解析
│   ├── extractor_vue.py    # Vue SFC 解析
│   ├── resolver.py         # Java 跨文件调用解析（4 级策略）
│   ├── resolver_js.py      # JS/TS/Vue 调用解析（6 Case 策略）
│   ├── pipeline.py         # 多语言管线编排 + 并行调度
│   └── models.py           # 数据模型（SourceFile, ParsedFile, etc.）
├── graph/                  # 图数据库层（PyGitNexus）
│   ├── schema.py           # KuzuDB schema 定义
│   └── store.py            # KuzuDB 操作封装 (UNWIND/COPY FROM)
├── mcp/                    # MCP Server（PyGitNexus）
│   └── server.py           # MCP 工具定义（6 个 tools）
├── search/                 # 查询接口（PyGitNexus）
│   └── query.py            # 符号搜索、上下文、Cypher
├── storage/                # 存储管理（PyGitNexus）
│   └── repo_manager.py     # 仓库注册表
└── testnexus/              # TestNexus 前端测试模块
    ├── cli/                # TestNexus CLI 命令
    │   ├── main.py         # test 命令入口
    │   ├── analyze.py      # test analyze
    │   ├── generate.py     # test generate（穷举测试用例）
    │   ├── report.py       # test report（HTML 报告）
    │   ├── llm_analyze.py  # test llm-analyze
    │   ├── record.py       # test record（浏览器录制）
    │   ├── replay_cmd.py   # test replay（操作重放）
    │   ├── impact.py       # test impact（影响分析）
    │   ├── modules_cmd.py  # test modules（模块查看）
    │   ├── status.py       # test status
    │   ├── list.py         # test list
    │   ├── clean.py        # test clean
    │   └── _common.py      # 公共参数
    ├── core/               # 核心引擎
    │   ├── pipeline.py     # 分析管线编排
    │   ├── metadata_extractor.py # 前端项目元数据提取
    │   ├── module_infer.py # LLM 模块推断
    │   ├── llm_analyzer.py # LLM 分析编排
    │   ├── llm_provider.py # LLM provider 自动检测
    │   ├── mapper.py       # 数据映射
    │   └── models.py       # TestNexus 数据模型
    ├── scanners/           # 前端扫描器
    │   ├── base.py         # 扫描器基类
    │   ├── vue_scanner.py  # Vue 项目扫描器
    │   └── react_scanner.py # React 项目扫描器
    ├── generators/         # 测试用例生成
    │   ├── api_test.py     # API 测试用例生成（三级穷举）
    │   └── e2e_test.py     # E2E 测试用例生成
    ├── graph/              # TestNexus 图数据库层
    │   ├── schema.py       # TestNexus KuzuDB schema
    │   └── store.py        # TestNexus 图操作
    ├── report/             # 报告生成
    │   └── html_report.py  # HTML 报告生成（含页面操作集）
    ├── recorder/           # 浏览器录制
    │   ├── capture.py      # 操作捕获
    │   └── replay.py       # 操作重放
    └── storage/            # TestNexus 存储
        └── meta.py         # 元数据管理
```

## 准确率验证

### Java（shenyu 项目，3,176 文件）

| 指标 | GitNexus | PyGitNexus |
|------|----------|-----------|
| 有效调用 | 22,074 | 25,946 |
| 假阳性率 | 2.0% | **0.7%** |
| 双方共有 | 18,300 | - |
| 仅 GitNexus | 4,050 | - |
| 仅 PyGitNexus | - | 7,712 |
| 覆盖率 | - | 117.5% |

方法级匹配分布：

| 匹配类型 | 数量 | 说明 |
|---------|------|------|
| 完全匹配 | 1,114 | 调用目标完全一致 |
| PyGitNexus 超集 | 1,094 | PY 包含 GN 全部 + 额外发现 |
| GitNexus 超集 | 752 | GN 包含 PY 全部 + 额外发现 |
| 部分重叠 | 1,050 | 双方都有对方没有的调用 |
| 仅 PyGitNexus | 783 | GN 未检测到 |
| 仅 GitNexus | 276 | PY 未检测到 |

GitNexus 独有调用中约 2,000+ 条为已知的 JDK/Logger 方法误匹配假阳性。

### JS/TS/Vue（vue-pure-admin 项目，484 文件）

| 指标 | 值 |
|------|-----|
| 严格召回率 | 92.6% |
| 宽松召回率 | 97.8% |
| TS 宽松召回率 | 100.0% |
| JS 宽松召回率 | 100.0% |
| Vue 宽松召回率 | 95.3% |

剩余差距主要来自：
- CSS `:deep()` 伪选择器被 GitNexus 误识别为函数调用（~6 条，PyGitNexus 正确忽略）
- Vue 模板属性值引用（~3 条，非真实函数调用）
- Vue 组件构造函数引用（~1 条）
- 外部包方法链（~1 条）
- Vue 自引用组件（~1 条）
- 跨文件未导出函数（~1 条）

### 回归测试

PyGitNexus 内置回归测试脚本，确保多语言解析器互不影响：

```bash
# 前端 Vue 回归测试（newbee-mall-vue3-app，33 文件，默认）
uv run python tests/regression_newbee.py

# Java 回归测试（shenyu 项目，3,176 文件）
uv run python tests/regression_shenyu.py

# JS/TS/Vue 自动化对比测试
python tests/compare_calls.py /path/to/js-ts-project
```

#### newbee 回归测试内容（6 项）

1. Vue 文件扫描完整性（>=20 文件）
2. 页面提取完整性（>=10 页面，router + views 约定）
3. 组件提取（>=15 组件）
4. 操作 + API 调用提取（>=15 操作，>=15 API 调用）
5. 模块推断（不崩溃验证）
6. 完整流水线运行（fresh 分析 + 图谱验证 >50 节点）

#### shenyu 回归测试内容（6 项）

1. Java 文件扫描完整性（>3000 文件）
2. Java 文件解析完整性（Class/Method/Call 数量验证）
3. Java 调用解析完整性
4. 与 GitNexus Java 调用链对比
5. JS/TS 解析器 import 隔离（无交叉导入）
6. 完整流水线运行（全量 Java 解析 + 调用解析）

### 对比测试

```bash
# JS/TS/Vue 自动化对比测试
python tests/compare_calls.py /path/to/js-ts-project
```
