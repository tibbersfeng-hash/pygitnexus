"""LLM analyze command: run LLM-powered business module analysis."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..core.llm_analyzer import LLMAnalyzer
from ..core.llm_provider import LLMConfig, LLMProvider, LLMProviderError
from ..core.metadata_extractor import MetadataExtractor
from ..graph.store import GraphStore
from . _common import find_testnexus_db


@click.command("llm-analyze")
@click.option("--provider", default=None, help="LLM provider: openai or anthropic (env: TESTNEXUS_LLM_PROVIDER).")
@click.option("--llm-url", default=None, help="LLM API base URL (env: TESTNEXUS_LLM_URL).")
@click.option("--api-key", default=None, help="API key (env: TESTNEXUS_API_KEY).")
@click.option("--model", default=None, help="LLM model name (env: TESTNEXUS_LLM_MODEL).")
@click.option("--frontend", default=None, help="Frontend source path for metadata enrichment.")
@click.option("--backend", default=None, help="Backend source path for metadata enrichment.")
@click.option("--dry-run", is_flag=True, help="Show metadata that would be sent to LLM without calling API.")
@click.option("--output", "output_file", default=None, help="Save LLM JSON result to file.")
def llm_analyze_cmd(
    provider: str | None,
    llm_url: str | None,
    api_key: str | None,
    model: str | None,
    frontend: str | None,
    backend: str | None,
    dry_run: bool,
    output_file: str | None,
) -> None:
    """Run LLM-powered business module analysis on existing TestGraph."""
    import os

    from ..core.llm_provider import resolve_llm_config

    # Resolve config with priority: CLI > ~/.claude/settings.json > env vars
    config = resolve_llm_config(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=llm_url,
    )

    # Find the database
    db_path = find_testnexus_db()
    click.echo(f"Loading TestGraph from: {db_path}")

    store = GraphStore(db_path)

    # Resolve paths
    frontend_path = Path(frontend) if frontend else Path(db_path).parent.parent
    backend_path = Path(backend) if backend else None

    if not frontend_path.exists():
        click.echo(f"Warning: frontend path not found: {frontend_path}")

    # Extract metadata
    click.echo("Extracting project metadata...")
    extractor = MetadataExtractor(store, frontend_path, backend_path)

    if dry_run:
        serialized = extractor.serialize_for_llm()
        click.echo("\n" + "=" * 60)
        click.echo("METADATA TO BE SENT TO LLM (dry run):")
        click.echo("=" * 60)
        click.echo(serialized)
        click.echo("=" * 60)
        store.close()
        return

    # Create LLM provider
    try:
        provider_inst = LLMProvider(config)
    except LLMProviderError as e:
        click.echo(f"Error: {e}", err=True)
        store.close()
        raise SystemExit(1)

    analyzer = LLMAnalyzer(provider_inst, store)

    # Run analysis
    click.echo(f"Calling LLM ({config.provider}/{config.model})...")
    try:
        modules, workflows, classifications = analyzer.analyze(extractor.extract_all())
    except LLMProviderError as e:
        click.echo(f"LLM analysis failed: {e}", err=True)
        provider_inst.close()
        store.close()
        raise SystemExit(1)

    # Save raw output if requested
    if output_file:
        raw_output = {
            "modules": [
                {
                    "name": m.name,
                    "description": m.description,
                    "confidence": m.confidence,
                    "data_sources": m.data_sources,
                    "belongs_to": m.belongs_to,
                }
                for m in modules
            ],
            "workflows": [
                {
                    "from_module": w.from_module,
                    "to_module": w.to_module,
                    "description": w.description,
                    "type": w.workflow_type,
                }
                for w in workflows
            ],
            "endpoint_classifications": [
                {
                    "endpoint": c.endpoint,
                    "business_purpose": c.business_purpose,
                    "category": c.category,
                }
                for c in classifications
            ],
        }
        Path(output_file).write_text(json.dumps(raw_output, indent=2))
        click.echo(f"Results saved to: {output_file}")

    # Store results in graph
    click.echo("Storing results in TestGraph...")
    analysis_id = analyzer.store_results(modules, workflows, classifications, config.model)

    # Summary
    click.echo(f"\nLLM Analysis complete! (ID: {analysis_id})")
    click.echo(f"\nModules analyzed: {len(modules)}")
    for mod in modules:
        click.echo(f"  [{mod.confidence:.0%}] {mod.name}: {mod.description}")
        if mod.data_sources:
            click.echo(f"    Data sources: {', '.join(mod.data_sources)}")

    click.echo(f"\nWorkflows discovered: {len(workflows)}")
    for wf in workflows:
        click.echo(f"  {wf.from_module} → {wf.to_module} [{wf.workflow_type}]: {wf.description}")

    click.echo(f"\nEndpoint classifications: {len(classifications)}")
    for cls in classifications[:10]:
        click.echo(f"  {cls.endpoint} → [{cls.category}] {cls.business_purpose}")
    if len(classifications) > 10:
        click.echo(f"  ... and {len(classifications) - 10} more")

    provider_inst.close()
    store.close()
