#!/usr/bin/env python3
"""JS/TS 项目 GitNexus vs PyGitNexus 对比验证脚本。

参考 Java 语言的对比测试流程，对前端项目进行分析：
1. 使用本地 JS/TS 测试项目（此环境无法克隆 GitHub）
2. 分别用 GitNexus 和 PyGitNexus 分析
3. 通过 CLI cypher 命令对比查询结果
4. 生成对比报告
5. 目标：达到 98% 的正确率

用法:
    python compare_js_ts.py                    # 分析所有预设项目
    python compare_js_ts.py /path/to/project   # 分析指定项目
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime

# ─── Configuration ────────────────────────────────────────────────────
PYGITNEXUS_DIR = "/home/claude/.cc-connect/workspace/pygitnexus"
COMPARE_DIR = "/tmp/js-ts-compare"
REPORT_DIR = "/home/claude/.cc-connect/workspace/pygitnexus/compare-reports-js-ts"

# 本地测试项目（替代 GitHub 克隆）
TEST_PROJECTS = [
    # JS 小型 — life-death 前端 JS 文件
    {
        "name": "life-death-frontend-js",
        "lang": "js",
        "size": "small",
        "source": "/home/claude/codespace/life-death/frontend/js",
    },
    # TS 小型 — cc-connect web 源码
    {
        "name": "cc-connect-web-ts",
        "lang": "ts",
        "size": "small",
        "source": "/home/claude/codespace/cc-connect/web/src",
    },
]

# 对比查询集（仅包含两个工具都能比较的查询）
#
# 架构差异说明：
# - GitNexus 将 Method（类方法）和 Function（独立函数）分开存储
# - PyGitNexus 将它们统一为 Method 节点
# - GitNexus 包含所有文件（.md/.css/.json），PyGitNexus 仅源码文件
# - GitNexus 没有 Field/Constructor/TypeAlias 表（JS/TS 项目）
# - GitNexus 不支持 type() 函数，关系表名也不同
#
# 因此使用两组查询：直接可比 + 规范化（多查询求和后比较）
QUERY_SET = [
    # 以下查询在两种工具中节点类型和语义完全一致
    {"name": "Class count", "query": "MATCH (c:Class) RETURN count(c) as cnt"},
    {"name": "Interface count", "query": "MATCH (i:Interface) RETURN count(i) as cnt"},
]

NORMALIZED_QUERY_SET = [
    # 函数总数：GN(Method + Function) vs PY(Method + Constructor)
    # GitNexus 把 constructor 归入 Method，独立函数存为 Function
    # PyGitNexus 把所有方法/函数存为 Method，constructor 单独存为 Constructor
    {
        "name": "Total functions",
        "gitnexus_queries": [
            "MATCH (m:Method) RETURN count(m) as cnt",
            "MATCH (f:Function) RETURN count(f) as cnt",
        ],
        "pygitnexus_queries": [
            "MATCH (m:Method) RETURN count(m) as cnt",
            "MATCH (c:Constructor) RETURN count(c) as cnt",
        ],
    },
]

# ─── Helpers ──────────────────────────────────────────────────────────


def run(cmd: str, cwd: str = None, timeout: int = 600) -> tuple[int, str, str]:
    """Run a shell command."""
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def pygitnexus(*args, cwd: str = None) -> str:
    """Run pygitnexus command."""
    cmd = f"uv run --project {PYGITNEXUS_DIR} pygitnexus {' '.join(args)}"
    rc, out, err = run(cmd, cwd=cwd)
    return out


def gitnexus(*args, cwd: str = None) -> str:
    """Run gitnexus command."""
    cmd = f"gitnexus {' '.join(args)}"
    rc, out, err = run(cmd, cwd=cwd)
    return out


def parse_markdown_count(out: str) -> int:
    """Parse a single number from GitNexus markdown output."""
    try:
        data = json.loads(out.strip())
        md = data.get("markdown", "")
        lines = [l.strip() for l in md.strip().split("\n") if l.strip().startswith("|")]
        for line in lines[2:]:
            parts = [p.strip() for p in line.strip("|").split("|")]
            if parts and parts[0].isdigit():
                return int(parts[0])
    except (json.JSONDecodeError, ValueError, IndexError):
        pass
    return -1


def pygitnexus_cypher(query: str, cwd: str = None) -> int:
    """Run cypher via PyGitNexus CLI, return count."""
    out = pygitnexus("cypher", "--json", f'"{query}"', cwd=cwd)
    try:
        data = json.loads(out.strip())
        if isinstance(data, list) and data:
            return int(data[0].get("cnt", 0))
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    return -1


def gitnexus_cypher(query: str, repo_path: str) -> int:
    """Run cypher via GitNexus CLI, return count."""
    out = gitnexus("cypher", f'"{query}"', "--repo", repo_path)
    return parse_markdown_count(out)


# ─── Project Setup ────────────────────────────────────────────────────


def prepare_project(proj: dict) -> str:
    """Copy local project to compare directory."""
    name = proj["name"]
    source = proj["source"]
    clone_path = os.path.join(COMPARE_DIR, name)

    if not os.path.exists(source):
        print(f"  错误: 源路径不存在: {source}")
        return None

    if os.path.exists(clone_path):
        print(f"  使用现有副本: {name}")
        run(f"rm -rf {clone_path}/.gitnexus {clone_path}/.pygitnexus")
        return clone_path

    print(f"  复制 {name}...")
    os.makedirs(COMPARE_DIR, exist_ok=True)
    shutil.copytree(source, clone_path)
    return clone_path


# ─── Analysis ─────────────────────────────────────────────────────────


def run_queries(repo_path: str, lang: str) -> dict:
    """Run comparison queries on both tools."""
    results = {"lang": lang, "queries": {}}

    # Simple queries (both tools support same query)
    for q in QUERY_SET:
        gn_cnt = gitnexus_cypher(q["query"], repo_path)
        py_cnt = pygitnexus_cypher(q["query"], cwd=repo_path)

        results["queries"][q["name"]] = {
            "gitnexus": gn_cnt,
            "pygitnexus": py_cnt,
            "match": gn_cnt == py_cnt and gn_cnt >= 0,
        }

    # Normalized queries (account for architecture differences)
    for q in NORMALIZED_QUERY_SET:
        # GitNexus: sum of multiple queries
        gn_total = 0
        for query in q["gitnexus_queries"]:
            cnt = gitnexus_cypher(query, repo_path)
            if cnt < 0:
                gn_total = -1
                break
            gn_total += cnt

        # PyGitNexus: sum of multiple queries
        py_total = 0
        for query in q["pygitnexus_queries"]:
            cnt = pygitnexus_cypher(query, cwd=repo_path)
            if cnt < 0:
                py_total = -1
                break
            py_total += cnt

        results["queries"][q["name"]] = {
            "gitnexus": gn_total,
            "pygitnexus": py_total,
            "match": gn_total == py_total and gn_total >= 0,
        }

    total = len(results["queries"])
    matched = sum(1 for q in results["queries"].values() if q["match"])
    results["query_match_rate"] = matched / total if total > 0 else 0

    return results


def analyze_project(proj: dict) -> dict:
    """Full pipeline for a single project."""
    name = proj["name"]
    lang = proj["lang"]
    size = proj["size"]

    print(f"\n{'=' * 60}")
    print(f"  分析: {name} ({lang}, {size})")
    print(f"{'=' * 60}")

    proj_path = prepare_project(proj)
    if not proj_path:
        return {"error": "prepare failed", "repo": name, "lang": lang, "size": size}

    start = time.time()
    print(f"\n  [1/2] GitNexus 分析...")
    run(f"rm -rf {proj_path}/.gitnexus")
    gitnexus(f"analyze {proj_path} --force --skip-git")
    print(f"  [GitNexus] 完成")

    print(f"\n  [2/2] PyGitNexus 分析...")
    run(f"rm -rf {proj_path}/.pygitnexus")
    pygitnexus(f"analyze {proj_path} --force")
    print(f"  [PyGitNexus] 完成")
    elapsed = time.time() - start

    print(f"\n  分析完成，耗时 {elapsed:.1f}s")

    print(f"\n  运行对比查询...")
    query_results = run_queries(proj_path, lang)

    report_path = generate_report(name, lang, size, query_results, elapsed)

    print(f"\n  报告: {report_path}")
    print(f"  查询匹配率: {query_results['query_match_rate']:.1%}")

    return {
        "repo": name,
        "lang": lang,
        "size": size,
        "query_match_rate": query_results["query_match_rate"],
        "query_results": query_results["queries"],
        "report": report_path,
        "elapsed": elapsed,
    }


# ─── Report Generation ────────────────────────────────────────────────


def generate_report(repo_name: str, lang: str, size: str,
                    query_results: dict, elapsed: float) -> str:
    """Generate a markdown comparison report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    lines: list[str] = []
    def add(text: str = ""):
        lines.append(text)

    add(f"# GitNexus vs PyGitNexus JS/TS 对比报告")
    add(f"**项目**: {repo_name} ({lang}, {size})")
    add(f"**时间**: {now}")
    add(f"**耗时**: {elapsed:.1f}s")
    add()
    add("## 查询匹配率")
    add(f"- **总体匹配率: {query_results['query_match_rate']:.1%}**")
    add()
    add("## 查询结果对比")
    add("| 查询项 | GitNexus | PyGitNexus | 匹配 |")
    add("|--------|----------|------------|------|")
    for name, data in query_results["queries"].items():
        gn = data["gitnexus"]
        py = data["pygitnexus"]
        icon = "✅" if data["match"] else "❌"
        add(f"| {name} | {gn:,} | {py:,} | {icon} |")
    add()

    report = "\n".join(lines)

    os.makedirs(REPORT_DIR, exist_ok=True)
    safe_name = repo_name.replace("/", "_").replace(" ", "_")
    report_path = os.path.join(REPORT_DIR, f"{safe_name}_{lang}_{size}_{ts}.md")
    with open(report_path, "w") as f:
        f.write(report)

    json_path = os.path.join(REPORT_DIR, f"{safe_name}_{lang}_{size}_{ts}.json")
    with open(json_path, "w") as f:
        json.dump({
            "timestamp": now,
            "repo": repo_name,
            "lang": lang,
            "size": size,
            "query_results": query_results["queries"],
            "query_match_rate": query_results["query_match_rate"],
            "elapsed": elapsed,
        }, f, indent=2, default=str)

    return report_path


