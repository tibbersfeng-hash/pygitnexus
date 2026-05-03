#!/usr/bin/env python3
"""Compare GitNexus vs PyGitNexus analysis results."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime


def run_cmd(cmd: str, cwd: str | None = None) -> str:
    """Run a shell command and return stdout."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.stdout + r.stderr


def run_gitnexus_cypher(repo: str, query: str, cwd: str | None = None) -> str:
    """Run GitNexus cypher query and extract count."""
    cmd = f'gitnexus cypher --repo "{repo}" "{query}"'
    out = run_cmd(cmd, cwd)
    try:
        data = json.loads(out.strip().split("\n")[0])
        if "markdown" in data:
            # Parse markdown table: | cnt |\n| --- |\n| 123 |
            lines = data["markdown"].strip().split("\n")
            if len(lines) >= 3:
                val = lines[-1].strip().strip("|").strip()
                return val
        return str(data)
    except Exception:
        return out.strip()[:100]


def run_pygitnexus_cypher(query: str, cwd: str) -> int:
    """Run PyGitNexus cypher query and return count."""
    proj = "/home/claude/.cc-connect/workspace/pygitnexus"
    cmd = f'uv run --project {proj} pygitnexus cypher "{query}" --json'
    out = run_cmd(cmd, cwd)
    try:
        # Find the JSON array in output
        start = out.index("[")
        end = out.rindex("]") + 1
        data = json.loads(out[start:end])
        if data:
            return int(data[0].get("cnt", 0))
        return 0
    except Exception:
        print(f"  WARN: Failed to parse PyGitNexus output: {out[:200]}")
        return 0


def count_gitnexus_edges(repo: str, cwd: str | None = None) -> dict:
    """Get edge counts from GitNexus."""
    cmd = f'gitnexus cypher --repo "{repo}" "MATCH ()-[r:CodeRelation]->() RETURN r.type as type, count(r) as cnt ORDER BY cnt DESC"'
    out = run_cmd(cmd, cwd)
    result = {}
    try:
        data = json.loads(out.strip().split("\n")[0])
        if "markdown" in data:
            lines = data["markdown"].strip().split("\n")
            for line in lines[2:]:  # skip header and separator
                parts = [p.strip() for p in line.strip().split("|") if p.strip()]
                if len(parts) == 2:
                    result[parts[0]] = int(parts[1])
    except Exception as e:
        print(f"  WARN: Failed to parse GitNexus edges: {e}")
    return result


def count_pygitnexus_edges(cwd: str) -> dict:
    """Get edge counts from PyGitNexus."""
    proj = "/home/claude/.cc-connect/workspace/pygitnexus"
    cmd = f'uv run --project {proj} pygitnexus cypher "MATCH ()-[r:CodeRelation]->() RETURN r.type as type, count(r) as cnt ORDER BY cnt DESC" --json'
    out = run_cmd(cmd, cwd)
    result = {}
    try:
        start = out.index("[")
        end = out.rindex("]") + 1
        data = json.loads(out[start:end])
        for row in data:
            result[row["type"]] = row["cnt"]
    except Exception as e:
        print(f"  WARN: Failed to parse PyGitNexus edges: {e}")
    return result


def sample_calls_gitnexus(repo: str, limit: int = 50, cwd: str | None = None) -> list[dict]:
    """Sample CALLS relations from GitNexus."""
    cmd = f'gitnexus cypher --repo "{repo}" "MATCH (a:Method)-[r:CodeRelation {{type: \'CALLS\'}}]->(b:Method) RETURN a.className as callerClass, a.name as callerMethod, b.className as targetClass, b.name as targetMethod LIMIT {limit}"'
    out = run_cmd(cmd, cwd)
    result = []
    try:
        data = json.loads(out.strip().split("\n")[0])
        if "markdown" in data:
            lines = data["markdown"].strip().split("\n")
            for line in lines[2:]:
                parts = [p.strip() for p in line.strip().split("|") if p.strip()]
                if len(parts) == 4:
                    result.append({
                        "caller_class": parts[0],
                        "caller_method": parts[1],
                        "target_class": parts[2],
                        "target_method": parts[3],
                    })
    except Exception as e:
        print(f"  WARN: Failed to sample GitNexus calls: {e}")
    return result


def check_call_in_pygitnexus(sample: dict, cwd: str) -> bool:
    """Check if a specific call exists in PyGitNexus."""
    proj = "/home/claude/.cc-connect/workspace/pygitnexus"
    # Search by method names
    query = (
        f"MATCH (a:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(b:Method) "
        f"WHERE a.name = '{sample['caller_method']}' AND b.name = '{sample['target_method']}' "
        f"RETURN count(r) as cnt"
    )
    cmd = f'uv run --project {proj} pygitnexus cypher "{query}" --json'
    out = run_cmd(cmd, cwd)
    try:
        start = out.index("[")
        end = out.rindex("]") + 1
        data = json.loads(out[start:end])
        if data and data[0].get("cnt", 0) > 0:
            return True
    except Exception:
        pass
    return False


