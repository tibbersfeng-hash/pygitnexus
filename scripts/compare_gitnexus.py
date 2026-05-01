#!/usr/bin/env python3
"""
GitNexus vs PyGitNexus 对比验证脚本

对 Apache ShenYu 项目进行分析，对比两个工具的：
1. 存储内容（节点数、关系数、各类符号数量）
2. 查询结果（相同查询的输出差异）
3. 生成对比报告
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import shutil
from pathlib import Path
from datetime import datetime

# ─── Configuration ────────────────────────────────────────────────────
REPO_URL = "https://github.com/apache/shenyu.git"
CLONE_DIR = "/tmp/shenyu-compare"
PYGITNEXUS_DIR = "/home/claude/.cc-connect/workspace/pygitnexus"
REPORT_DIR = "/home/claude/.cc-connect/workspace/pygitnexus/compare-reports"

# ─── Helpers ──────────────────────────────────────────────────────────

def run(cmd: str, cwd: str = None, timeout: int = 600000) -> tuple[int, str, str]:
    """Run a shell command, return (exit_code, stdout, stderr)."""
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def pygitnexus(*args, cwd: str = None) -> str:
    """Run pygitnexus command."""
    cmd = f"uv run --project {PYGITNEXUS_DIR} pygitnexus {' '.join(args)}"
    rc, out, err = run(cmd, cwd=cwd)
    if rc != 0 and err:
        print(f"  [pygitnexus warn] {err.strip()[:200]}")
    return out


def gitnexus(*args, cwd: str = None) -> str:
    """Run gitnexus command."""
    cmd = f"gitnexus {' '.join(args)}"
    rc, out, err = run(cmd, cwd=cwd)
    if rc != 0 and err:
        print(f"  [gitnexus warn] {err.strip()[:200]}")
    return out


def cypher_store(db_path: str, query: str) -> list[dict]:
    """Execute a Cypher query against a KuzuDB database."""
    try:
        import kuzu
        db = kuzu.Database(db_path)
        conn = kuzu.Connection(db)
        result = conn.execute(query)
        cols = result.get_column_names()
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append(dict(zip(cols, row)))
        conn.close()
        return rows
    except Exception as e:
        return [{"_error": str(e)}]


def count_gitnexus_nodes(db_path: str) -> dict:
    """Count GitNexus node types by querying the lbug file directly."""
    result = {}
    try:
        import kuzu
        # GitNexus uses lbug file directly
        db = kuzu.Database(db_path)
        conn = kuzu.Connection(db)
        # Get all table names
        tables_result = conn.execute("CALL TABLE_INFO()")
        cols = tables_result.get_column_names()
        while tables_result.has_next():
            row = tables_result.get_next()
            table_dict = dict(zip(cols, row))
            table_name = table_dict.get("Table Name", table_dict.get("table_name", ""))
            if table_name:
                count_result = conn.execute(f"SELECT count(*) FROM {table_name}")
                while count_result.has_next():
                    cnt = count_result.get_next()
                    result[table_name] = cnt[0] if isinstance(cnt, (list, tuple)) else cnt
        conn.close()
    except Exception as e:
        result["_error"] = str(e)
    return result


def count_pygitnexus_nodes(db_path: str) -> dict:
    """Count PyGitNexus node types."""
    result = {}
    try:
        import kuzu
        db = kuzu.Database(db_path)
        conn = kuzu.Connection(db)
        tables_result = conn.execute("CALL TABLE_INFO()")
        cols = tables_result.get_column_names()
        while tables_result.has_next():
            row = tables_result.get_next()
            table_dict = dict(zip(cols, row))
            table_name = table_dict.get("Table Name", table_dict.get("table_name", ""))
            if table_name:
                count_result = conn.execute(f"SELECT count(*) FROM {table_name}")
                while count_result.has_next():
                    cnt = count_result.get_next()
                    result[table_name] = cnt[0] if isinstance(cnt, (list, tuple)) else cnt
        conn.close()
    except Exception as e:
        result["_error"] = str(e)
    return result


# ─── Main Pipeline ────────────────────────────────────────────────────

def prepare_repo() -> str:
    """Clone or update the ShenYu repo."""
    if os.path.exists(CLONE_DIR) and os.path.exists(f"{CLONE_DIR}/.git"):
        print("  Updating existing clone...")
        run("git fetch --depth 1 origin main && git reset --hard origin/main", cwd=CLONE_DIR)
    else:
        print("  Cloning Apache ShenYu...")
        run(f"rm -rf {CLONE_DIR}")
        run(f"git clone --depth 1 --branch master {REPO_URL} {CLONE_DIR}")
    return CLONE_DIR


def analyze_with_gitnexus(repo_path: str) -> dict:
    """Analyze with GitNexus and return stats."""
    print("  [GitNexus] Starting analysis...")
    # Clean existing
    run(f"rm -rf {repo_path}/.gitnexus")
    out = gitnexus(f"analyze {repo_path} --language java")
    print(f"  [GitNexus] Output: {out.strip()[:300]}")

    db_path = f"{repo_path}/.gitnexus/lbug"
    stats = count_gitnexus_nodes(db_path)
    return {"db_path": db_path, "stats": stats, "raw_output": out}


def analyze_with_pygitnexus(repo_path: str) -> dict:
    """Analyze with PyGitNexus and return stats."""
    print("  [PyGitNexus] Starting analysis...")
    run(f"rm -rf {repo_path}/.pygitnexus")
    out = pygitnexus(f"analyze {repo_path}")
    print(f"  [PyGitNexus] Output: {out.strip()[-300:]}")

    db_path = f"{repo_path}/.pygitnexus/kuzu"
    stats = count_pygitnexus_nodes(db_path)
    return {"db_path": db_path, "stats": stats, "raw_output": out}


QUERY_SET = [
    # Queries that both tools should support
    {"name": "File count", "query": "MATCH (f:File) RETURN count(f) as cnt"},
    {"name": "Method count", "query": "MATCH (m:Method) RETURN count(m) as cnt"},
    {"name": "Top classes by method count", "query": "MATCH (m:Method) RETURN m.className as name, count(m) as cnt ORDER BY cnt DESC LIMIT 10"},
    {"name": "CALLS relation count", "query": "MATCH ()-[r:CodeRelation {type: 'CALLS'}]->() RETURN count(r) as cnt"},
    {"name": "Total relations", "query": "MATCH ()-[r]->() RETURN count(r) as cnt"},
]


def compare_queries(gn_result: dict, pgn_result: dict) -> list[dict]:
    """Run the same queries on both databases and compare."""
    comparisons = []

    for q in QUERY_SET:
        gn_data = cypher_store(gn_result["db_path"], q["query"])
        pgn_data = cypher_store(pgn_result["db_path"], q["query"])
        comparisons.append({
            "query_name": q["name"],
            "query": q["query"],
            "gitnexus": gn_data,
            "pygitnexus": pgn_data,
        })

    return comparisons


def compare_node_schemas(gn_stats: dict, pgn_stats: dict) -> list[dict]:
    """Compare node/edge table schemas between the two tools."""
    all_tables = set(list(gn_stats.keys()) + list(pgn_stats.keys()))
    comparisons = []
    for table in sorted(all_tables):
        gn_cnt = gn_stats.get(table, 0)
        pgn_cnt = pgn_stats.get(table, 0)
        comparisons.append({
            "table": table,
            "gitnexus_count": gn_cnt,
            "pygitnexus_count": pgn_cnt,
            "match": gn_cnt == pgn_cnt,
        })
    return comparisons


def generate_report(repo_path: str, gn_result: dict, pgn_result: dict,
                    query_comparisons: list, schema_comparisons: list) -> str:
    """Generate a markdown comparison report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    lines = []
    lines.append(f"# GitNexus vs PyGitNexus 对比报告\n")
    lines.append(f"**项目**: Apache ShenYu ({REPO_URL})\n")
    lines.append(f"**时间**: {now}\n")
    lines.append(f"**GitNexus 版本**: 1.6.3\n")
    lines.append(f"**PyGitNexus 版本**: 0.1.0\n")

    # Section 1: Storage content comparison
    lines.append("\n## 1. 存储内容对比\n")
    lines.append("### 1.1 表级别统计\n")
    lines.append("| 表名 | GitNexus | PyGitNexus | 是否一致 |")
    lines.append("|------|----------|------------|---------|")
    for sc in schema_comparisons:
        icon = "✅" if sc["match"] else "❌"
        lines.append(f"| {sc['table']} | {sc['gitnexus_count']:,} | {sc['pygitnexus_count']:,} | {icon} |")

    # Section 2: Query comparison
    lines.append("\n### 1.2 查询结果对比\n")
    for qc in query_comparisons:
        lines.append(f"\n**{qc['query_name']}**\n")
        lines.append(f"```cypher\n{qc['query']}\n```\n")
        lines.append(f"GitNexus: `{json.dumps(qc['gitnexus'], default=str)[:200]}`\n")
        lines.append(f"PyGitNexus: `{json.dumps(qc['pygitnexus'], default=str)[:200]}`\n")

    # Section 3: PyGitNexus detailed stats
    lines.append("\n## 2. PyGitNexus 详细统计\n")
    lines.append(f"- 文件数: `{pgn_result['stats'].get('File', 'N/A')}`\n")
    lines.append(f"- Class: `{pgn_result['stats'].get('Class', 'N/A')}`\n")
    lines.append(f"- Interface: `{pgn_result['stats'].get('Interface', 'N/A')}`\n")
    lines.append(f"- Method: `{pgn_result['stats'].get('Method', 'N/A')}`\n")
    lines.append(f"- Field: `{pgn_result['stats'].get('Field', 'N/A')}`\n")
    lines.append(f"- Folder: `{pgn_result['stats'].get('Folder', 'N/A')}`\n")
    lines.append(f"- CodeRelation: `{pgn_result['stats'].get('CodeRelation', 'N/A')}`\n")

    # Section 4: GitNexus detailed stats
    lines.append("\n## 3. GitNexus 详细统计\n")
    for table, count in sorted(gn_result['stats'].items()):
        if table != "_error":
            lines.append(f"- {table}: `{count:,}`\n")

    # Section 5: Schema differences
    lines.append("\n## 4. Schema 差异分析\n")
    gn_tables = set(gn_result['stats'].keys())
    pgn_tables = set(pgn_result['stats'].keys())
    only_gn = gn_tables - pgn_tables
    only_pgn = pgn_tables - gn_tables
    if only_gn:
        lines.append(f"\n**GitNexus 独有的表**: {', '.join(sorted(only_gn))}\n")
    if only_pgn:
        lines.append(f"\n**PyGitNexus 独有的表**: {', '.join(sorted(only_pgn))}\n")
    if not only_gn and not only_pgn:
        lines.append("\n两个工具的表完全一致\n")

    # Section 6: Top classes comparison
    lines.append("\n## 5. Top 10 复杂类对比\n")
    gn_top = cypher_store(gn_result["db_path"],
                          "MATCH (m:Method) RETURN m.className as name, count(m) as cnt ORDER BY cnt DESC LIMIT 10")
    pgn_top = cypher_store(pgn_result["db_path"],
                           "MATCH (m:Method) RETURN m.className as name, count(m) as cnt ORDER BY cnt DESC LIMIT 10")
    lines.append("| 排名 | GitNexus | PyGitNexus |\n")
    lines.append("|------|----------|------------|\n")
    for i in range(max(len(gn_top), len(pgn_top))):
        g = gn_top[i]["name"] if i < len(gn_top) else "-"
        gcnt = gn_top[i]["cnt"] if i < len(gn_top) else "-"
        p = pgn_top[i]["name"] if i < len(pgn_top) else "-"
        pcnt = pgn_top[i]["cnt"] if i < len(pgn_top) else "-"
        lines.append(f"| {i+1} | {g} ({gcnt}) | {p} ({pcnt}) |\n")

    # Section 7: Call chain analysis
    lines.append("\n## 6. 调用链分析\n")
    gn_calls = cypher_store(gn_result["db_path"],
                            "MATCH ()-[r:CodeRelation {type: 'CALLS'}]->() RETURN count(r) as cnt")
    pgn_calls = cypher_store(pgn_result["db_path"],
                             "MATCH ()-[r:CodeRelation {type: 'CALLS'}]->() RETURN count(r) as cnt")
    lines.append(f"- GitNexus 调用关系数: `{gn_calls[0].get('cnt', 'N/A') if gn_calls else 'N/A'}`\n")
    lines.append(f"- PyGitNexus 调用关系数: `{pgn_calls[0].get('cnt', 'N/A') if pgn_calls else 'N/A'}`\n")

    # Section 8: Conclusion
    lines.append("\n## 7. 结论\n")
    match_count = sum(1 for sc in schema_comparisons if sc["match"])
    total_count = len(schema_comparisons)
    lines.append(f"- 表级别匹配率: {match_count}/{total_count}\n")

    lines.append(f"\n---\n*报告生成时间: {now}*\n")

    report = "\n".join(lines)

    # Save report
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"compare_{ts}.md")
    with open(report_path, "w") as f:
        f.write(report)

    # Save raw JSON for machine consumption
    json_path = os.path.join(REPORT_DIR, f"compare_{ts}.json")
    with open(json_path, "w") as f:
        json.dump({
            "timestamp": now,
            "repo": REPO_URL,
            "gitnexus_stats": gn_result["stats"],
            "pygitnexus_stats": pgn_result["stats"],
            "schema_comparison": schema_comparisons,
            "query_comparisons": query_comparisons,
            "match_rate": f"{match_count}/{total_count}",
        }, f, indent=2, default=str)

    return report_path


