"""CLI entry point for PyGitNexus."""

from __future__ import annotations

import click

from .analyze import analyze_cmd
from .list import list_cmd
from .status import status_cmd
from .clean import clean_cmd
from .query import query_cmd
from .context import context_cmd
from .cypher import cypher_cmd
from .mcp import mcp_cmd
from .setup import setup_cmd


@click.group()
@click.version_option(version="0.1.0", prog_name="pygitnexus")
def cli() -> None:
    """PyGitNexus - Java codebase knowledge graph builder."""
    pass


cli.add_command(analyze_cmd)
cli.add_command(list_cmd)
cli.add_command(status_cmd)
cli.add_command(clean_cmd)
cli.add_command(query_cmd)
cli.add_command(context_cmd)
cli.add_command(cypher_cmd)
cli.add_command(mcp_cmd)
cli.add_command(setup_cmd)


if __name__ == "__main__":
    cli()
