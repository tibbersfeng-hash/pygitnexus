# GitNexus vs PyGitNexus 功能差异对比

> 分析时间：2026-05-01
> GitNexus 版本：v1.6.3（TypeScript/Node.js）
> PyGitNexus：当前工作区版本

## 总览

| 维度 | GitNexus | PyGitNexus | 差异 |
|------|----------|-----------|------|
| 支持语言 | 16 种 | 仅 Java | PyGitNexus 专注 Java |
| 流水线阶段 | 13 个阶段 | 4 个阶段 | GitNexus 有大量后处理 |
| 节点类型 | 47 种 | 9 种 | GitNexus 覆盖广 |
| 关系类型 | 21 种 | 12 种 | GitNexus 覆盖广 |
| 跨文件解析 | 完整（import 排序 + 类型传播） | 名称匹配 | 精度差异 |
| 置信度评分 | 分层评分 | 固定 1.0 | 精度差异 |

---

## 1. 调用提取 — 遗漏的调用类型

### 1.1 `new SomeClass()` — 构造函数调用

**GitNexus**: 提取 `object_creation_expression`，生成 CALLS 边指向 Constructor 节点。
**PyGitNexus**: 仅提取 `method_invocation`，**未提取 `new` 调用**。

```java
// GitNexus 会提取，PyGitNexus 不会
ProcessInfo info = new ProcessInfo();
var handler = new WebSocketHandler();
```

**影响**: 项目中的对象创建调用链路完全丢失。

### 1.2 `this()` / `super()` — 构造函数内委托调用

**GitNexus**: 提取 `explicit_constructor_invocation`。
**PyGitNexus**: **未提取此模式**。

```java
// GitNexus 会提取，PyGitNexus 不会
public MyService() {
    this(config);  // 委托到另一个构造函数
}
```

### 1.3 `super.method()` — 父类方法调用

**GitNexus**: 将 `super` 识别为接收者（在 `SIMPLE_RECEIVER_TYPES` 中），走 MRO 解析到父类方法。
**PyGitNexus**: 提取为普通 `method_invocation`，但 `super` 不在 type_map 中，**receiver_type 为 null**，无法解析到父类方法。

```java
// 两者都能提取调用，但解析精度不同
super.notifyStatusChange(session);  // PyGitNexus 无法解析到父类实现
```

### 1.4 接口虚方法分发（Interface Dispatch）

**GitNexus**: 当调用目标为接口方法时，通过 MRO 查找所有实现类，为**每个实现类**生成一条 CALLS 边。
**PyGitNexus**: 仅匹配方法名，**不追踪接口的所有实现**。

```java
// 如果 SessionService 有 3 个实现类：
service.getSession();
// GitNexus → 4 条 CALLS 边（接口 + 3 个实现）
// PyGitNexus → 仅 1 条（名称匹配到的第一个）
```

---

## 2. 跨文件调用解析 — 精度差异

### 2.1 调用目标匹配机制

| | GitNexus | PyGitNexus |
|--|----------|-----------|
| 策略 | 分层解析（局部→import→owner→MRO→全局→兜底） | 按方法名全局匹配 |
| 接收者类型 | 通过 TypeEnv 精确推导 | per-method type_map（字段+参数+局部变量） |
| 重载区分 | 通过 arity（参数数量）+ typeTag | 不区分 |
| 置信度 | 分层评分（0.6-1.0） | 固定 1.0 |

**GitNexus 的 7 步查找**:
1. 词法绑定（局部变量）
2. 作用域绑定
3. import 解析
4. owner 解析（接收者类型）
5. MRO 遍历（继承链）
6. 全局搜索
7. 兜底

**PyGitNexus 的简化匹配**:
1. 通过 receiver 在 type_map 中查找类型
2. 找到类型后匹配方法名
3. 如果 receiver 为空或无法解析，直接按方法名全局匹配

**关键差距**：当两个类有同名方法时，PyGitNexus 可能匹配到错误的目标。

```java
// 假设 AService 和 BService 都有 getAlerts() 方法
AService a = ...;
a.getAlerts();
// PyGitNexus 可能匹配到 BService.getAlerts()（取决于匹配顺序）
// GitNexus 通过 AService 的类型精确匹配
```

### 2.2 跨文件类型传播

**GitNexus**: `BindingAccumulator` + `ExportedTypeMap` 实现跨文件类型传播：
- 如果 `x: User` 在文件 A 中定义
- 文件 B import 了 A，则 `x` 的类型传播到 B
- `x.save()` 能正确解析到 `User.save()`

**PyGitNexus**: 仅单文件内的 type_map，**不跨文件传播类型**。

---

## 3. 节点类型 — GitNexus 有但 PyGitNexus 缺失

