"""MCP command: start the MCP server for AI editors."""

from __future__ import annotations

import click

from ..mcp.server import create_server


@click.command("mcp")
def mcp_cmd() -> None:
    """Start MCP server (stdio) for AI editors like Cursor, Claude Code, and Codex."""
    import logging
    logging.getLogger("mcp").setLevel(logging.WARNING)

    server = create_server()
    server.run(transport="stdio")
