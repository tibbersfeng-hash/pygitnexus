"""CLI group commands for cross-repo management."""

from __future__ import annotations

import os

import click

from ..core.group.config_parser import (
    GroupConfig,
    GroupNotFoundError,
    parse_group_yaml,
    write_group_yaml,
)
from ..core.group.storage import (
    create_group_dir,
    get_group_dir,
    list_groups,
    load_group_config,
    remove_group,
)
from ..core.group.sync import sync_group
from ..core.group.cross_impact import group_impact


@click.group("group")
def group_cmd() -> None:
    """Manage repository groups for cross-index impact analysis."""
    pass


@group_cmd.command("create")
@click.argument("name")
@click.option("--force", is_flag=True, help="Overwrite existing group")
def group_create(name: str, force: bool) -> None:
    """Create a new group with template group.yaml."""
    try:
        group_dir = create_group_dir(name, force=force)
        print(f'Created group "{name}" at {group_dir}')
        print("Edit group.yaml to add repos, then run:")
        print(f"  pygitnexus group add {name} <group_path> <registry_name>")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@group_cmd.command("add")
@click.argument("group_name")
@click.argument("group_path")
@click.argument("registry_name")
def group_add(group_name: str, group_path: str, registry_name: str) -> None:
    """Add a repo to a group.

    GROUP_PATH: hierarchy path (e.g. app/backend)
    REGISTRY_NAME: name from 'pygitnexus list'
    """
    group_dir = get_group_dir(group_name)
    if not group_dir or not os.path.exists(os.path.join(group_dir, "group.yaml")):
        click.echo(f'Error: Group "{group_name}" not found. Create with: pygitnexus group create {group_name}', err=True)
        raise SystemExit(1)

    config = load_group_config(group_dir)
    config.repos[group_path] = registry_name
    write_group_yaml(os.path.join(group_dir, "group.yaml"), config)
    click.echo(f"Added {registry_name} as \"{group_path}\" to group \"{group_name}\"")
    click.echo(f"Run: pygitnexus group sync {group_name}")


@group_cmd.command("remove")
@click.argument("group_name")
@click.argument("group_path")
def group_remove(group_name: str, group_path: str) -> None:
    """Remove a repo from a group."""
    group_dir = get_group_dir(group_name)
    if not group_dir:
        click.echo(f'Error: Group "{group_name}" not found.', err=True)
        raise SystemExit(1)

    config = load_group_config(group_dir)
    if group_path not in config.repos:
        click.echo(f'Error: Repo path "{group_path}" not found in group "{group_name}"', err=True)
        raise SystemExit(1)

    del config.repos[group_path]
    write_group_yaml(os.path.join(group_dir, "group.yaml"), config)
    click.echo(f'Removed "{group_path}" from group "{group_name}"')


@group_cmd.command("list")
@click.argument("name", required=False)
def group_list(name: str | None) -> None:
    """List all groups or details of one."""
    if not name:
        groups = list_groups()
        if not groups:
            click.echo("No groups configured. Create one with: pygitnexus group create <name>")
            return
        click.echo(f"Groups ({len(groups)}):")
        for g in groups:
            click.echo(f"  {g}")
        return

    group_dir = get_group_dir(name)
    if not group_dir:
        click.echo(f'Error: Group "{name}" not found.', err=True)
        raise SystemExit(1)

    config = load_group_config(group_dir)
    click.echo(f"Group: {config.name}")
    if config.description:
        click.echo(f"Description: {config.description}")
    click.echo(f"\nRepos ({len(config.repos)}):")
    for gp, rn in config.repos.items():
        click.echo(f"  {gp} -> {rn}")


@group_cmd.command("delete")
@click.argument("name")
@click.option("--force", is_flag=True, help="Skip confirmation")
def group_delete(name: str, force: bool) -> None:
    """Delete a group entirely."""
    if not force:
        if not click.confirm(f'Delete group "{name}" and all its data?'):
            return
    if remove_group(name):
        click.echo(f'Deleted group "{name}"')
    else:
        click.echo(f'Group "{name}" not found.', err=True)
        raise SystemExit(1)


@group_cmd.command("sync")
@click.argument("name")
@click.option("--allow-stale", is_flag=True, help="Skip stale index warnings")
@click.option("--verbose", is_flag=True, help="Show each cross-link detail")
def group_sync(name: str, allow_stale: bool, verbose: bool) -> None:
    """Sync Contract Registry — extract contracts and build cross-links."""
    click.echo(f'Syncing group "{name}"...')

    result = sync_group(name, allow_stale=allow_stale, verbose=verbose)

    if "error" in result:
        click.echo(f"Error: {result['error']}", err=True)
        raise SystemExit(1)

    click.echo(f"\nContracts: {result['contracts']}")
    click.echo(f"Cross-links: {result['crossLinks']}")
    click.echo(f"Unmatched: {result['unmatched']}")
    if result.get("missingRepos"):
        click.echo(f"Missing repos: {', '.join(result['missingRepos'])}")
    report = result.get("report", {})
    click.echo(f"Bridge DB: {report.get('contractsInserted', 0)} contracts, "
               f"{report.get('linksInserted', 0)} links inserted")


@group_cmd.command("status")
@click.argument("name")
def group_status(name: str) -> None:
    """Check staleness of group and repos."""
    import json

    group_dir = get_group_dir(name)
    if not group_dir:
        click.echo(f'Error: Group "{name}" not found.', err=True)
        raise SystemExit(1)

    config = load_group_config(group_dir)

    # Check meta.json
    meta_path = os.path.join(group_dir, "meta.json")
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path))
        click.echo(f"Group: {name} (last sync: {meta.get('generatedAt', 'unknown')})")
    else:
        click.echo(f"Group: {name} (never synced)")

    click.echo("\nRepo status:")
    from ..storage.repo_manager import list_repos
    all_repos = {r.name: r for r in list_repos()}

    for gp, rn in config.repos.items():
        repo = all_repos.get(rn)
        if not repo:
            click.echo(f"  {gp:30s} MISSING   (not in registry)")
            continue
        click.echo(f"  {gp:30s} INDEXED   ({repo.indexed_at})")


