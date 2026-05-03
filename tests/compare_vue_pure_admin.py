#!/usr/bin/env python3
"""Compare CALLS relations between GitNexus and PyGitNexus for vue-pure-admin."""

import json
import sys
from pathlib import Path


def load_gitnexus(path):
    """Load GitNexus CALLS from JSON file."""
    with open(path) as f:
        data = json.load(f)
    calls = set()
    all_calls = []
    for r in data:
        key = (r['caller_file'], r['caller_name'], r['target_file'], r['target_name'])
        calls.add(key)
        all_calls.append(key)
    return calls, all_calls


def load_pygitnexus(path):
    """Load PyGitNexus CALLS from JSON file."""
    with open(path) as f:
        data = json.load(f)
    calls = set()
    all_calls = []
    for r in data:
        key = (r['m.filePath'], r['m.name'], r['t.filePath'], r['t.name'])
        calls.add(key)
        all_calls.append(key)
    return calls, all_calls


def analyze_coverage(gn_calls, pg_calls, gn_all, pg_all):
    """Analyze the coverage and overlap between the two tools."""
    # Intersection
    overlap = gn_calls & pg_calls

    # GitNexus unique
    gn_unique = gn_calls - pg_calls

    # PyGitNexus unique
    pg_unique = pg_calls - gn_calls

    # Stats
    print(f"=== CALLS Comparison: GitNexus vs PyGitNexus ===")
    print(f"")
    print(f"GitNexus CALLS:  {len(gn_calls)} unique ({len(gn_all)} total)")
    print(f"PyGitNexus CALLS: {len(pg_calls)} unique ({len(pg_all)} total)")
    print(f"Overlap:          {len(overlap)}")
    print(f"")
    print(f"GitNexus recall:  {len(overlap)/len(gn_calls)*100:.1f}% ({len(overlap)}/{len(gn_calls)})")
    print(f"PyGitNexus precision: {len(overlap)/len(pg_calls)*100:.1f}% ({len(overlap)}/{len(pg_calls)})")
    print(f"")

    # Cross-file analysis
    gn_cross = {c for c in gn_calls if c[0] != c[2]}
    pg_cross = {c for c in pg_calls if c[0] != c[2]}
    overlap_cross = gn_cross & pg_cross

    print(f"Cross-file calls:")
    print(f"  GitNexus:  {len(gn_cross)}")
    print(f"  PyGitNexus: {len(pg_cross)}")
    print(f"  Overlap:    {len(overlap_cross)}")
    print(f"  PyGitNexus cross-file recall: {len(overlap_cross)/len(gn_cross)*100:.1f}%")
    print(f"")

    # Same-file analysis
    gn_same = {c for c in gn_calls if c[0] == c[2]}
    pg_same = {c for c in pg_calls if c[0] == c[2]}
    overlap_same = gn_same & pg_same

    print(f"Same-file calls:")
    print(f"  GitNexus:  {len(gn_same)}")
    print(f"  PyGitNexus: {len(pg_same)}")
    print(f"  Overlap:    {len(overlap_same)}")
    print(f"  PyGitNexus same-file recall: {len(overlap_same)/len(gn_same)*100:.1f}%")
    print(f"")

    # Top callers by file
    print(f"=== Top 10 Files with Most CALLS (GitNexus) ===")
    from collections import Counter
    gn_caller_files = Counter(c[0] for c in gn_all)
    for file, count in gn_caller_files.most_common(10):
        pg_count = sum(1 for c in pg_all if c[0] == file)
        overlap_count = sum(1 for c in gn_all if c in pg_calls and c[0] == file)
        print(f"  {file}: GN={count}, PG={pg_count}, overlap={overlap_count}")
    print(f"")

    # Top targets by file
    print(f"=== Top 10 Most Called Files (GitNexus) ===")
    gn_target_files = Counter(c[2] for c in gn_all)
    for file, count in gn_target_files.most_common(10):
        pg_count = sum(1 for c in pg_all if c[2] == file)
        overlap_count = sum(1 for c in gn_all if c in pg_calls and c[2] == file)
        print(f"  {file}: GN={count}, PG={pg_count}, overlap={overlap_count}")
    print(f"")

    # GitNexus unique calls that PyGitNexus missed
    print(f"=== GitNexus Unique Calls (PyGitNexus missed, first 30) ===")
    for caller_file, caller_name, target_file, target_name in sorted(gn_unique)[:30]:
        print(f"  {caller_file}:{caller_name} -> {target_file}:{target_name}")
    print(f"  ... total {len(gn_unique)} unique calls")
    print(f"")

    # PyGitNexus unique calls that GitNexus doesn't have
    print(f"=== PyGitNexus Unique Calls (GitNexus doesn't have, first 30) ===")
    for caller_file, caller_name, target_file, target_name in sorted(pg_unique)[:30]:
        print(f"  {caller_file}:{caller_name} -> {target_file}:{target_name}")
    print(f"  ... total {len(pg_unique)} unique calls")
    print(f"")

    # Categorize GitNexus unique calls by reason
    categorize_gn_unique(gn_unique, pg_calls, gn_calls)


def categorize_gn_unique(gn_unique, pg_calls, gn_calls):
    """Categorize why GitNexus found calls that PyGitNexus missed."""
    # Check if the target is in PyGitNexus's method list at all
    # This requires knowing all PyGitNexus methods, which we don't have directly
    # But we can analyze patterns

    # Group by target file
    from collections import Counter
    target_counter = Counter((c[2], c[3]) for c in gn_unique)

    print(f"=== GitNexus-unique targets (top 20) ===")
    for (target_file, target_name), count in target_counter.most_common(20):
        print(f"  {target_file}:{target_name} (called by {count} methods)")
    print(f"")

    # Check if PyGitNexus misses entire files
    gn_files_with_calls = set(c[0] for c in gn_unique)
    pg_files_with_calls = set(c[0] for c in pg_calls)
    gn_only_caller_files = gn_files_with_calls - pg_files_with_calls

    print(f"=== Files GitNexus has callers from but PyGitNexus doesn't (top 20) ===")
    print(f"  Total: {len(gn_only_caller_files)} files")
    for f in sorted(list(gn_only_caller_files)[:20]):
        count = sum(1 for c in gn_unique if c[0] == f)
        print(f"  {f} ({count} calls)")
    print(f"")

    # Check self-recursion patterns (GitNexus often has these)
    self_recursive = [c for c in gn_unique if c[0] == c[2] and c[1] == c[3]]
    print(f"GitNexus unique self-recursive calls: {len(self_recursive)}")

    # Check calls within same file (but different methods)
    same_file_diff = [c for c in gn_unique if c[0] == c[2] and c[1] != c[3]]
    print(f"GitNexus unique same-file different-method calls: {len(same_file_diff)}")

    # Cross-file calls missed
    cross_file = [c for c in gn_unique if c[0] != c[2]]
    print(f"GitNexus unique cross-file calls: {len(cross_file)}")


def main():
    gn_path = '/tmp/gitnexus_calls.json'
    pg_path = '/tmp/pygitnexus_calls_new.json'

    gn_calls, gn_all = load_gitnexus(gn_path)
    pg_calls, pg_all = load_pygitnexus(pg_path)

    analyze_coverage(gn_calls, pg_calls, gn_all, pg_all)


if __name__ == '__main__':
    main()
