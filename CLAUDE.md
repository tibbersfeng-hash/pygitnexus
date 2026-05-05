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

## README 更新规则

每次功能变更后，必须更新 `README.md` 中的对应章节，包括：
- CLI 命令表（如有新命令）
- 项目结构图（如有新模块）
- 依赖表（如有新依赖）
- 新增功能的使用说明和示例