| GitNexus 节点 | 说明 | PyGitNexus 状态 |
|---------------|------|----------------|
| `Record` | Java 16+ record 声明 | 未提取 |
| `Enum` | 枚举类型（独立节点） | 归入 Class |
| `Struct` | C/C++ 结构体 | 不适用（仅 Java） |
| `Trait` | Rust/Scala trait | 不适用 |
| `Impl` | Rust impl block | 不适用 |
| `TypeAlias` | 类型别名 | 未提取 |
| `Union` | 联合体 | 不适用 |
| `Macro` | 宏定义 | 不适用 |
| `Decorator` | 装饰器节点 | 未提取 |
| `Import` | import 语句（独立节点） | 仅作为关系 |
| `CodeElement` | 通用代码元素 | 未提取 |
| `Module` | 模块/包 | 未提取 |
| `Package` | Java 包声明 | 未提取 |
| `Namespace` | 命名空间 | 未提取 |
| `Community` | 社区检测结果 | 未实现 |
| `Process` | 执行流程 | 未实现 |
| `Route` | API 路由 | 未实现 |
| `Tool` | MCP 工具定义 | 未实现 |

---

## 4. 关系类型 — GitNexus 有但 PyGitNexus 缺失

| 关系类型 | 说明 | PyGitNexus 状态 |
|---------|------|----------------|
| `METHOD_OVERRIDES` | MRO 计算方法覆盖 | 未实现（无 MRO） |
| `METHOD_IMPLEMENTS` | 接口方法实现关系 | 未实现 |
| `STEP_IN_PROCESS` | 执行流程步骤 | 未实现 |
| `ENTRY_POINT_OF` | 流程入口点 | 未实现 |
| `FETCHES` | HTTP 消费者→路由 | 未实现 |
| `HANDLES_ROUTE` | 路由处理器 | 未实现 |
| `HANDLES_TOOL` | 工具处理器 | 未实现 |
| `WRAPS` | 包装关系 | 未实现 |
| `QUERIES` | ORM 查询关系 | 未实现 |
| `USES` | 类型引用依赖 | 未实现 |
| `DECORATES` | 装饰器关系 | 未实现 |
| `MEMBER_OF` | 社区成员关系 | 未实现 |

---

## 5. 解析细节差异

### 5.1 嵌套类

**GitNexus**: 支持嵌套类，通过 `ancestorScopeNodeTypes` 走 AST 构建限定名：
```java
class Outer {
    class Inner { }  // → com.example.Outer.Inner
}
```

**PyGitNexus**: `_find_enclosing_class` 能找到最内层包围类（通过字节范围比较），但**仅返回简单类名**，不构建 `Outer.Inner` 形式的限定名。嵌套类中的方法归属信息不完整。

### 5.2 Record 声明

**GitNexus**: 提取 `record_declaration` 节点，标签为 `Record`。
**PyGitNexus**: **未处理**，record 会被当作普通 class 提取（如果 tree-sitter 能解析）。

### 5.3 Compact Constructor（Record 构造函数）

**GitNexus**: 支持 `compact_constructor_declaration`（Java 16+ record 的紧凑构造函数）。
**PyGitNexus**: 仅处理 `constructor_declaration`，**不支持紧凑构造函数**。

### 5.4 方法参数元数据

| 属性 | GitNexus | PyGitNexus |
|------|----------|-----------|
| 参数名 | ✓ | ✓ |
| 简单类型 | ✓ | ✓ |
| **完整泛型类型（rawType）** | ✓ | ✗ |
| 是否可选 | ✓ | ✗ |
| 是否可变参数 | ✓ | ✗ |

### 5.5 方法返回类型

**GitNexus**: `extractReturnTypeFromField` 使用 `.text` 保留完整泛型（如 `List<User>`）。
**PyGitNexus**: 已实现泛型返回类型提取（上一轮修复），但仅在方法定义上存储简化的类型名。

### 5.6 方法注解

**GitNexus**: 提取方法注解（`@Override`, `@Autowired` 等）并存储为方法元数据。
**PyGitNexus**: 提取了注解数据（`pf.annotations`），但在 pipeline 中**被注释掉**，不写入图数据库：

```python
# pipeline.py 第 437 行
# Skip Annotation nodes — GitNexus only creates Annotation nodes for
# @interface definitions (custom annotation types), not annotation usages.
```

### 5.7 字段读写区分

**GitNexus**: 通过 `@assignment` 捕获区分读写，`ACCESSES` 边的 `reason` 属性为 `'read'` 或 `'write'`。
**PyGitNexus**: 有 `isWrite` 标志在 `FieldAccess` 模型中，ACCESSES 关系也有 `isWrite` 属性。**基本一致**。

### 5.8 继承链解析（MRO）

**GitNexus**: 计算完整的 MRO（Method Resolution Order），Python 用 C3 线性化，Java 用 first-wins 策略。支持菱形继承检测。
**PyGitNexus**: **无 MRO 计算**。EXTENDS 关系仅记录直接父类，不计算继承链。

### 5.9 静态导入

**GitNexus**: 处理 Java `import static pkg.Class.method`，通过 `resolveJvmWildcard` 解析通配符。
**PyGitNexus**: **未处理静态导入**，静态方法调用可能无法正确解析。

