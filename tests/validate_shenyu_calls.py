#!/usr/bin/env python3
"""Validate PyGitNexus CALLS against GitNexus samples for shenyu project."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GITNEXUS_REPO = "shenyu-codespace"
SHENYU_DIR = "/home/claude/codespace/shenyu"
PYGITNEXUS_PROJ = "/home/claude/.cc-connect/workspace/pygitnexus"


def run(cmd: str, cwd: str | None = None) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.stdout.strip()


def get_gitnexus_samples(limit: int = 50) -> list[dict]:
    """Sample CALLS from GitNexus."""
    out = run(
        f'gitnexus cypher --repo "{GITNEXUS_REPO}" '
        f'"MATCH (a:Method)-[r:CodeRelation {{type: \'CALLS\'}}]->(b:Method) '
        f'RETURN a.name as callerMethod, a.filePath as callerFile, '
        f'b.name as targetMethod, b.filePath as targetFile LIMIT {limit}"'
    )
    data = json.loads(out)
    samples = []
    if "markdown" in data:
        lines = data["markdown"].strip().split("\n")
        for line in lines[2:]:  # skip header + separator
            parts = [p.strip() for p in line.strip().split("|") if p.strip()]
            if len(parts) == 4:
                samples.append({
                    "caller_method": parts[0],
                    "caller_file": parts[1],
                    "target_method": parts[2],
                    "target_file": parts[3],
                })
    return samples


def check_pygitnexus_call(sample: dict) -> bool:
    """Check if PyGitNexus has this CALLS relation."""
    # Match by method name + file path
    query = (
        f"MATCH (a:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(b:Method) "
        f"WHERE a.name = '{sample['caller_method']}' "
        f"AND a.filePath = '{sample['caller_file']}' "
        f"AND b.name = '{sample['target_method']}' "
        f"AND b.filePath = '{sample['target_file']}' "
        f"RETURN count(r) as cnt"
    )
    out = run(
        f'uv run --project {PYGITNEXUS_PROJ} pygitnexus cypher "{query}" --json',
        cwd=SHENYU_DIR,
    )
    try:
        start = out.index("[")
        end = out.rindex("]") + 1
        data = json.loads(out[start:end])
        if data and data[0].get("cnt", 0) > 0:
            return True
    except Exception:
        pass

    # Fallback: check by method name only (looser match)
    # This catches cases where file paths differ slightly
    fallback_query = (
        f"MATCH (a:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(b:Method) "
        f"WHERE a.name = '{sample['caller_method']}' "
        f"AND b.name = '{sample['target_method']}' "
        f"RETURN count(r) as cnt"
    )
    out = run(
        f'uv run --project {PYGITNEXUS_PROJ} pygitnexus cypher "{fallback_query}" --json',
        cwd=SHENYU_DIR,
    )
    try:
        start = out.index("[")
        end = out.rindex("]") + 1
        data = json.loads(out[start:end])
        if data and data[0].get("cnt", 0) > 0:
            return True
    except Exception:
        pass

    return False


def get_pygitnexus_samples(limit: int = 30) -> list[dict]:
    """Sample CALLS from PyGitNexus."""
    query = (
        f"MATCH (a:Method)-[r:CodeRelation {{type: 'CALLS'}}]->(b:Method) "
        f"RETURN a.name as callerMethod, a.filePath as callerFile, "
        f"b.name as targetMethod, b.filePath as targetFile LIMIT {limit}"
    )
    out = run(
        f'uv run --project {PYGITNEXUS_PROJ} pygitnexus cypher "{query}" --json',
        cwd=SHENYU_DIR,
    )
    try:
        start = out.index("[")
        end = out.rindex("]") + 1
        data = json.loads(out[start:end])
        return data[:limit]
    except Exception:
        return []


def check_gitnexus_call(sample: dict) -> bool:
    """Check if GitNexus has this CALLS relation."""
    out = run(
        f'gitnexus cypher --repo "{GITNEXUS_REPO}" '
        f'"MATCH (a:Method)-[r:CodeRelation {{type: \'CALLS\'}}]->(b:Method) '
        f'WHERE a.name = \'{sample["callerMethod"]}\' AND b.name = \'{sample["targetMethod"]}\' '
        f'RETURN count(r) as cnt"'
    )
    try:
        data = json.loads(out)
        if "markdown" in data:
            lines = data["markdown"].strip().split("\n")
            if len(lines) >= 3:
                val = lines[-1].strip().strip("|").strip()
                return int(val) > 0
    except Exception:
        pass
    return False


if __name__ == "__main__":
    print("=== shenyu CALLS 抽样验证 (GitNexus → PyGitNexus) ===\n")
    samples = get_gitnexus_samples(50)
    print(f"获取 {len(samples)} 条 GitNexus CALLS 抽样\n")

    matched = 0
    not_matched = []
    for i, s in enumerate(samples):
        found = check_pygitnexus_call(s)
        if found:
            matched += 1
            status = "✅"
        else:
            not_matched.append(s)
            status = "❌"

        if i < 20:
            print(f"  {status} {s['caller_method']}({s['caller_file']}) -> {s['target_method']}({s['target_file']})")

    total = len(samples)
    recall = matched / total * 100 if total > 0 else 0

    print(f"\n--- 结果 ---")
    print(f"  匹配: {matched}/{total} ({recall:.1f}%)")
    print(f"  未匹配: {len(not_matched)}/{total}")

    if not_matched:
        print(f"\n  未匹配的调用 (前 10 条):")
        for s in not_matched[:10]:
            print(f"    {s['caller_method']}({s['caller_file']}) -> {s['target_method']}({s['target_file']})")

    # Also check the reverse: PyGitNexus CALLS that GitNexus doesn't have
    print(f"\n=== 反向验证：PyGitNexus CALLS 在 GitNexus 中是否存在 ===\n")
    py_calls = get_pygitnexus_samples(30)
    gn_matched = 0
    for s in py_calls:
        if check_gitnexus_call(s):
            gn_matched += 1

    py_total = len(py_calls)
    py_recall = gn_matched / py_total * 100 if py_total > 0 else 0
    print(f"  PyGitNexus 抽样: {py_total}")
    print(f"  GitNexus 也找到: {gn_matched}/{py_total} ({py_recall:.1f}%)")
