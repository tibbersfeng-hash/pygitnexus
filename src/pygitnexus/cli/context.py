"""Context command: get full context for a symbol."""

from __future__ import annotations

import click

from ..search.query import symbol_context as sym_context
from ._common import load_store


@click.command("context")
@click.argument("name")
def context_cmd(name: str) -> None:
    """Show full context (callers, callees, imports) for a symbol."""
    store = load_store()
    try:
        ctx = sym_context(store, name)
    finally:
        store.close()

    click.echo(f"Context for '{name}':\n")

    # Callers
    callers = ctx.get("callers", [])
    click.echo(f"Callers ({len(callers)}):")
    if callers:
        for c in callers:
            caller_class = c.get("callerClass", "")
            caller_name = c.get("caller", "")
            confidence = c.get("confidence", 0)
            prefix = f"{caller_class}." if caller_class else ""
            click.echo(f"  - {prefix}{caller_name} (confidence: {confidence:.2f})")
    else:
        click.echo("  (none)")

    click.echo()

    # Callees
    callees = ctx.get("callees", [])
    click.echo(f"Callees ({len(callees)}):")
    if callees:
        for c in callees:
            callee_class = c.get("calleeClass", "")
            callee_name = c.get("callee", "")
            confidence = c.get("confidence", 0)
            prefix = f"{callee_class}." if callee_class else ""
            click.echo(f"  -> {prefix}{callee_name} (confidence: {confidence:.2f})")
    else:
        click.echo("  (none)")

    click.echo()

    # Imports
    imports = ctx.get("imports", [])
    click.echo(f"Imports ({len(imports)}):")
    if imports:
        for imp in imports:
            target = imp.get("target", "")
            reason = imp.get("reason", "")
            reason_str = f" ({reason})" if reason else ""
            click.echo(f"  > {target}{reason_str}")
    else:
        click.echo("  (none)")