def compare_project(name: str, gn_repo: str, project_dir: str):
    """Run full comparison for a project."""
    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"  路径: {project_dir}")
    print(f"{'='*70}")

    # Node counts
    print(f"\n{'节点类型':<15} {'GitNexus':>10} {'PyGitNexus':>12} {'差异%':>8} {'状态':>6}")
    print("-" * 60)

    gn_nodes = {}
    py_nodes = {}

    for node_type in ["Class", "Interface", "Method", "Field", "File"]:
        # GitNexus
        gn_val = 0
        try:
            gn_str = run_gitnexus_cypher(gn_repo, f"MATCH (n:{node_type}) RETURN count(n) as cnt", project_dir)
            gn_val = int(gn_str) if gn_str.isdigit() else 0
        except Exception:
            gn_val = 0

        # PyGitNexus
        py_val = run_pygitnexus_cypher(f"MATCH (n:{node_type}) RETURN count(n) as cnt", project_dir)

        gn_nodes[node_type] = gn_val
        py_nodes[node_type] = py_val

        if gn_val > 0:
            diff_pct = abs(py_val - gn_val) / gn_val * 100
        elif py_val > 0:
            diff_pct = 100.0
        else:
            diff_pct = 0.0

        status = "✅" if diff_pct <= 15 else "⚠️"
        print(f"{node_type:<15} {gn_val:>10,} {py_val:>12,} {diff_pct:>7.1f}% {status:>6}")

    # Edge counts
    print(f"\n{'关系类型':<15} {'GitNexus':>10} {'PyGitNexus':>12} {'差异%':>8}")
    print("-" * 60)

    gn_edges = count_gitnexus_edges(gn_repo, project_dir)
    py_edges = count_pygitnexus_edges(project_dir)

    all_types = sorted(set(list(gn_edges.keys()) + list(py_edges.keys())))
    for rel_type in all_types:
        gn_val = gn_edges.get(rel_type, 0)
        py_val = py_edges.get(rel_type, 0)
        if gn_val > 0:
            diff_pct = abs(py_val - gn_val) / gn_val * 100
        elif py_val > 0:
            diff_pct = 100.0
        else:
            diff_pct = 0.0
        marker = " (仅Py)" if gn_val == 0 and py_val > 0 else (" (仅GN)" if py_val == 0 and gn_val > 0 else "")
        print(f"{rel_type:<15} {gn_val:>10,} {py_val:>12,} {diff_pct:>7.1f}%{marker}")

    # Summary
    gn_total_nodes = sum(gn_nodes.values())
    py_total_nodes = sum(py_nodes.values())
    gn_total_edges = sum(gn_edges.values())
    py_total_edges = sum(py_edges.values())

    print(f"\n{'汇总':<15} {'GitNexus':>10} {'PyGitNexus':>12}")
    print(f"  节点总数       {gn_total_nodes:>10,} {py_total_nodes:>12,}")
    print(f"  关系总数       {gn_total_edges:>10,} {py_total_edges:>12,}")

    # Sampling
    gn_calls_total = gn_edges.get("CALLS", 0)
    sample_size = max(50, int(gn_calls_total * 0.2))
    print(f"\n调用关系抽样验证 (GitNexus CALLS 总数: {gn_calls_total:,}, 抽样: {min(sample_size, gn_calls_total)} 条)")
    print("-" * 60)

    samples = sample_calls_gitnexus(gn_repo, min(sample_size, gn_calls_total), project_dir)
    if not samples:
        print("  无法获取抽样数据")
        return

    matched = 0
    not_matched = 0
    for i, s in enumerate(samples):
        found = check_call_in_pygitnexus(s, project_dir)
        if found:
            matched += 1
        else:
            not_matched += 1
        if i < 10:
            status = "✅" if found else "❌"
            print(f"  {status} {s['caller_class']}.{s['caller_method']} -> {s['target_class']}.{s['target_method']}")

    total = matched + not_matched
    recall = matched / total * 100 if total > 0 else 0
    print(f"\n  匹配: {matched}/{total} ({recall:.1f}%)")


if __name__ == "__main__":
    projects = [
        {
            "name": "dashboard-backend (中型 Java)",
            "gn_repo": "life-death",
            "dir": "/home/claude/codespace/life-death/dashboard-backend",
        },
        {
            "name": "shenyu (大型 Java — 性能基准)",
            "gn_repo": "shenyu-codespace",
            "dir": "/home/claude/codespace/shenyu",
        },
        {
            "name": "vue-pure-admin (前端)",
            "gn_repo": "vue-pure-admin",
            "dir": "/home/claude/codespace/vue-pure-admin",
        },
    ]

    for proj in projects:
        compare_project(proj["name"], proj["gn_repo"], proj["dir"])

    print(f"\n{'='*70}")
    print(f"  测试完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
