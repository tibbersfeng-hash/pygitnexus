# PyGitNexus vs GitNexus 调用链对比测试方案

## 1. 测试目的

验证 PyGitNexus（Python 实现）的 Java 调用链提取质量，以原版 GitNexus（Node.js 实现）为基准，确保：

1. **覆盖率相当或更优** — PyGitNexus 检测到的有效调用不低于 GitNexus
2. **假阳性率显著更低** — 避免 JDK 方法误匹配到项目业务方法
3. **方法级匹配一致** — 每个方法的调用集合与 GitNexus 基本一致

## 2. 测试原理

### 2.1 核心思路

将两个工具对同一 Java 项目的分析结果导出为统一格式，进行集合运算：

```
GitNexus 调用集合 = { (caller_method, target_method) }
PyGitNexus 调用集合 = { (caller_method, target_method) }

交集 = GN ∩ PY    → 双方共有的调用（预期为主体部分）
仅 GN = GN - PY   → GitNexus 独有（可能为假阳性）
仅 PY = PY - GN   → PyGitNexus 独有（需人工验证）
```

### 2.2 对比粒度

| 粒度 | 说明 |
|------|------|
| **数量级** | 总调用数、有效调用数、假阳性数/率 |
| **调用级** | 每条 `(caller, target)` 是否一致 |
| **方法级** | 每个方法的调用集合是否一致 |

### 2.3 假阳性识别

GitNexus 已知的假阳性模式（因其不做类型解析，仅按方法名匹配）：

| 模式 | 根因 | 典型场景 |
|------|------|---------|
| `Map.get()` → 项目 `get()` | JDK Map 的 `.get(key)` 被匹配到业务类的 `get()` 方法 | `body.get("alertId")` |
| `logger.error()` → `ApiResponse.error()` | JDK Logger 的 `.error(msg)` 被匹配到业务类的 `error()` 静态工厂 | `log.error("failed")` |
| `List.stream()` → 项目 `stream()` | Java Stream API 的 `.stream()` 被匹配到业务类的 `stream()` 方法 | `alerts.stream()` |
| `Exception.getMessage()` → 项目 `getMessage()` | JDK Exception 的 `.getMessage()` 被匹配到业务类的同名方法 | `e.getMessage()` |

PyGitNexus 因做了精确的类型解析（tree-sitter AST + per-method type map），理论上不应出现上述假阳性。

## 3. 测试工具

### 3.1 自动化对比脚本

```
pygitnexus/tests/compare_calls.py
```

用法：
```bash
python tests/compare_calls.py /path/to/java/project
```

环境变量：
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PYGITNEXUS_CLI` | PyGitNexus CLI 路径 | `pygitnexus` |
| `GITNEXUS_CLI` | GitNexus CLI 路径 | `gitnexus` |
| `OUTPUT_DIR` | 输出目录 | `./comparison_output` |

### 3.2 脚本工作流程

```
┌─────────────────────────────────────────────────────┐
│                    compare_calls.py                  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  1. GitNexus analyze + cypher export → gn_calls     │
│  2. PyGitNexus analyze + cypher export → py_calls   │
│  3. 规范化 → (caller_method, target_method) 集合     │
│  4. 集合运算: 交集 / GN独有 / PY独有                  │
│  5. 假阳性分类 → 识别已知模式                          │
│  6. 方法级匹配 → exact/superset/partial              │
│  7. 生成报告 (summary.txt + TSV 文件)                 │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## 4. 输出文件

| 文件 | 内容 |
|------|------|
| `summary.txt` | 完整对比报告（Markdown 格式） |
| `both.tsv` | 双方共有的调用 |
| `only_gn.tsv` | 仅 GitNexus 有的调用 |
| `only_py.tsv` | 仅 PyGitNexus 有的调用 |
| `gn_false_positives.tsv` | GitNexus 已知假阳性详情 |
| `py_false_positives.tsv` | PyGitNexus 已知假阳性详情 |
| `gitnexus_calls.tsv` | GitNexus 全量调用 |
| `pygitnexus_calls.tsv` | PyGitNexus 全量调用 |
| `method_match.tsv` | 方法级匹配统计 |

所有 TSV 文件均以制表符分隔，可用 Excel/Sheets 直接打开。

## 4.1 已知限制

- **GitNexus 64KB 截断**: GitNexus CLI 的 stdout 输出在 ~64KB 时被截断。因此查询仅返回 `c.name, t.name` 两列，不含 `filePath` 和 `lineNumber`。
- **假阳性识别范围**: 仅识别预定义的已知模式（`get`, `error`, `stream`, `getMessage` 等），无法覆盖所有假阳性。
- **GitNexus 需要已索引**: 项目必须先被 `gitnexus analyze` 索引过，否则 cypher 查询返回空。

## 5. 测试项目要求

### 5.1 最低要求

- Java 项目，包含 `.java` 源文件
- 非空项目（至少有一个包含方法调用的 Java 文件）
- 两个工具均能解析（tree-sitter-java + GitNexus parser）

### 5.2 推荐项目特征

为了全面测试，测试项目应覆盖以下场景：

| 场景 | 为什么重要 |
|------|-----------|
| Lambda 表达式 | 验证 lambda 参数类型推断（`stream().filter(x -> ...)`） |
| 方法引用 | 验证 `Type::method` 语法处理 |
| 链式调用 | 验证 `foo().bar().baz()` 去重逻辑 |
| 泛型返回类型 | 验证 `ApiResponse<T>` 等泛型传播 |
| 增强 for 循环 | 验证 `for (Item i : list)` 变量类型提取 |
| 日志调用 | 验证 `logger.error()` 不被误匹配 |
| Map 操作 | 验证 `map.get("key")` 不被误匹配 |
| Stream API | 验证 `list.stream()` 不被误匹配 |
| 异常处理 | 验证 `e.getMessage()` 不被误匹配 |