def main():
    start = time.time()
    print("=" * 60)
    print("  GitNexus vs PyGitNexus 对比验证")
    print(f"  目标: Apache ShenYu")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: Prepare repo
    print("\n[1/5] 准备 Apache ShenYu 仓库...")
    repo_path = prepare_repo()
    print(f"  仓库路径: {repo_path}")

    # Step 2: GitNexus analysis
    print("\n[2/5] 使用 GitNexus 分析...")
    gn_result = analyze_with_gitnexus(repo_path)
    print(f"  GitNexus 表统计: {json.dumps(gn_result['stats'], default=str)[:300]}")

    # Step 3: PyGitNexus analysis
    print("\n[3/5] 使用 PyGitNexus 分析...")
    pgn_result = analyze_with_pygitnexus(repo_path)
    print(f"  PyGitNexus 表统计: {json.dumps(pgn_result['stats'], default=str)[:300]}")

    # Step 4: Compare
    print("\n[4/5] 对比分析...")
    schema_comparison = compare_node_schemas(gn_result["stats"], pgn_result["stats"])
    query_comparison = compare_queries(gn_result, pgn_result)

    # Step 5: Report
    print("\n[5/5] 生成报告...")
    report_path = generate_report(repo_path, gn_result, pgn_result, query_comparison, schema_comparison)

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"  对比完成!")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  报告: {report_path}")
    print(f"{'=' * 60}")

    return report_path


if __name__ == "__main__":
    main()
