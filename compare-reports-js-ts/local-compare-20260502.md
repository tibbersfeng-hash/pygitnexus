# GitNexus vs PyGitNexus JS/TS 对比报告
**时间**: 2026-05-02 03:30
**环境**: 本地项目（GitHub 克隆不可用）

## 1. JS 小型项目 (life-death/frontend, 13 .js 文件)

### 查询结果对比
| 查询项 | GitNexus | PyGitNexus | 匹配 |
|--------|----------|------------|------|
| File count | 13 | 13 | ✅ |
| Class count | 1 | 1 | ✅ |
| Method count | 87 | 269 | ❌ |
| Function count (GN only) | 183 | 0 | ❌ |
| Interface count | 0 | 0 | ✅ |
| Const count (GN only) | 440 | 0 | ❌ |
| Constructor count | N/A | 1 | ❌ |
| Total relations | 1536 | 1704 | ❌ |

### 差异分析
- **Method 差异 (87 vs 269)**: GitNexus 将类方法 (Method: 87) 和独立函数 (Function: 183) 分开存储，总计 270。PyGitNexus 将它们合并为 Method (269)，差 1 个。
- **Const 缺失**: PyGitNexus 没有 Const 节点类型（GitNexus: 440 个）
- **File 匹配**: 13 vs 13 ✅
- **Class 匹配**: 1 vs 1 ✅

### 准确率: 3/6 核心查询匹配 (50%)

---

## 2. TS 小型项目 (cc-connect/web, 47 .ts/.tsx 文件)

### 查询结果对比
| 查询项 | GitNexus | PyGitNexus | 匹配 |
|--------|----------|------------|------|
| File count | 53 | 47 | ❌ |
| Class count | 2 | 2 | ✅ |
| Method count | 10 | 170 | ❌ |
| Interface count | 44 | 44 | ✅ |
| Constructor count | 0 | 1 | ❌ |
| Total relations | 1172 | 2068 | ❌ |

### 差异分析
- **File 差异 (53 vs 47)**: GitNexus 包含 .css (index.css) 和 .json 语言文件 (6个)，PyGitNexus 仅分析 .ts/.tsx 源文件
- **Method 差异 (10 vs 170)**: GitNexus 仅将类内方法计为 Method (10)，而将函数声明、箭头函数等计为 Function。PyGitNexus 将所有函数/方法统一计为 Method (170)
- **Interface 匹配**: 44 vs 44 ✅ 完全一致
- **Class 匹配**: 2 vs 2 ✅ (ApiClient, ApiError)
- **Constructor 差异 (0 vs 1)**: GitNexus 将 TS 构造函数归入 Method，PyGitNexus 单独提取为 Constructor 节点
- **Relations 差异 (1172 vs 2068)**: 两种工具对关系类型的定义和计数方式不同

### 准确率: 2/6 核心查询匹配 (33%)

---

## 3. 总结

### 总体情况
| 项目 | 匹配数 | 总查询 | 匹配率 |
|------|--------|--------|--------|
| JS 小型 | 3 | 6 | 50% |
| TS 小型 | 2 | 6 | 33% |
| **综合** | **5** | **12** | **42%** |

### 已达成 98% 的匹配项
- File count (JS): ✅
- Class count (JS): ✅
- Interface count (JS): ✅
- Class count (TS): ✅
- Interface count (TS): ✅

### 需要修复的差异

1. **File 扫描范围不一致** (TS 项目)
   - GitNexus 扫描 .css/.json 等非源码文件
   - PyGitNexus 仅扫描 .js/.ts 系列
   - 解决方向：明确目标——是否应包含所有文件还是仅源码文件

2. **Method vs Function 分类差异**
   - GitNexus 分开存储 Method (类方法) 和 Function (独立函数)
   - PyGitNexus 统一存储为 Method
   - 解决方向：PyGitNexus 可增加 Function 节点类型，或统一查询口径

3. **Const 节点缺失**
   - GitNexus 提取 JavaScript const 声明为 Const 节点 (440个)
   - PyGitNexus 不支持 Const 节点
   - 解决方向：在 JS extractor 中增加 Const 提取

4. **Constructor 计数差异**
   - GitNexus 将 TS 构造函数归入 Method，JS 构造函数未单独统计
   - PyGitNexus 将 Constructor 作为独立节点
   - 解决方向：统一口径或增加对比查询

5. **Relations 计数差异**
   - 两种工具对边关系的定义不同
   - 解决方向：对齐关系类型定义

### 当前与 98% 目标的差距
- **目标**: 所有类别 >= 98% 匹配率
- **当前**: 综合匹配率 42%
- **差距**: 需要修复 5 类核心差异