## 6. 评估标准

### 6.1 通过标准

| 指标 | 阈值 | 说明 |
|------|------|------|
| PyGitNexus 有效调用 | ≥ GitNexus 有效调用的 95% | 覆盖率相当 |
| PyGitNexus 假阳性率 | ≤ 1% | 显著低于 GitNexus（通常 ~18%） |
| 方法级完全匹配 | ≥ 80% | 大部分方法的调用集合一致 |
| PyGitNexus 独有调用 | 全部可解释为真阳性 | 通常是 GitNexus 故意不追踪的 getter/setter |

### 6.2 质量分级

| 等级 | 条件 |
|------|------|
| **优秀** | 有效调用 ≥ GN，假阳性率 = 0% |
| **良好** | 有效调用 ≥ 95% GN，假阳性率 ≤ 1% |
| **合格** | 有效调用 ≥ 90% GN，假阳性率 ≤ 5% |
| **需改进** | 有效调用 < 90% GN 或 假阳性率 > 5% |

## 7. 测试执行

### 7.1 前置条件

```bash
# 安装 PyGitNexus
cd pygitnexus
pip install -e .

# 确认 GitNexus 已安装
gitnexus --version
```

### 7.2 执行命令

```bash
cd pygitnexus
python tests/compare_calls.py /path/to/java/project
```

如果 GitNexus 已经分析过该项目，可以跳过 PyGitNexus 重新分析：

```bash
SKIP_PY=1 python tests/compare_calls.py /path/to/java/project
```

### 7.3 示例：dashboard-backend 项目

```bash
python tests/compare_calls.py /home/claude/codespace/life-death/dashboard-backend
```

### 7.4 查看结果

```bash
cat comparison_output/summary.txt
```

## 8. 报告解读

### 8.1 总体数据表格

```
| 指标 | GitNexus | PyGitNexus |
|------|----------|-----------|
| 原始调用总数 | 1263 | 1029 |
| 假阳性数 | 232 | 0 |
| 假阳性率 | 18.4% | 0.0% |
| **有效调用** | **1031** | **1029** |
```

解读：
- **原始调用**：两个工具检测到的调用总数（含假阳性）
- **假阳性**：被错误匹配到不相关方法的调用
- **有效调用** = 原始调用 - 假阳性

### 8.2 方法级匹配

```
| 匹配类型 | 数量 |
|---------|------|
| 完全匹配 | 225 |
| PyGitNexus 超集 | 9 |
| GitNexus 超集 | 105 |
| 部分重叠 | 7 |
| 仅 PyGitNexus | 2 |
| 仅 GitNexus | 81 |
```

解读：
- **完全匹配**：该方法的调用集合两个工具完全一致
- **PyGitNexus 超集**：PY 包含 GN 全部调用，还有额外调用（通常是真阳性）
- **GitNexus 超集**：GN 包含 PY 全部调用，还有额外调用（通常是假阳性）
- **部分重叠**：双方各有对方没有的调用
- **仅 PyGitNexus/GitNexus**：一个工具检测到了另一个完全没有的方法

### 8.3 假阳性分类

```
| 假阳性模式 | 数量 | 占比 |
|-----------|------|------|
| JDK get 方法误匹配 | 132 | 56.9% |
| 日志 error 误匹配 | 90 | 38.8% |
| Stream API 误匹配 | 7 | 3.0% |
| 异常 getMessage 误匹配 | 3 | 1.3% |
```

解读：每个模式代表一类已知的误匹配根因，可用于验证 PyGitNexus 是否已正确处理。

## 9. 手动验证流程

对于自动化工具无法判断的情况，建议人工抽样验证：

### 9.1 验证 PyGitNexus 独有调用

```bash
# 打开 only_py.tsv，抽样 10 条
head -20 comparison_output/only_py.tsv

# 对每条调用，在源码中定位：
# 1. 找到 caller_method 所在的 Java 文件
# 2. 确认 target_method 是否确实被调用
# 3. 判断是否为真阳性
```

### 9.2 验证 GitNexus 独有调用（真阳性部分）

```bash
# 打开 only_gn.tsv，排除已知假阳性后抽样
# 对每条调用，在源码中确认是否真实存在
```

## 10. 常见问题

### Q: GitNexus 分析失败

确认项目包含 `.git` 目录或使用 `--skip-git` 参数。

### Q: PyGitNexus 分析失败

确认项目包含 `.java` 文件，且 tree-sitter-java 已正确安装。

### Q: 假阳性率异常高

检查项目是否大量使用 Map.get()、日志、Stream API 等模式。这些是 GitNexus 的已知弱点。

### Q: PyGitNexus 调用数少于 GitNexus

这是正常现象。GitNexus 的原始调用数通常更多，但其中包含大量假阳性。比较**有效调用**更有意义。

## 11. 扩展：自定义假阳性模式

如果测试中发现新的假阳性模式，可以在 `compare_calls.py` 中扩展 `FALSE_POSITIVE_PATTERNS`：

```python
FALSE_POSITIVE_PATTERNS = [
    # (target_method, known_false_positive_sources, description)
    ("get", ["Map.get", "Optional.get"], "JDK get 方法误匹配"),
    ("error", ["Logger.error"], "日志 error 误匹配"),
    # 添加新的模式...
]
```
