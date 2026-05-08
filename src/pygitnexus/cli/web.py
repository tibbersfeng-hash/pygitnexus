"""Web dashboard command: start the HTTP dashboard server."""

from __future__ import annotations

import click


@click.command("web")
@click.option("--host", default="0.0.0.0", help="Bind address.")
@click.option("--port", default=8000, type=int, help="Port to serve on.")
@click.option("--repo", default=None, help="Repository name or path.")
@click.option("--open/--no-open", default=True, help="Open browser after start.")
def web_cmd(host: str, port: int, repo: str | None, open: bool) -> None:
    """Launch the web dashboard in your browser."""
    from ..web.server import start_web

    browser_url = f"http://127.0.0.1:{port}"
    click.echo(f"Starting PyGitNexus Dashboard at {browser_url}")

    if open:
        import threading
        import webbrowser

        def _open_browser():
            import time
            time.sleep(1.2)
            try:
                webbrowser.open(browser_url)
            except Exception:
                pass

        threading.Thread(target=_open_browser, daemon=True).start()

    start_web(host=host, port=port, repo=repo)
