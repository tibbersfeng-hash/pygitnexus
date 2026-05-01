#!/usr/bin/env python3
"""GitNexus vs PyGitNexus 调用链对比测试。

用法:
    python compare_calls.py /path/to/java/project

可选环境变量:
    PYGITNEXUS_CLI  PyGitNexus CLI 路径 (默认: pygitnexus)
    GITNEXUS_CLI    GitNexus CLI 路径   (默认: gitnexus)
    OUTPUT_DIR      输出目录             (默认: ./comparison_output)
    SKIP_GN         跳过 GitNexus 分析    (默认: 0)
    SKIP_PY         跳过 PyGitNexus 分析  (默认: 0)

输出:
    - {output_dir}/summary.txt         汇总报告
    - {output_dir}/gitnexus_calls.tsv  GitNexus 全量调用
    - {output_dir}/pygitnexus_calls.tsv  PyGitNexus 全量调用
    - {output_dir}/only_gn.tsv         仅 GitNexus 有的调用
    - {output_dir}/only_py.tsv         仅 PyGitNexus 有的调用
    - {output_dir}/both.tsv            双方共有的调用
    - {output_dir}/method_match.tsv    方法级匹配统计
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


# ─── 配置 ──────────────────────────────────────────────────────────────

PYGITNEXUS_CLI = os.environ.get("PYGITNEXUS_CLI", "pygitnexus")
GITNEXUS_CLI = os.environ.get("GITNEXUS_CLI", "gitnexus")
DEFAULT_OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./comparison_output")
SKIP_GN = os.environ.get("SKIP_GN", "0") == "1"
SKIP_PY = os.environ.get("SKIP_PY", "0") == "1"

# GitNexus 假阳性模式识别 — 已知 JDK/第三方库方法会被误匹配到项目业务方法
FALSE_POSITIVE_PATTERNS = [
    # (target_method, description)
    ("get", "JDK Map/Optional/JsonNode/List.get() 误匹配到项目 get() 方法"),
    ("error", "Logger.error() 误匹配到 ApiResponse.error() 等业务方法"),
    ("stream", "Java Stream API .stream() 误匹配到项目 stream() 方法"),
    ("getMessage", "Exception.getMessage() 误匹配到项目 getMessage() 方法"),
    ("getData", "Optional/Map.getData() 误匹配"),
    ("getUptime", "OperatingSystem.getUptime() 误匹配"),
    ("getOrDefault", "Map.getOrDefault() 误匹配"),
    ("warn", "Logger.warn() 误匹配到项目 warn() 方法"),
    ("info", "Logger.info() 误匹配到项目 info() 方法"),
    ("debug", "Logger.debug() 误匹配到项目 debug() 方法"),
    ("toString", "Object.toString() 误匹配"),
    ("equals", "Object.equals() 误匹配"),
    ("hashCode", "Object.hashCode() 误匹配"),
    ("compareTo", "Comparable.compareTo() 误匹配"),
]

# JDK 方法白名单 — 这些调用不应匹配到项目业务方法
JDK_METHODS = {
    "toString", "equals", "hashCode", "getClass", "notify", "notifyAll",
    "wait", "clone", "finalize",
    "get", "put", "remove", "contains", "containsKey", "containsValue",
    "size", "isEmpty", "clear", "keySet", "values", "entrySet",
    "add", "addAll", "indexOf", "lastIndexOf",
    "stream", "parallelStream", "iterator", "spliterator",
    "forEach", "replaceAll", "sort",
    "getOrDefault", "computeIfAbsent", "computeIfPresent", "merge",
    "error", "warn", "info", "debug", "trace",
    "getMessage", "getCause", "printStackTrace",
    "orElse", "orElseGet", "orElseThrow", "isPresent", "isEmpty",
    "compareTo", "compare",
    "length", "charAt", "substring", "startsWith", "endsWith",
    "trim", "split", "replace", "toLowerCase", "toUpperCase",
    "valueOf", "parseInt", "parseLong", "parseDouble",
    "now", "of", "from", "toInstant", "atZone", "getEpochSecond",
}


# ─── 工具函数 ─────────────────────────────────────────────────────────

import tempfile

def run_cmd(cmd: list[str], cwd: Path | None = None, timeout: int = 1200) -> tuple[int, str, str]:
    """执行命令并返回 (returncode, stdout, stderr)。

    Uses temp files to capture large output (>64KB) since subprocess
    pipe buffers are limited to 64KB in some environments.
    """
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.out', delete=False) as stdout_f, \
         tempfile.NamedTemporaryFile(mode='w+', suffix='.err', delete=False) as stderr_f:
        stdout_path = stdout_f.name
        stderr_path = stderr_f.name

    try:
        with open(stdout_path, 'w') as stdout_f, open(stderr_path, 'w') as stderr_f:
            result = subprocess.run(
                cmd, stdout=stdout_f, stderr=stderr_f,
                cwd=cwd, timeout=timeout
            )

        with open(stdout_path, 'r') as f:
            stdout = f.read()
        with open(stderr_path, 'r') as f:
            stderr = f.read()

        return result.returncode, stdout, stderr
    finally:
        import os
        os.unlink(stdout_path)
        os.unlink(stderr_path)


def _parse_gn_markdown(text: str) -> list[dict]:
    """解析 GitNexus 的 markdown 表格输出为 dict 列表。

    GitNexus 输出格式:
    | c.name | t.name |
    | --- | --- |
    | broadcast | toJsonMessage |
    | notifyStatusChange | broadcast |
    """
    lines = text.strip().split("\n")
    if not lines:
        return []

    # Find the header row (first line starting with |)
    header_line = None
    sep_line_idx = None
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("|") and "---" in line and "|" in line:
            sep_line_idx = i
            break

    if sep_line_idx is None or sep_line_idx < 1:
        return []

    header_line = lines[sep_line_idx - 1].strip()
    headers = [h.strip() for h in header_line.strip("|").split("|")]

    data_lines = lines[sep_line_idx + 1:]
    results = []
    for line in data_lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        values = [v.strip() for v in line.strip("|").split("|")]
        if len(values) == len(headers):
            results.append(dict(zip(headers, values)))

    return results


def _find_gn_repo(project: Path) -> str | None:
    """Find the correct GitNexus repo identifier for the given project path."""
    rc, out, err = run_cmd([GITNEXUS_CLI, "list"])
    if rc != 0:
        return None

    # Parse the output to find matching repo
    project_abs = str(project.resolve())
    for line in out.split("\n"):
        if project_abs in line:
            # Extract the identifier from the line
            # Format: "alias (path)" or just "alias"
            # Try matching by path first
            match = re.search(r'([^\s(]+)\s*\(([^)]+)\)', line)
            if match:
                alias = match.group(1)
                path = match.group(2)
                if project_abs == path or project_abs.endswith(path):
                    return path
            # Try the full line as alias
            parts = line.strip().split()
            if parts:
                return parts[0]
    return None


def _shard_count(gitnexus_cli: str, repo: str, low: str, high: str) -> int:
    """Get the actual COUNT of edges in a name range from GitNexus DB."""
    cypher = (
        f"MATCH (c:Method)-[r]->(t:Method) "
        f"WHERE c.name >= '{low}' AND c.name < '{high}' "
        f"RETURN count(r) as cnt"
    )
    rc, out, err = run_cmd([gitnexus_cli, "cypher", "--repo", repo, cypher])
    if rc != 0:
        return -1
    try:
        d = json.loads(out.strip())
        if isinstance(d, dict):
            md = d.get("markdown", "")
            parsed = _parse_gn_markdown(md)
            if parsed:
                return int(parsed[0].get("cnt", 0))
        elif isinstance(d, list) and d:
            return int(d[0].get("cnt", 0))
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    return -1


def _fetch_shard(gitnexus_cli: str, repo: str, low: str, high: str, depth: int = 0) -> list[dict]:
    """Fetch a single name-range shard, recursively subdividing if truncated."""
    indent = "    " + "  " * depth
    cypher = (
        f"MATCH (c:Method)-[r]->(t:Method) "
        f"WHERE c.name >= '{low}' AND c.name < '{high}' "
        f"RETURN c.name, t.name LIMIT 10000"
    )
    rc, out, err = run_cmd(
        [gitnexus_cli, "cypher", "--repo", repo, cypher],
    )
    if rc != 0:
        return []

    # Parse the response
    try:
        gn_response = json.loads(out.strip())
        if isinstance(gn_response, list):
            batch = gn_response
        elif isinstance(gn_response, dict):
            md = gn_response.get("markdown", "")
            batch = _parse_gn_markdown(md)
        else:
            batch = []
    except (json.JSONDecodeError, ValueError):
        batch = _parse_gn_markdown(out.strip())

    for row in batch:
        if isinstance(row, dict):
            if "c.name" in row:
                row["caller_method"] = row.pop("c.name")
            if "t.name" in row:
                row["target_method"] = row.pop("t.name")

    results = [r for r in batch if isinstance(r, dict)]

    # Check if we got fewer results than expected → truncated
    if depth < 3:
        actual_count = _shard_count(gitnexus_cli, repo, low, high)
        if actual_count > 0 and len(results) < min(actual_count, 10000):
            # Truncated or limited by LIMIT — subdivide
            sub_results = []
            for ch in "abcdefghijklmnopqrstuvwxyz":
                sub_low = low + ch
                sub_high = low + chr(ord(ch) + 1)
                sub_data = _fetch_shard(gitnexus_cli, repo, sub_low, sub_high, depth + 1)
                sub_results.extend(sub_data)
            return sub_results

    return results


def export_gitnexus_calls(project: Path, output_dir: Path) -> list[dict]:
    """通过 GitNexus CLI cypher 命令导出全部调用链。

    GitNexus 使用 KuzuDB 存储，Method 节点之间的边即为调用关系。
    输出格式为 markdown 表格，需要解析。

    Note: GitNexus has a 64KB stdout truncation issue for large projects.
    We use recursive name-range sharding to work around this.
    """
    if SKIP_GN:
        print("  [SKIP] 跳过 GitNexus 分析 (SKIP_GN=1)")
        return []

    print("  [1/3] 导出 GitNexus 调用数据...")

    # First, get total count
    count_cypher = "MATCH (c:Method)-[r]->(t:Method) RETURN count(r) as total"
    rc, out, err = run_cmd(
        [GITNEXUS_CLI, "cypher", "--repo", str(project.resolve()), count_cypher],
        cwd=project,
    )
    if rc != 0:
        print(f"    GitNexus count query 失败: {err[:300]}")
        return []

    try:
        gn_response = json.loads(out.strip())
        md = gn_response.get("markdown", "")
        parsed = _parse_gn_markdown(md)
        total = int(parsed[0].get("total", 0)) if parsed else 0
    except (json.JSONDecodeError, ValueError, KeyError):
        print(f"    GitNexus count parse 失败: {out[:200]}")
        return []

    if total == 0:
        print("    GitNexus 返回 0 条调用")
        return []

    print(f"    GitNexus 总调用数: {total}，分片查询中...")

    # Initial shards: a-z, 0-9, _
    initial_shards = []
    for ch in "abcdefghijklmnopqrstuvwxyz":
        initial_shards.append((ch, chr(ord(ch) + 1)))
    initial_shards.append(("0", ":"))
    initial_shards.append(("_", "`"))

    all_data = []
    repo = str(project.resolve())

    for low, high in initial_shards:
        batch = _fetch_shard(GITNEXUS_CLI, repo, low, high)
        if batch:
            all_data.extend(batch)
            print(f"    已获取 {len(all_data)}/{total} 条... (shard '{low}')")

    print(f"  [3/3] 获得 {len(all_data)} 条 GitNexus 调用")
    return all_data


def export_pygitnexus_calls(project: Path, output_dir: Path) -> list[dict]:
    """通过 PyGitNexus CLI cypher 命令导出全部调用链。

    PyGitNexus 使用 CodeRelation {type: 'CALLS'} 边表示调用关系。
    输出格式为 JSON。
    """
    if SKIP_PY:
        print("  [SKIP] 跳过 PyGitNexus 分析 (SKIP_PY=1)")
        return []

    print("  [1/3] 运行 PyGitNexus 分析...")
    # Clean up entire .pygitnexus directory to avoid re-analysis errors
    existing_dir = project / ".pygitnexus"
    if existing_dir.exists():
        import shutil
        shutil.rmtree(existing_dir, ignore_errors=True)

    rc, out, err = run_cmd(
        [PYGITNEXUS_CLI, "analyze", str(project), "--force"],
        cwd=project,
    )
    if rc != 0:
        print(f"    PyGitNexus analyze 失败: {err[:300]}")
        return []

    # 导出所有 CALLS 关系
    print("  [2/3] 导出 PyGitNexus 调用数据...")
    cypher = (
        "MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(target) "
        "RETURN caller.name as caller_method, "
        "       caller.className as caller_class, "
        "       target.name as target_method, "
        "       r.confidence as confidence "
        "ORDER BY caller.name, target.name"
    )
    rc, out, err = run_cmd(
        [PYGITNEXUS_CLI, "cypher", cypher, "--json"],
        cwd=project,
    )
    if rc != 0:
        print(f"    PyGitNexus cypher 失败: {err[:300]}")
        return []

    try:
        data = json.loads(out.strip())
    except json.JSONDecodeError:
        print(f"    PyGitNexus JSON 解析失败: {out[:300]}")
        return []

    print(f"  [3/3] 获得 {len(data)} 条 PyGitNexus 调用")
    return data


# ─── 数据规范化 ────────────────────────────────────────────────────────

def normalize_call_no_line(call: dict) -> tuple[str, str]:
    """将调用记录规范化为 (caller_method, target_method) 二元组。"""
    caller = (call.get("caller_method") or "").strip()
    target = (call.get("target_method") or "").strip()
    return (caller, target)


# ─── 假阳性识别 ────────────────────────────────────────────────────────

def classify_false_positive(target_method: str) -> str | None:
    """判断某条调用是否为已知的假阳性模式。

    返回假阳性描述字符串，如果不是假阳性则返回 None。
    """
    for pattern_name, description in FALSE_POSITIVE_PATTERNS:
        if target_method == pattern_name:
            return description
    return None


def is_jdk_method(method_name: str) -> bool:
    """判断是否为 JDK 标准库方法。"""
    return method_name in JDK_METHODS


# ─── 对比分析 ─────────────────────────────────────────────────────────

def compare_calls(
    gn_calls: list[dict], py_calls: list[dict]
) -> dict:
    """对比 GitNexus 和 PyGitNexus 的调用链。

    返回包含以下 key 的字典:
    - gn_total, py_total: 原始总数
    - gn_effective, py_effective: 去除假阳性后的有效数
    - gn_false_positives, py_false_positives: 假阳性列表
    - both: 双方共有
    - only_gn: 仅 GitNexus
    - only_py: 仅 PyGitNexus
    - method_match: 方法级匹配统计
    """
    # 规范化
    gn_set = set(normalize_call_no_line(c) for c in gn_calls)
    py_set = set(normalize_call_no_line(c) for c in py_calls)

    both_set = gn_set & py_set
    only_gn_set = gn_set - py_set
    only_py_set = py_set - gn_set

    # 假阳性分类
    # 假阳性分类 — refined: only flag as FP when the call is NOT in both tools.
    # If both tools detect the same (caller, target) pair, it's likely a legitimate call
    # (e.g., ApiResponse.error() is a real project method both tools find).
    # The actual FP is when only one tool has the call with a generic target name.
    both_pairs = gn_set & py_set

    gn_fp = []
    for c in gn_calls:
        caller, target = normalize_call_no_line(c)
        pair = (caller, target)
        if pair in both_pairs:
            continue  # Both tools agree, treat as legitimate
        fp_type = classify_false_positive(target)
        if fp_type:
            gn_fp.append((caller, target, fp_type))

    py_fp = []
    for c in py_calls:
        caller, target = normalize_call_no_line(c)
        pair = (caller, target)
        if pair in both_pairs:
            continue  # Both tools agree, treat as legitimate
        fp_type = classify_false_positive(target)
        if fp_type:
            py_fp.append((caller, target, fp_type))

    gn_fp_set = set((c, t) for c, t, _ in gn_fp)
    py_fp_set = set((c, t) for c, t, _ in py_fp)

    gn_effective = len(gn_set) - len(gn_fp_set & gn_set)
    py_effective = len(py_set) - len(py_fp_set & py_set)

    # 方法级匹配
    gn_methods: dict[str, set[str]] = defaultdict(set)  # caller -> set of targets
    py_methods: dict[str, set[str]] = defaultdict(set)

    for caller, target in gn_set:
        gn_methods[caller].add(target)
    for caller, target in py_set:
        py_methods[caller].add(target)

    all_methods = set(gn_methods.keys()) | set(py_methods.keys())

    method_match = {
        "exact": 0,       # 调用目标完全一致
        "py_superset": 0, # PY 包含 GN 全部 + 更多
        "gn_superset": 0, # GN 包含 PY 全部 + 更多
        "partial": 0,     # 双方都有对方没有的
        "only_py": 0,     # 仅 PY 有调用
        "only_gn": 0,     # 仅 GN 有调用
    }

    for method in all_methods:
        gn_targets = gn_methods.get(method, set())
        py_targets = py_methods.get(method, set())

        if gn_targets and py_targets:
            if gn_targets == py_targets:
                method_match["exact"] += 1
            elif py_targets > gn_targets:
                method_match["py_superset"] += 1
            elif gn_targets > py_targets:
                method_match["gn_superset"] += 1
            else:
                method_match["partial"] += 1
        elif py_targets:
            method_match["only_py"] += 1
        elif gn_targets:
            method_match["only_gn"] += 1

    return {
        "gn_total": len(gn_set),
        "py_total": len(py_set),
        "gn_effective": gn_effective,
        "py_effective": py_effective,
        "gn_false_positives": gn_fp,
        "py_false_positives": py_fp,
        "both": sorted(both_set),
        "only_gn": sorted(only_gn_set),
        "only_py": sorted(only_py_set),
        "method_match": method_match,
        "all_methods": all_methods,
        "gn_methods": dict(gn_methods),
        "py_methods": dict(py_methods),
    }


# ─── 报告生成 ─────────────────────────────────────────────────────────

def write_tsv(path: Path, header: list[str], rows: list[tuple]):
    """写入 TSV 文件。"""
    with open(path, "w") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join(str(x) for x in row) + "\n")


def generate_report(result: dict, output_dir: Path, project: Path):
    """生成完整的对比报告和 TSV 文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 写入全量调用 TSV
    write_tsv(
        output_dir / "both.tsv",
        ["caller_method", "target_method"],
        [(c, t) for c, t in result["both"]],
    )
    write_tsv(
        output_dir / "only_gn.tsv",
        ["caller_method", "target_method"],
        [(c, t) for c, t in result["only_gn"]],
    )
    write_tsv(
        output_dir / "only_py.tsv",
        ["caller_method", "target_method"],
        [(c, t) for c, t in result["only_py"]],
    )

    # 写入假阳性详情
    write_tsv(
        output_dir / "gn_false_positives.tsv",
        ["caller_method", "target_method", "fp_type"],
        result["gn_false_positives"],
    )
    write_tsv(
        output_dir / "py_false_positives.tsv",
        ["caller_method", "target_method", "fp_type"],
        result["py_false_positives"],
    )

    # 写入全量调用（按工具分列）
    write_tsv(
        output_dir / "gitnexus_calls.tsv",
        ["caller_method", "target_method"],
        [(c, t) for c, t in sorted(set(normalize_call_no_line(c) for c in result.get("_gn_raw", [])))]
        if result.get("_gn_raw") else [],
    )
    write_tsv(
        output_dir / "pygitnexus_calls.tsv",
        ["caller_method", "target_method"],
        [(c, t) for c, t in sorted(set(normalize_call_no_line(c) for c in result.get("_py_raw", [])))]
        if result.get("_py_raw") else [],
    )

    # 写入方法级匹配
    write_tsv(
        output_dir / "method_match.tsv",
        ["match_type", "count"],
        [(k, v) for k, v in result["method_match"].items()],
    )

    # 生成摘要报告
    summary_path = output_dir / "summary.txt"
    lines: list[str] = []

    def add(text: str = ""):
        lines.append(text)

    add("=" * 70)
    add(f"GitNexus vs PyGitNexus 调用链对比报告")
    add(f"测试项目: {project}")
    add("=" * 70)
    add()

    # 总体数据
    add("## 总体数据")
    add()
    add(f"| 指标 | GitNexus | PyGitNexus |")
    add(f"|------|----------|-----------|")
    add(f"| 原始调用总数 | {result['gn_total']} | {result['py_total']} |")
    add(f"| 假阳性数 | {len(result['gn_false_positives'])} | {len(result['py_false_positives'])} |")
    gn_fp_rate = len(result['gn_false_positives']) / result['gn_total'] * 100 if result['gn_total'] > 0 else 0
    py_fp_rate = len(result['py_false_positives']) / result['py_total'] * 100 if result['py_total'] > 0 else 0
    add(f"| 假阳性率 | {gn_fp_rate:.1f}% | {py_fp_rate:.1f}% |")
    add(f"| **有效调用** | **{result['gn_effective']}** | **{result['py_effective']}** |")
    add()

    # 交集/差集
    add("## 交集与差集")
    add()
    add(f"- 双方共有: {len(result['both'])} 条")
    add(f"- 仅 GitNexus: {len(result['only_gn'])} 条")
    add(f"- 仅 PyGitNexus: {len(result['only_py'])} 条")
    add()

    # 假阳性分类
    add("## GitNexus 假阳性分类")
    add()
    if result["gn_false_positives"]:
        fp_counts: dict[str, int] = defaultdict(int)
        for _, _, fp_type in result["gn_false_positives"]:
            fp_counts[fp_type] += 1
        add(f"| 假阳性模式 | 数量 | 占比 |")
        add(f"|-----------|------|------|")
        total_fp = len(result["gn_false_positives"])
        for fp_type, count in sorted(fp_counts.items(), key=lambda x: -x[1]):
            pct = count / total_fp * 100 if total_fp > 0 else 0
            add(f"| {fp_type} | {count} | {pct:.1f}% |")
        add()
    else:
        add("无已知假阳性模式")
        add()

    add("## PyGitNexus 假阳性分类")
    add()
    if result["py_false_positives"]:
        fp_counts = defaultdict(int)
        for _, _, fp_type in result["py_false_positives"]:
            fp_counts[fp_type] += 1
        add(f"| 假阳性模式 | 数量 | 占比 |")
        add(f"|-----------|------|------|")
        total_fp = len(result["py_false_positives"])
        for fp_type, count in sorted(fp_counts.items(), key=lambda x: -x[1]):
            pct = count / total_fp * 100 if total_fp > 0 else 0
            add(f"| {fp_type} | {count} | {pct:.1f}% |")
        add()
    else:
        add("无已知假阳性模式")
        add()

    # 方法级匹配
    add("## 方法级匹配")
    add()
    mm = result["method_match"]
    add(f"| 匹配类型 | 数量 | 说明 |")
    add(f"|---------|------|------|")
    add(f"| 完全匹配 | {mm['exact']} | 调用目标完全一致 |")
    add(f"| PyGitNexus 超集 | {mm['py_superset']} | PY 包含 GN 全部调用 + 额外调用 |")
    add(f"| GitNexus 超集 | {mm['gn_superset']} | GN 包含 PY 全部调用 + 额外调用 |")
    add(f"| 部分重叠 | {mm['partial']} | 双方都有对方没有的调用 |")
    add(f"| 仅 PyGitNexus | {mm['only_py']} | GN 完全没有检测到这些方法 |")
    add(f"| 仅 GitNexus | {mm['only_gn']} | PY 完全没有检测到这些方法 |")
    add()

    # 仅 GitNexus 有的调用（按假阳性和真阳性分类）
    add("## 仅 GitNexus 有的调用详情")
    add()
    gn_fp_call_set = set((c, t) for c, t, _ in result["gn_false_positives"])
    gn_only_fp = [(c, t) for c, t in result["only_gn"] if (c, t) in gn_fp_call_set]
    gn_only_tp = [(c, t) for c, t in result["only_gn"] if (c, t) not in gn_fp_call_set]

    add(f"### 已知假阳性 ({len(gn_only_fp)} 条)")
    add()
    if gn_only_fp:
        add(f"| 调用方 | 目标方法 |")
        add(f"|--------|---------|")
        for c, t in gn_only_fp[:50]:
            add(f"| {c} | {t} |")
        if len(gn_only_fp) > 50:
            add(f"| ... | 还有 {len(gn_only_fp) - 50} 条 |")
        add()

    add(f"### 可能为真阳性 ({len(gn_only_tp)} 条)")
    add()
    if gn_only_tp:
        add(f"| 调用方 | 目标方法 |")
        add(f"|--------|---------|")
        for c, t in gn_only_tp[:50]:
            add(f"| {c} | {t} |")
        if len(gn_only_tp) > 50:
            add(f"| ... | 还有 {len(gn_only_tp) - 50} 条 |")
        add()

    # 仅 PyGitNexus 有的调用
    add("## 仅 PyGitNexus 有的调用详情")
    add()
    add(f"共 {len(result['only_py'])} 条")
    add()
    if result["only_py"]:
        add(f"| 调用方 | 目标方法 |")
        add(f"|--------|---------|")
        for c, t in result["only_py"][:50]:
            add(f"| {c} | {t} |")
        if len(result["only_py"]) > 50:
            add(f"| ... | 还有 {len(result['only_py']) - 50} 条 |")
        add()

    # 质量评估
    add("## 质量评估")
    add()
    if result['gn_effective'] > 0:
        coverage = result['py_effective'] / result['gn_effective'] * 100
    else:
        coverage = 100

    if coverage >= 100 and py_fp_rate == 0:
        grade = "优秀"
    elif coverage >= 95 and py_fp_rate <= 1:
        grade = "良好"
    elif coverage >= 90 and py_fp_rate <= 5:
        grade = "合格"
    else:
        grade = "需改进"

    add(f"- PyGitNexus 覆盖率: {coverage:.1f}% (有效调用 {result['py_effective']} / {result['gn_effective']})")
    add(f"- PyGitNexus 假阳性率: {py_fp_rate:.1f}%")
    add(f"- 质量评级: **{grade}**")
    add()

    with open(summary_path, "w") as f:
        f.write("\n".join(lines))

    return summary_path


