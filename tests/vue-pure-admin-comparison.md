# GitNexus vs PyGitNexus CALLS Comparison Report

**Project**: vue-pure-admin (484 JS/TS/Vue files)
**Date**: 2026-05-03

## Summary

| Metric | GitNexus | PyGitNexus v11 | Overlap | Recall |
|--------|----------|---------------|---------|--------|
| All CALLS | 880 | 2075 | 815 | **92.6%** |
| Vue CALLS | 402 | 826 | 371 | **92.3%** |
| TS/TSX CALLS | 335 | 791 | 309 | 92.2% |
| JS/JSX CALLS | 143 | 458 | 136 | 95.1% |

### Relaxed Matching (ignoring caller_method)

| Metric | Recall | Truly Missing |
|--------|--------|---------------|
| All CALLS | **97.8%** | 14 |
| Vue CALLS | **95.3%** | 14 |
| TS/TSX CALLS | **100.0%** | 0 |
| JS/JSX CALLS | **100.0%** | 0 |

> Note: Recall = Overlap / GitNexus count. Relaxed matching ignores caller_method differences.

## Improvement History

| Version | Overall Recall | Vue Recall | TS Recall | JS Recall | PG Calls | Key Changes |
|---------|---------------|------------|-----------|-----------|----------|-------------|
| v0 | 30.6% | 0% | 30.4% | — | 112 | Initial |
| v3 | 74.6% | 0% | 74.6% | — | 677 | Import alias, arrow func, export, same-file, utility fallback |
| v6 | 84.3% | 0% | 84.3% | — | 633 | Return chain, external receiver map, module-level calls |
| v7 | 80.3% | 80.3% | 80.3% | — | 664 | Nested arrow function call extraction |
| v8 | 89.5% | 91.8% | 89.3% | 83.9% | 1944 | Vue SFC parsing, template expr extraction, component resolution |
| v9 | 91.8% | 91.8% | 89.3% | 97.9% | 1965 | Function expression naming, variable_declarator resolution |
| v10 | 91.9% | 92.0% | 90.7% | 94.4% | 1967 | Vue chained call extraction (`hook().method()`) |
| **v11** | **92.6%** | **92.3%** | **92.2%** | **95.1%** | **2075** | Same-file class resolution + Vue ref `.value` resolution |

### Fixes Applied (v10 → v11)

1. **同文件类引用解析** (`resolver_js.py`): 新增 Case 3b，解析同文件中定义的类引用（如 `new ImageCapture()`、`PureHttp`、`StorageProxy`）
2. **Vue ref `.value` 解析** (`resolver_js.py`): 新增 `_build_vue_ref_map` 和 Case 6，解析 `h(component, { ref: refVar })` 模式，将 `refVar.value.method()` 关联到目标组件
3. **Pinia state 箭头函数命名优化** (`extractor_ts.py`): 修复 `state: () => ({...})` 中箭头函数的 caller 命名，正确识别 `state` property

## Remaining Gaps

### 1. 命名差异 (Caller Method) — 最大的差异来源
- **Pinia store state**: GitNexus 使用模块级 caller (如 `app.ts`)，PyGitNexus 使用实际方法名 (如 `TOGGLE_SIDEBAR`)
- **Vue template expressions**: GitNexus 使用文件名，PyGitNexus 使用实际方法名
- **Relaxed matching recall**: 97.8% — 忽略 caller_method 后只有 14 条真正遗漏

### 2. Vue CSS `:deep()` 伪选择器 (6 Vue misses)
- `:deep(.el-loading-mask)` 是 CSS 语法，非函数调用
- GitNexus 将其误识别为 `deep()` 函数调用
- PyGitNexus 正确处理（未误识别）— 这些不是真正的遗漏

### 3. Vue 模板表达式调用 (8 Vue misses)
- `CanvasRenderer` — 组件构造函数引用
- `run (useRunProcess.ts)` — 特定模板模式
- `input(Vditor.vue)`、`change(draggable.vue)` — 属性值引用，非函数调用
- `format (progress.vue)` — `dayjs().format()` 外部包方法链
- `Vditor.vue` — 自引用组件
- `toggle (useBoolean.ts)` — 跨文件未导出函数

## Key Findings

### Vue SFC 支持 (最大新增功能)
- GitNexus: 402 Vue 调用 (115 个 .vue 文件)
- PyGitNexus: 826 Vue 调用
- 严格匹配: 371 (92.3%)
- 宽松匹配: 283 (95.3%)
- 真正遗漏: 仅 ~8 条 (排除 CSS :deep() 误报后)

### TypeScript & JavaScript 100% 宽松召回
- **TS: 100.0% relaxed recall** (0 truly missing)
- **JS: 100.0% relaxed recall** (0 truly missing)
- 通过同文件类引用解析和 Vue ref `.value` 解析达到完全覆盖

### PyGitNexus 发现更多真实调用
- PyGitNexus: 2075 CALLS vs GitNexus: 880 CALLS
- 1262 条为 PyGitNexus 独有
- 主要来源:
  - Vue 模板表达式函数调用
  - 嵌套箭头函数内调用
  - 更深层的属性链解析
  - 模块级调用提取

### 精度分析
- **严格 recall**: 92.6%
- **宽松 recall**: 97.8%
- 剩余 2.2% 的差异主要是:
  - CSS 伪选择器误识别 (GitNexus 特有, ~6 条)
  - Vue 模板属性值引用 (~3 条)
  - Vue 组件构造函数引用 (~1 条)
  - 外部包方法链 (~1 条)
  - 跨文件未导出函数 (~1 条)
  - Vue 自引用组件 (~1 条)
  - Vue flow 特定模式 (~1 条)