### 5.10 类型作为接收者启发式

**GitNexus**: 当接收者名首字母大写且 TypeEnv 中无绑定时，直接将其视为类型名（处理 `SomeClass.staticMethod()`）。
**PyGitNexus**: 有类似逻辑（在 call extraction 中检查 receiver 是否在 type_map 中），但**不如 GitNexus 精确**。

---

## 6. 高级功能 — GitNexus 有但 PyGitNexus 完全没有

### 6.1 社区检测（Community Detection）

GitNexus 使用 Leiden 算法对 CALLS 图进行社区检测，生成 `Community` 节点和 `MEMBER_OF` 关系。PyGitNexus 无此功能。

### 6.2 执行流程提取（Process Extraction）

GitNexus 从入口点（main 方法、路由处理器等）开始 BFS 遍历 CALLS 图，构建执行流程（`Process` 节点 + `STEP_IN_PROCESS` 关系）。PyGitNexus 无此功能。

### 6.3 路由检测（Route Detection）

GitNexus 检测多种框架的路由定义（Next.js、Express、Spring、Laravel 等），生成 `Route` 节点和 `HANDLES_ROUTE` 关系。PyGitNexus 无此功能。

### 6.4 HTTP 消费者追踪（Fetch Tracking）

GitNexus 追踪 `fetch()`、`axios.get()` 等 HTTP 客户端调用，生成 `FETCHES` 边连接到路由。PyGitNexus 无此功能。

### 6.5 ORM 查询检测

GitNexus 检测 Prisma、Supabase 等 ORM 查询。PyGitNexus 无此功能。

### 6.6 MCP 工具定义检测

GitNexus 检测 `@tool` 装饰器，生成 `Tool` 节点。PyGitNexus 无此功能。

### 6.7 Git Diff 影响分析

GitNexus 的 `detect_changes` 分析 git diff hunks 影响的符号和执行流程。PyGitNexus 无此功能。

### 6.8 混合搜索（BM25 + 语义向量）

GitNexus 有 BM25 关键词搜索 + 语义向量搜索的混合搜索。PyGitNexus 仅有 Cypher 查询。

### 6.9 多语言支持

GitNexus 支持 16 种语言。PyGitNexus 仅支持 Java。

### 6.10 响应形状不匹配检测（Shape Check）

GitNexus 检测 API 响应形状与消费者属性访问之间的不匹配。PyGitNexus 无此功能。

### 6.11 影响范围分析（Impact Analysis）

GitNexus 的 `impact` 工具分析修改某个符号的爆破半径，按距离分组（d=1/d=2/d=3）并评估风险。PyGitNexus 无此功能。

---

## 7. 核心调用提取功能对比总结

| 功能 | GitNexus | PyGitNexus | 优先级 |
|------|----------|-----------|--------|
| `method_invocation` | ✓ | ✓ | - |
| `object_creation_expression` (`new X()`) | ✓ | ✗ | **高** |
| `method_reference` (`X::method`) | ✓ | ✓ | - |
| `explicit_constructor_invocation` (`this()` / `super()`) | ✓ | ✗ | **中** |
| Lambda 参数类型推断 | ✓ | ✓（自定义实现） | - |
| 接收者类型解析 | ✓（7层） | ✓（简化） | 精度差异 |
| 接口虚方法分发 | ✓ | ✗ | **低** |
| MRO 继承链解析 | ✓ | ✗ | **低** |
| 跨文件类型传播 | ✓ | ✗ | **中** |
| 静态导入解析 | ✓ | ✗ | **低** |
| 重载区分（arity） | ✓ | ✗ | **中** |
| 置信度评分 | ✓ | ✗ | **低** |
| 嵌套类限定名 | ✓ | ✗ | **低** |
| Record 类型提取 | ✓ | ✗ | **低** |
| 紧凑构造函数 | ✓ | ✗ | **低** |
| 方法注解写入图 | ✓ | ✗（注释掉） | **中** |
| 完整泛型参数（rawType） | ✓ | ✗ | **中** |

---

## 8. 建议优先级

### P0 — 直接影响调用链完整性

1. **添加 `object_creation_expression` 提取**（`new SomeClass()` 调用）
2. **增强调用目标匹配**：当多个类有同名方法时，通过 receiver 的 receiver_type 精确匹配

### P1 — 提升解析精度

3. **跨文件类型传播**：import 后能使用外部类的字段/方法类型
4. **方法参数 rawType 存储**：保留完整泛型信息
5. **方法注解写入图**：取消 Annotation 节点的注释
6. **添加 `explicit_constructor_invocation` 提取**

### P2 — 功能完善

7. **嵌套类限定名**：祖先作用域遍历
8. **Record 类型提取**
9. **重载区分**：通过 arity 缩小匹配范围
10. **MRO 计算**：基础继承链解析

### P3 — 远期规划

11. 接口虚方法分发
12. 置信度评分
13. 静态导入解析
14. 紧凑构造函数
