"""Report command: generate HTML report from TestGraph analysis results."""

from __future__ import annotations

from pathlib import Path

import click

from ..graph.store import GraphStore
from ..report.html_report import HTMLReportGenerator
from ..storage.meta import get_project, list_projects


@click.command("report")
@click.option("--output", default="testnexus_report.html", help="Output HTML file path.")
@click.option("--project", default=None, help="Project name (auto-detected if omitted).")
@click.option("--backend", default=None, help="Backend source path (for LLM analysis).")
def report_cmd(output: str, project: str | None, backend: str | None) -> None:
    """Generate an HTML report from TestGraph analysis results."""
    db_path = _find_db(project)
    click.echo(f"Loading TestGraph from: {db_path}")

    store = GraphStore(db_path)

    # Auto-initialize missing dependencies
    frontend_path = db_path.parent.parent
    _auto_init_deps(store, frontend_path, backend)

    project_name = project or db_path.parent.parent.name
    generator = HTMLReportGenerator(store, project_name=project_name)
    result_path = generator.generate(output)
    store.close()

    click.echo(f"Report generated: {result_path}")


def _auto_init_deps(store: GraphStore, frontend_path: Path, backend: str | None) -> None:
    """Auto-initialize missing dependencies before report generation."""
    from ..storage.meta import get_project

    project_name = frontend_path.name
    proj = get_project(project_name)
    proj_backend = backend
    if proj and proj_backend is None and hasattr(proj, "backend_path"):
        proj_backend = proj.backend_path

    # Check LLMAnalysis nodes — auto run if API key available
    llm_result = store.query("MATCH (a:LLMAnalysis) RETURN count(a) AS cnt")
    llm_count = llm_result[0].get("cnt", 0) if llm_result else 0
    if llm_count == 0:
        click.echo("  Checking LLM analysis availability...")
        try:
            from ..core.llm_provider import resolve_llm_config, LLMProvider, LLMProviderError
            from ..core.llm_analyzer import LLMAnalyzer
            from ..core.metadata_extractor import MetadataExtractor

            config = resolve_llm_config()
            if not config.api_key:
                click.echo("    LLM analysis skipped: no API key configured")
                return

            click.echo(f"  Auto-initializing: LLM analysis ({config.provider}/{config.model})...")
            extractor = MetadataExtractor(
                store, frontend_path,
                Path(proj_backend) if proj_backend else None,
            )
            provider = LLMProvider(config)
            analyzer = LLMAnalyzer(provider, store)
            modules, workflows, classifications = analyzer.analyze(
                extractor.extract_all(), extractor=extractor
            )
            analyzer.store_results(modules, workflows, classifications, config.model)
            provider.close()
            click.echo(f"    LLM analysis complete ({len(modules)} modules, {len(workflows)} workflows)")
        except LLMProviderError as e:
            click.echo(f"    LLM analysis skipped: {e}")
        except Exception as e:
            click.echo(f"    LLM analysis skipped: {e}")


def _find_db(project: str | None) -> Path:
    """Find the TestGraph KuzuDB path."""
    if project:
        proj = get_project(project)
        if proj is None:
            raise SystemExit(f"Project '{project}' not found in registry.")
        db_path = Path(proj.frontend_path) / ".testnexus" / "kuzu"
        if db_path.exists():
            return db_path
        raise SystemExit(f"No TestGraph database for project '{project}'.")

    # Try current directory
    repo_path = Path(".").resolve()
    db_path = repo_path / ".testnexus" / "kuzu"
    if db_path.exists():
        return db_path

    # Try registered projects
    for p in list_projects():
        db_path = Path(p.frontend_path) / ".testnexus" / "kuzu"
        if db_path.exists():
            return db_path

    raise SystemExit(
        "No TestGraph database found. Run 'testnexus analyze --frontend <path>' first."
    )