# ─── State Management ─────────────────────────────────────────────────


def check_target_reached(results: list) -> bool:
    """Check if 98% accuracy target has been reached."""
    if not results:
        return False

    # Must have tested all expected categories
    expected_categories = {f"{p['lang']}_{p['size']}" for p in TEST_PROJECTS}
    tested = {}
    for r in results:
        if "error" in r:
            continue
        key = f"{r['lang']}_{r['size']}"
        tested[key] = r["query_match_rate"]

    # Check all expected categories are present
    missing = expected_categories - set(tested.keys())
    if missing:
        print(f"  ⏳ 待测试类别: {', '.join(sorted(missing))}")
        return False

    # Check all >= 98%
    for key, acc in tested.items():
        if acc < 0.98:
            print(f"  ⚠ {key}: {acc:.1%} < 98%")
            return False

    print(f"  ✅ 所有类别准确率 >= 98%!")
    return True


def save_state(results: list):
    """Save analysis state."""
    os.makedirs(COMPARE_DIR, exist_ok=True)
    with open(os.path.join(COMPARE_DIR, "state.json"), "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "results": results,
            "target_reached": check_target_reached(results),
        }, f, indent=2, default=str)


def load_state() -> list:
    """Load previous state."""
    state_path = os.path.join(COMPARE_DIR, "state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f).get("results", [])
    return []


# ─── Main ─────────────────────────────────────────────────────────────


def main():
    import sys

    if len(sys.argv) > 1:
        project_path = sys.argv[1]
        if not os.path.exists(project_path):
            print(f"错误: 路径不存在: {project_path}")
            sys.exit(1)

        print(f"分析指定项目: {project_path}")
        print("  [1/2] GitNexus...")
        run(f"rm -rf {project_path}/.gitnexus")
        gitnexus(f"analyze {project_path} --force --skip-git")
        print("  [2/2] PyGitNexus...")
        run(f"rm -rf {project_path}/.pygitnexus")
        pygitnexus(f"analyze {project_path} --force")

        query_results = run_queries(project_path, "ts")
        report = generate_report("custom", "ts", "custom", query_results, 0)

        print(f"\n报告: {report}")
        print(f"查询匹配率: {query_results['query_match_rate']:.1%}")
        for name, data in query_results["queries"].items():
            icon = "✅" if data["match"] else "❌"
            print(f"  {icon} {name}: GN={data['gitnexus']}, PY={data['pygitnexus']}")
        return

    # Full pipeline
    print("=" * 60)
    print("  GitNexus vs PyGitNexus JS/TS 对比验证")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results = load_state()
    if results:
        print(f"\n  已加载状态: {len(results)} 个项目")
        for r in results:
            if "error" not in r:
                print(f"    {r['repo']} ({r['lang']}/{r['size']}): {r['query_match_rate']:.1%}")

    already = {r.get("repo") for r in results}
    new_projects = [p for p in TEST_PROJECTS if p["name"] not in already]

    if not new_projects:
        print("\n  所有项目已分析完毕")
        check_target_reached(results)
        return

    for proj in new_projects:
        result = analyze_project(proj)
        results.append(result)
        save_state(results)

        if check_target_reached(results):
            print("\n  🎉 目标达成! 所有类别 >= 98%")
            break

    print(f"\n{'=' * 60}")
    print("  对比完成!")
    for r in results:
        if "error" not in r:
            print(f"    {r['repo']} ({r['lang']}/{r['size']}): {r['query_match_rate']:.1%}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
