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
from .group import group_cmd
from .web import web_cmd
from .autogen import autogen_cmd


@click.group()
@click.version_option(version="0.1.0", prog_name="pygitnexus")
def cli() -> None:
    """PyGitNexus — Java codebase knowledge graph builder + TestNexus testing."""
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
cli.add_command(group_cmd)
cli.add_command(web_cmd)
cli.add_command(autogen_cmd)


# ------------------------------------------------------------------
# TestNexus subcommand group: test <subcommand>
# ------------------------------------------------------------------

@click.group(name="test")
def test_group() -> None:
    """TestNexus — Frontend test knowledge graph builder."""
    pass


from ..testnexus.cli.analyze import analyze_cmd as tn_analyze_cmd
from ..testnexus.cli.impact import impact_cmd as tn_impact_cmd
from ..testnexus.cli.list import list_cmd as tn_list_cmd
from ..testnexus.cli.status import status_cmd as tn_status_cmd
from ..testnexus.cli.clean import clean_cmd as tn_clean_cmd
from ..testnexus.cli.modules_cmd import modules_cmd
from ..testnexus.cli.llm_analyze import llm_analyze_cmd
from ..testnexus.cli.report import report_cmd

test_group.add_command(tn_analyze_cmd)
test_group.add_command(tn_impact_cmd)
test_group.add_command(tn_list_cmd)
test_group.add_command(tn_status_cmd)
test_group.add_command(tn_clean_cmd)
test_group.add_command(modules_cmd)
test_group.add_command(llm_analyze_cmd)
test_group.add_command(report_cmd)

cli.add_command(test_group)


if __name__ == "__main__":
    cli()
