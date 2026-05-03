# PyGitNexus 性能基准与验收标准

> 每次代码修改后，必须先通过小项目初测，再通过大项目验收。

## 小项目验收（初测，必须通过）

小项目用于快速验证功能是否正常，分析应在 **< 15 秒** 内完成。

### 目标项目：dashboard-backend

| 项目 | 路径 | 文件数 | 语言 |
|------|------|--------|------|
| dashboard-backend | `/home/claude/codespace/life-death/dashboard-backend/` | 127 | Java |

### 验收命令

```bash
cd /home/claude/.cc-connect/workspace/pygitnexus
uv run pygitnexus analyze /home/claude/codespace/life-death/dashboard-backend --force
```

### 验收标准

| 指标 | 要求 |
|------|------|
| 总耗时 | < 15 秒 |
| 解析耗时 | < 8 秒 |
| 图谱构建 | < 5 秒 |
| 文件数 | 127（全部解析） |
| Class 节点 | >= 100 |
| Method 节点 | >= 800 |
| Call 关系 | >= 3000 |
| 无崩溃 | 无 RecursionError / RuntimeError |

### 参考基线（2026-05-03）

```
Analysis complete!
  Files:    127
  Classes:  143
  Methods:  1065
  Fields:   584
  Calls:    5486
  Imports:  944
  Total:    6.4s (parse: 3.9s, resolve: 0.0s, graph: 2.2s)
```

## 中项目验收（JS/TS/Vue 验证）

### 目标项目：vue-pure-admin

| 项目 | 路径 | 文件数 | 语言 |
|------|------|--------|------|
| vue-pure-admin | `/home/claude/codespace/vue-pure-admin/` | 484 | JS/TS/Vue |

### 验收命令

```bash
uv run pygitnexus analyze /home/claude/codespace/vue-pure-admin --force
```

### 验收标准

| 指标 | 要求 |
|------|------|
| 总耗时 | < 60 秒 |
| 解析耗时 | < 40 秒 |
| 文件数 | 484（全部解析） |
| Calls 关系 | >= 5000 |
| 无崩溃 | 无 RecursionError / RuntimeError / Duplicate key |

### 参考基线（2026-05-03）

```
Analysis complete!
  Files:    484
  Classes:  108
  Methods:  1136
  Fields:   10
  Calls:    18316
  Imports:  2710
  Total:    24.6s (parse: 21.9s, resolve: 0.3s, graph: 1.6s)
```

## 大项目验收（压力测试，重要发布前执行）

### 目标项目：shenyu

| 项目 | 路径 | 文件数 | 语言 |
|------|------|--------|------|
| shenyu | `/home/claude/codespace/shenyu/` | 3,176 | Java |

### 验收命令

```bash
uv run pygitnexus analyze /home/claude/codespace/shenyu --force
```

### 验收标准

| 指标 | 要求 |
|------|------|
| 总耗时 | < 180 秒（3 分钟） |
| 解析耗时 | < 90 秒 |
| 调用解析 | < 30 秒 |
| 图谱构建 | < 90 秒 |
| 文件数 | > 3000 |
| Call 关系 | >= 20000 |
| 假阳性率 | < 2% |
| 无崩溃 | 无 RecursionError / RuntimeError / Duplicate key |

### 参考基线

| 阶段 | GitNexus 原版 | 首次优化 | 当前版本 |
|------|--------------|---------|---------|
| Parse | ~161s | 78s | **~52s** |
| Resolve | ~183s | 22s | **~18s** |
| Graph | ~150s | 93s | **~63s** |
| **总计** | **~496s** | **~194s** | **~133s** |

## 回归测试

### 运行回归测试

```bash
uv run python tests/regression_shenyu.py
```

### 回归测试标准

| 测试项 | 要求 |
|--------|------|
| Java 文件扫描 | > 3000 文件 |
| Java 解析完整性 | Class/Method/Call 数量合理 |
| JS/TS 解析器隔离 | 无交叉导入 |
| 完整流水线 | 全量 Java 解析 + 调用解析通过 |

### JS/TS/Vue 对比测试

```bash
python tests/compare_calls.py /path/to/js-ts-project
```

## 验收流程

```
修改代码
  ├── 1. 小项目验收 (dashboard-backend, < 15s)
  │     └── 通过 → 继续
  │     └── 失败 → 修复
  ├── 2. 中项目验收 (vue-pure-admin, < 60s) [如涉及 JS/TS/Vue]
  │     └── 通过 → 继续
  │     └── 失败 → 修复
  ├── 3. 大项目验收 (shenyu, < 180s) [重要发布前]
  │     └── 通过 → 继续
  │     └── 失败 → 修复
  ├── 4. 回归测试 (tests/regression_shenyu.py)
  │     └── 通过 → 发布
  │     └── 失败 → 修复
  └── 5. 发布
```

## 已知性能优化点

| 优化 | 效果 | 文件 |
|------|------|------|
| 预计算 AST 字节范围索引 | Parse 提升 68% | extractor.py |
| ThreadPoolExecutor 8 workers | 并行解析 | pipeline.py |
| COPY FROM CSV 批量写入 | Graph 提升 58% | store.py |
| 类型感知 4 级解析策略 | Resolve 降低 90% | resolver.py |
| sys.setrecursionlimit(10000) | 避免深层嵌套崩溃 | extractor.py |
| 节点 ID 去重 | 避免压缩 JS 主键冲突 | pipeline.py |
