"""CLI entry point for TestNexus."""

from __future__ import annotations

import click

from .analyze import analyze_cmd
from .impact import impact_cmd
from .list import list_cmd
from .status import status_cmd
from .clean import clean_cmd
from .modules_cmd import modules_cmd
from .llm_analyze import llm_analyze_cmd
from .report import report_cmd


@click.group()
@click.version_option(version="0.1.0", prog_name="testnexus")
def cli() -> None:
    """TestNexus — Test knowledge graph builder."""
    pass


cli.add_command(analyze_cmd)
cli.add_command(impact_cmd)
cli.add_command(list_cmd)
cli.add_command(status_cmd)
cli.add_command(clean_cmd)
cli.add_command(modules_cmd)
cli.add_command(llm_analyze_cmd)
cli.add_command(report_cmd)


if __name__ == "__main__":
    cli()
