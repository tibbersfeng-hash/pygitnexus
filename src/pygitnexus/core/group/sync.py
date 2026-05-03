"""Group sync: extract contracts, match, and build bridge DB."""

from __future__ import annotations

import os
from pathlib import Path

from ...graph.store import GraphStore
from ...storage.repo_manager import list_repos
from .contract_extractor import extract_http_contracts
from .matching import run_exact_match
from .bridge_db import sync_bridge
from .storage import load_group_config


def sync_group(
    group_name: str,
    allow_stale: bool = False,
    verbose: bool = False,
) -> dict:
    """Full group sync: extract contracts from all repos, match, build bridge.

    Args:
        group_name: name of the group
        allow_stale: skip stale index warnings
        verbose: show each cross-link detail

    Returns:
        Sync result dict.
    """
    from .storage import get_group_dir

    group_dir = get_group_dir(group_name)
    config = load_group_config(group_dir)

    if not config.repos:
        return {"error": "No repos configured in this group."}

    # Build registry lookup
    all_repos = {r.name: r for r in list_repos()}

    missing_repos: list[str] = []
    repo_snapshots: dict = {}
    all_contracts = []

    for group_path, reg_name in config.repos.items():
        repo_info = all_repos.get(reg_name)
        if not repo_info:
            missing_repos.append(group_path)
            if verbose:
                print(f"  [SKIP] {group_path}: registry entry '{reg_name}' not found")
            continue

        db_path = Path(repo_info.path) / ".pygitnexus" / "kuzu"
        if not db_path.exists():
            missing_repos.append(group_path)
            if verbose:
                print(f"  [SKIP] {group_path}: DB not found at {db_path}")
            continue

        if verbose:
            print(f"  [EXTRACT] {group_path} ({reg_name})...")

        try:
            store = GraphStore(db_path)

            # Extract HTTP contracts
            if config.detect_http:
                contracts = extract_http_contracts(store, repo_info.path, group_path)
                all_contracts.extend(contracts)
                if verbose:
                    providers = sum(1 for c in contracts if c.role == "provider")
                    consumers = sum(1 for c in contracts if c.role == "consumer")
                    print(f"    HTTP: {providers} providers, {consumers} consumers")

            # Repo snapshot
            repo_snapshots[group_path] = {
                "indexedAt": repo_info.indexed_at or "",
                "lastCommit": "",
            }

            store.close()
        except Exception as e:
            missing_repos.append(group_path)
            if verbose:
                print(f"    Error: {e}")

    if verbose:
        print(f"\n  Total contracts: {len(all_contracts)}")

    # Match contracts
    match_result = run_exact_match(all_contracts)
    cross_links = match_result["matched"]
    unmatched = match_result["unmatched"]

    if verbose:
        print(f"  Cross-links: {len(cross_links)}")
        print(f"  Unmatched: {len(unmatched)}")
        for cl in cross_links:
            print(f"    {cl['from']['repo']} -> {cl['to']['repo']} [{cl['match_type']}]: {cl['contract_id']}")

    # Build bridge DB
    # Convert Contract objects to dicts for bridge
    contract_dicts = []
    for c in all_contracts:
        if isinstance(c, dict):
            contract_dicts.append(c)
        else:
            contract_dicts.append({
                "contractId": c.contract_id,
                "type": c.contract_type,
                "role": c.role,
                "repo": c.repo,
                "service": c.service,
                "symbolUid": c.symbol_uid,
                "filePath": c.symbol_ref_file,
                "symbolName": c.symbol_ref_name,
                "confidence": c.confidence,
                "meta": c.meta or {},
            })

    # Convert cross-link dicts to format expected by bridge
    link_dicts = []
    for cl in cross_links:
        link_dicts.append({
            "from": cl["from"],
            "to": cl["to"],
            "contractId": cl.get("contractId", cl.get("contract_id", "")),
            "matchType": cl.get("matchType", cl.get("match_type", "exact")),
            "confidence": cl.get("confidence", 1.0),
        })

    report = sync_bridge(
        group_dir,
        contract_dicts,
        link_dicts,
        repo_snapshots,
        missing_repos,
    )

    return {
        "contracts": len(all_contracts),
        "crossLinks": len(cross_links),
        "unmatched": len(unmatched),
        "missingRepos": missing_repos,
        "report": report,
    }
