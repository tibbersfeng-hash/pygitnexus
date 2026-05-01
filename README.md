# PyGitNexus

Java 代码库知识图谱构建工具 —— [GitNexus](https://github.com/abhigyanpatwari/GitNexus) 的高性能 Python 实现。

通过静态分析 Java 源码，将项目结构、类、接口、方法、字段、调用关系等构建为 KuzuDB 知识图谱，为 AI Agent 提供深度代码感知能力。

## 为什么是 PyGitNexus

| 能力 | GitNexus (原版) | PyGitNexus |
|------|----------------|-----------|
| 解析引擎 | tree-sitter | tree-sitter-java |
| 图数据库 | KuzuDB | KuzuDB |
| 调用解析 | 名称匹配 | 类型感知 4 级解析策略 |
| 假阳性率 | 2.0% | **0.7%** |
| 有效调用 | 22,074 | **25,946** (+17.5%) |
| 3,000 文件分析 | ~8 min | **~3 min** |

**关键优势**：
- 基于 receiver 类型的精确调用解析，减少 JDK/Logger 方法误匹配
- 跨文件 return type 传播，支持链式调用 (如 `service.getUser().getAddress()`)
- 接口派发解析，自动追踪 interface → implementing class
- JDK 静态方法白名单 + 常见 JDK 类黑名单，有效抑制假阳性
- 多进程并行解析 + COPY FROM 批量写入，分析速度提升 2.5x

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

| 包 | 版本 | 用途 |
|---|------|------|
| tree-sitter | 0.23+ | AST 解析引擎 |
| tree-sitter-java | 0.23+ | Java 语法文件 |
| kuzu | 0.11+ | 嵌入式图数据库 |
| click | 8.1+ | CLI 框架 |
| mcp | 1.26+ | Model Context Protocol SDK |

## 快速开始

```bash
# 索引一个 Java 项目
cd your-java-project
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

## CLI 命令参考

| 命令 | 说明 | 示例 |
|------|------|------|
| `analyze [path]` | 索引 Java 项目，默认当前目录 | `analyze /path/to/project --force` |
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

## 输出示例

```
$ pygitnexus analyze my-project
Analyzing /path/to/my-project...
  [  5%] Scanning for Java files...
  [ 10%] Found 42 Java files
  [ 15%] Parsing Java files (concurrent)...
  [ 55%] Parsed 42/42 files
  [ 60%] Resolving cross-file relations...
  [ 70%] Resolved 156 calls in 0.3s
  [ 70%] Building knowledge graph...
  [100%] Analysis complete (parse: 2.1s, resolve: 0.3s, graph: 1.8s)

Analysis complete!
  Files:    42
  Classes:  38
  Methods:  215
  Fields:   89
  Calls:    156
  Imports:  24
```

```
$ pygitnexus query "User"
Found 5 symbol(s) matching 'User':

  [Class] com.example.User
    src/main/java/com/example/User.java:3
  [Method] User
    src/main/java/com/example/User.java:7
  [File] User.java
    src/main/java/com/example/User.java
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

## 调用解析策略

PyGitNexus 使用 4 级递进策略解析方法调用目标，每级附带置信度评分：

### Strategy 0: 构造函数调用
当调用目标名称与 receiver 类型名一致时，识别为构造函数调用。
- 精确构造器匹配 → 置信度 0.9
- 类节点回退 → 置信度 0.85

### Strategy 1: 已知 receiver 类型（来自局部变量类型推断）
- 精确类名.方法名匹配 → 置信度 0.9
- Builder 模式识别 (`build/builder/newBuilder`) → 置信度 0.85
- 接口派发 (interface → implementing class) → 置信度 0.6
- 类名后缀匹配 → 置信度 0.8
- 方法名 + 类名相似度匹配 → 置信度 0.5

### Strategy 1.5: 跨文件 return type 传播
当 receiver 类型未知但 receiver 名称匹配已知方法时，用该方法的返回类型解析调用。
```java
// service.getUser().getAddress() — getAddress() 的 receiver "getUser" 返回 User
// 因此将 getAddress() 解析到 User 类
```

### Strategy 2: receiver 是已知类名（静态调用）
- 静态字段类型推断 → 置信度 0.75
- 精确类名匹配 → 置信度 0.9
- 类名后缀匹配 → 置信度 0.8
- 方法名全局匹配（仅大写开头的类名）→ 置信度 0.5

### Strategy 3: 无 receiver（同类调用 / 静态导入）
- 同类方法匹配 → 置信度 0.7
- 继承链追溯 → 置信度 0.65
- 测试框架静态导入白名单 (JUnit/Mockito/Hamcrest) → 置信度 0.3

### JDK 假阳性抑制

| 机制 | 说明 |
|------|------|
| JDK 静态方法白名单 | `Files.list`, `Paths.get`, `Optional.get` 等不会匹配项目方法 |
| JDK 类黑名单 | `Logger`, `Map`, `List`, `Optional`, `Stream`, `JsonNode` 等不会参与回退匹配 |
| 常见 JDK 方法过滤 | `get/set/add/isEmpty/toString/equals` 等在回退匹配中被跳过 |
| 测试框架白名单 | 仅 JUnit/Mockito/Hamcrest 的断言方法允许全局名称匹配 |

## 知识图谱 Schema

### 节点表

| 表名 | 字段 | 说明 |
|------|------|------|
| `File` | id, name, filePath, content | Java 源文件 |
| `Folder` | id, name, filePath | 文件夹 |
| `Class` | id, name, filePath, startLine, endLine, isPublic, isAbstract, isInterface, content | 类 |
| `Interface` | id, name, filePath, startLine, endLine, isPublic, content | 接口 |
| `Method` | id, name, className, filePath, startLine, endLine, returnType, parameterCount, isStatic, isPublic, isConstructor, content | 方法 |
| `Field` | id, name, typeName, className, filePath, startLine, endLine, isStatic, isPublic | 字段 |
| `Constructor` | id, name, className, filePath, startLine, endLine, parameterCount, isPublic, content | 构造器 |
| `Variable` | id, name, typeName, methodName, className, filePath, line, isFinal | 局部变量 |
| `Annotation` | id, name, targetType, targetName, filePath, line, attributes | 注解 |

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

## 分析管线

```
analyze(repo_path)
  ├── [1] scanner.scan()              → List[JavaFile]       文件扫描（支持 .gitignore）
  ├── [2] extractor.parse()           → ParsedFile           tree-sitter AST 解析
  │       ├── 类/接口定义 + extends/implements
  │       ├── 方法定义 + 参数 + 返回值
  │       ├── 字段定义 + 类型
  │       ├── 构造函数定义
  │       ├── 局部变量声明
  │       ├── 方法调用站点 (CallSite) + receiver 类型推断
  │       ├── 字段访问 (FieldAccess)
  │       ├── import 语句
  │       └── 注解 (Annotation)
  ├── [3] resolver.resolve_calls()    → 跨文件调用关系       4 级解析策略 + 置信度
  │       ├── import → FQN class → 文件路径映射
  │       ├── simple name → FQN 反向索引
  │       ├── receiver type 精确匹配
  │       ├── interface 派发
  │       ├── Builder 模式识别
  │       ├── 跨文件 return type 传播
  │       ├── 静态字段类型推断
  │       ├── 继承链追溯
  │       ├── JDK 假阳性抑制
  │       └── 测试框架静态导入白名单
  ├── [4] GraphStore.init_schema()    → 创建 KuzuDB schema
  ├── [5] GraphStore.batch_write()    → 写入节点和关系
  │       ├── UNWIND 批量节点插入
  │       └── COPY FROM CSV 批量关系插入
  └── [6] register_repo()             → 保存到全局注册表
```

## 性能

基于 shenyu 项目（3,176 Java 文件）的基准测试：

| 阶段 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| Parse (ProcessPoolExecutor) | 161s | 78s | -51% |
| Resolve (simple_to_fqn 索引) | 183s | 22s | -88% |
| Graph (COPY FROM 批量写入) | ~150s | 93s | -38% |
| **总计** | **~496s (~8.3 min)** | **~194s (~3.2 min)** | **-61%** |

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
│   ├── main.py             # 主命令组 (click group)
│   ├── analyze.py          # 索引命令
│   ├── query.py            # 符号搜索
│   ├── context.py          # 符号上下文
│   ├── cypher.py           # 原始 Cypher 查询
│   ├── list.py             # 列出仓库
│   ├── status.py           # 仓库状态
│   ├── clean.py            # 删除索引
│   ├── mcp.py              # MCP Server 启动命令
│   └── setup.py            # 编辑器 MCP 配置
├── core/                   # 核心分析引擎
│   ├── scanner.py          # 文件扫描（支持 .gitignore）
│   ├── extractor.py        # tree-sitter AST 解析 + 符号提取
│   ├── resolver.py         # 跨文件 import/call 解析（4 级策略）
│   ├── pipeline.py         # 管线编排 + 并行调度
│   └── models.py           # 数据模型
├── graph/                  # 图数据库层
│   ├── schema.py           # KuzuDB schema 定义
│   └── store.py            # KuzuDB 操作封装 (UNWIND/COPY FROM)
├── mcp/                    # MCP Server
│   └── server.py           # MCP 工具定义（6 个 tools）
├── search/                 # 查询接口
│   └── query.py            # 符号搜索、上下文、Cypher
└── storage/                # 存储管理
    └── repo_manager.py     # 仓库注册表
```

## 准确率验证

在 shenyu 项目上与 GitNexus 原版进行全量对比（429 个方法级方法）：

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

验证命令：
```bash
python tests/compare_calls.py /path/to/java/project
```