@group_cmd.command("impact")
@click.argument("name")
@click.option("--target", required=True, help="Symbol or file name to analyze")
@click.option("--repo", required=True, help="Member path from group.yaml (e.g. app/backend)")
@click.option("--direction", default="upstream", help="upstream or downstream")
@click.option("--max-depth", default=3, type=int, help="Max graph traversal depth")
@click.option("--min-confidence", default=0.0, type=float, help="Minimum confidence (0-1)")
@click.option("--json", "json_output", is_flag=True, help="JSON output")
def group_impact_cmd(
    name: str,
    target: str,
    repo: str,
    direction: str,
    max_depth: int,
    min_confidence: float,
    json_output: bool,
) -> None:
    """Cross-repo impact for a symbol in one member repo."""
    # Resolve the target repo's DB path
    group_dir = get_group_dir(name)
    if not group_dir:
        click.echo(f'Error: Group "{name}" not found.', err=True)
        raise SystemExit(1)

    config = load_group_config(group_dir)
    reg_name = config.repos.get(repo)
    if not reg_name:
        click.echo(f'Error: Repo path "{repo}" not found in group "{name}"', err=True)
        raise SystemExit(1)

    from ..storage.repo_manager import get_repo as _get_repo
    repo_info = _get_repo(reg_name)
    if not repo_info:
        click.echo(f'Error: Registry entry "{reg_name}" not found', err=True)
        raise SystemExit(1)

    db_path = os.path.join(repo_info.path, ".pygitnexus", "kuzu")
    if not os.path.exists(db_path):
        click.echo(f'Error: KuzuDB not found at {db_path}. Run: pygitnexus analyze {repo_info.path}', err=True)
        raise SystemExit(1)

    result = group_impact(
        group_name=name,
        repo_path=repo,
        target=target,
        db_path=db_path,
        direction=direction,
        max_depth=max_depth,
        min_confidence=min_confidence,
    )

    if "error" in result:
        click.echo(f"Error: {result['error']}", err=True)
        raise SystemExit(1)

    if json_output:
        import json as _json
        # Convert result to JSON-safe format
        print(_json.dumps(result, indent=2, default=str))
        return

    # Format output
    lines = [
        f"Group impact for \"{target}\" ({repo}) in \"{name}\"",
        f"Risk: {result.get('risk', '?')}",
        f"Local: {result['local']['summary']['total_affected']} symbols affected, "
        f"{result['local']['summary']['direct_dependents']} direct",
        f"Cross-repo: {result['summary']['cross_repo_hits']} repos affected",
        "",
    ]

    if result["local"]["byDepth"]:
        for depth_key, items in result["local"]["byDepth"].items():
            lines.append(f"  {depth_key} (local, {len(items)} symbols):")
            for item in items[:10]:
                lines.append(f"    - {item.get('type', '')}:{item.get('name', '')} at {item.get('filePath', '')}")
            if len(items) > 10:
                lines.append(f"    ... and {len(items) - 10} more")
            lines.append("")

    if result["cross"]:
        lines.append("Cross-repo impacts:")
        for c in result["cross"]:
            lines.append(f"  -> {c['repo']} ({c['repo_path']}): {c['contract']['match_type']} match, "
                         f"confidence={c['contract']['confidence']:.2f}")
            lines.append(f"     Symbol: {c['symbol'].get('name', '')} at {c['symbol'].get('filePath', '')}")
            impact = c.get("impact", {})
            if impact.get("summary", {}).get("total_affected", 0) > 0:
                lines.append(f"     {impact['summary']['total_affected']} symbols affected in {c['repo']}")
            lines.append("")

    click.echo("\n".join(lines))


@group_cmd.command("contracts")
@click.argument("name")
@click.option("--repo", help="Filter by repo")
@click.option("--json", "json_output", is_flag=True, help="JSON output")
def group_contracts(name: str, repo: str | None, json_output: bool) -> None:
    """List contracts from the bridge DB."""
    from ..core.group.bridge_db import query_bridge

    group_dir = get_group_dir(name)
    if not group_dir:
        click.echo(f'Error: Group "{name}" not found.', err=True)
        raise SystemExit(1)

    bridge_path = os.path.join(group_dir, "bridge", "kuzu")
    if not os.path.exists(bridge_path):
        click.echo("No bridge DB found. Run: pygitnexus group sync <name>", err=True)
        raise SystemExit(1)

    cypher = "MATCH (c:Contract) RETURN c.contractId AS contractId, c.type AS type, c.role AS role, c.repo AS repo, c.symbolName AS symbolName, c.filePath AS filePath, c.confidence AS confidence"
    params: dict = {}
    if repo:
        cypher += " WHERE c.repo = $repo"
        params["repo"] = repo
    cypher += " ORDER BY c.repo, c.type, c.contractId"

    rows = query_bridge(group_dir, cypher, params)
    if not rows:
        click.echo("No contracts found in bridge DB.")
        return

    if json_output:
        import json
        print(json.dumps(rows, indent=2, default=str))
        return

    click.echo(f"Contracts ({len(rows)}):")
    for r in rows:
        click.echo(f"  [{r['role']:8s}] {r['contractId']:40s} ({r['repo']}) {r['symbolName']}")
