"""Auto-generate Playwright tests from frontend source code."""

from __future__ import annotations

import os
from pathlib import Path

import click

from ..core.operation_extractor import extract_operations_from_source
from ..auto_gen.playwright_generator import generate_all
from ..auto_gen.db_writer import write_to_db


@click.command("autogen")
@click.argument("repo_path", type=click.Path(exists=True))
@click.option(
    "--output",
    "-o",
    default="tests/auto_generated",
    type=click.Path(),
    help="Output directory for generated Playwright scripts",
)
@click.option(
    "--db",
    "-d",
    default=None,
    type=click.Path(),
    help="KuzuDB path for storing test scripts and operations",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Print detailed operation extraction info",
)
def autogen_cmd(repo_path: str, output: str, db: str | None, verbose: bool) -> None:
    """Auto-generate Playwright tests from frontend source code.

    Scans a frontend project for Vue/HTML files, extracts all interactive
    operations (buttons, form fields, event handlers, API calls), and
    generates executable Playwright Python test scripts.

    If --db is specified, test scripts and operations are also stored
    in the KuzuDB graph database for querying.
    """
    repo_path = os.path.abspath(repo_path)
    output = os.path.abspath(output)

    click.echo(f"Scanning frontend project: {repo_path}")
    click.echo(f"Output directory: {output}")
    if db:
        click.echo(f"Database: {os.path.abspath(db)}")

    # Scan for frontend files
    frontend_extensions = {".vue", ".html", ".htm"}
    ignore_dirs = {"node_modules", ".git", "dist", "build", "coverage", ".venv", "__pycache__", "test-results"}

    frontend_files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Skip ignored directories
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext in frontend_extensions:
                frontend_files.append(os.path.join(dirpath, fn))

    if not frontend_files:
        click.echo("No Vue/HTML files found in the project.")
        return

    click.echo(f"Found {len(frontend_files)} frontend files")

    # Extract operations from each file
    all_op_sets = []
    total_ops = 0
    total_fields = 0

    for file_path in frontend_files:
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            op_set = extract_operations_from_source(file_path, content)
            if op_set.operations or op_set.all_fields:
                all_op_sets.append(op_set)
                total_ops += len(op_set.operations)
                total_fields += len(op_set.all_fields)

                if verbose:
                    click.echo(f"\n  {file_path}")
                    click.echo(f"    Route: {op_set.page_route}")
                    click.echo(f"    Operations: {len(op_set.operations)}")
                    for op in op_set.operations:
                        click.echo(f"      {op.op_id}: {op.description}")
                    click.echo(f"    Form fields: {len(op_set.all_fields)}")
                    for f in op_set.all_fields:
                        click.echo(f"      [{f.field_type}] {f.field_name or f.selector} - {f.label}")
                    if op_set.all_api_endpoints:
                        click.echo(f"    API endpoints: {len(op_set.all_api_endpoints)}")
                        for api in op_set.all_api_endpoints:
                            click.echo(f"      {api['method']} {api['path']}")
        except Exception as e:
            if verbose:
                click.echo(f"  Error processing {file_path}: {e}")

    if not all_op_sets:
        click.echo("No operations found in any file.")
        return

    click.echo(f"\n{'='*60}")
    click.echo(f"Total pages with operations: {len(all_op_sets)}")
    click.echo(f"Total operations: {total_ops}")
    click.echo(f"Total form fields: {total_fields}")

    # Generate Playwright scripts
    generated = generate_all(all_op_sets, output)

    click.echo(f"\nGenerated {len(generated)} Playwright test scripts:")
    for path in generated:
        click.echo(f"  {path}")

    # Write to database if specified
    if db:
        click.echo(f"\nWriting {len(all_op_sets)} test scripts to database...")
        db_path = os.path.abspath(db)
        scripts_written = write_to_db(all_op_sets, db_path, verbose=verbose)
        click.echo(f"  Written {scripts_written} test scripts to {db_path}")
        click.echo("")
        click.echo("Query test scripts:")
        click.echo(f"  uv run pygitnexus cypher \"MATCH (ts:TestScript) RETURN ts.name, ts.totalOps\" --db {db_path}")
        click.echo("Query test operations:")
        click.echo(f"  uv run pygitnexus cypher \"MATCH (to:TestOperation) RETURN to.opId, to.opType, to.handler\" --db {db_path}")

    click.echo(f"\n{'='*60}")
    click.echo("To run the generated tests:")
    click.echo(f"  uv run playwright install chromium")
    click.echo(f"  uv run pytest {output} -v")
    click.echo("")