# ─── 主入口 ────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} /path/to/java/project")
        print()
        print("环境变量:")
        print(f"  PYGITNEXUS_CLI  PyGitNexus CLI 路径 (默认: {PYGITNEXUS_CLI})")
        print(f"  GITNEXUS_CLI    GitNexus CLI 路径   (默认: {GITNEXUS_CLI})")
        print(f"  OUTPUT_DIR      输出目录             (默认: {DEFAULT_OUTPUT_DIR})")
        print(f"  SKIP_GN         跳过 GitNexus 分析    (默认: 0)")
        print(f"  SKIP_PY         跳过 PyGitNexus 分析  (默认: 0)")
        sys.exit(1)

    project = Path(sys.argv[1]).resolve()
    if not project.exists():
        print(f"错误: 项目路径不存在: {project}")
        sys.exit(1)

    output_dir = Path(DEFAULT_OUTPUT_DIR).resolve()
    print(f"测试项目: {project}")
    print(f"输出目录: {output_dir}")
    print()

    # Step 1: 导出 GitNexus 调用
    print("=" * 50)
    print("Step 1: 导出 GitNexus 调用链")
    print("=" * 50)
    gn_calls = export_gitnexus_calls(project, output_dir)
    print()

    # Step 2: 导出 PyGitNexus 调用
    print("=" * 50)
    print("Step 2: 导出 PyGitNexus 调用链")
    print("=" * 50)
    py_calls = export_pygitnexus_calls(project, output_dir)
    print()

    if not gn_calls and not py_calls:
        print("错误: 两个工具都没有导出调用数据")
        sys.exit(1)

    # Step 3: 对比分析
    print("=" * 50)
    print("Step 3: 对比分析")
    print("=" * 50)
    result = compare_calls(gn_calls, py_calls)
    # 保留原始数据用于 TSV 输出
    result["_gn_raw"] = gn_calls
    result["_py_raw"] = py_calls
    print()

    # Step 4: 生成报告
    print("=" * 50)
    print("Step 4: 生成报告")
    print("=" * 50)
    summary = generate_report(result, output_dir, project)
    print()

    # 打印摘要
    print("=" * 50)
    print("对比摘要")
    print("=" * 50)
    mm = result["method_match"]
    print(f"GitNexus 原始调用:  {result['gn_total']}")
    print(f"PyGitNexus 原始调用: {result['py_total']}")
    print(f"GitNexus 假阳性:    {len(result['gn_false_positives'])}")
    print(f"PyGitNexus 假阳性:   {len(result['py_false_positives'])}")
    print(f"GitNexus 有效调用:  {result['gn_effective']}")
    print(f"PyGitNexus 有效调用: {result['py_effective']}")
    print(f"双方共有:           {len(result['both'])}")
    print(f"仅 GitNexus:        {len(result['only_gn'])}")
    print(f"仅 PyGitNexus:      {len(result['only_py'])}")
    print()
    print(f"报告已保存至: {summary}")


if __name__ == "__main__":
    main()
