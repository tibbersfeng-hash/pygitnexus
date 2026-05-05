"""LLM analyzer: orchestrate LLM analysis and store results in TestGraph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..graph.store import GraphStore
from .llm_provider import LLMConfig, LLMProvider, LLMProviderError
from .metadata_extractor import MetadataExtractor, ProjectMetadata


@dataclass
class LLMModuleResult:
    """A single module inferred by the LLM."""
    name: str
    description: str
    confidence: float
    page_names: list[str] = field(default_factory=list)
    endpoint_refs: list[str] = field(default_factory=list)
    action_names: list[str] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    belongs_to: str = ""


@dataclass
class WorkflowInfo:
    """A workflow relationship between modules."""
    from_module: str
    to_module: str
    description: str
    workflow_type: str  # data_flow | dependency | user_journey


@dataclass
class EndpointClassification:
    """Business classification of an endpoint."""
    endpoint: str  # "GET /path"
    business_purpose: str
    category: str  # crud | workflow | auth | reporting | admin | notification | health


SYSTEM_PROMPT = """\
You are a software architecture analyst. Analyze the provided codebase metadata and identify \
business modules, their relationships, and data flows.

Respond ONLY with valid JSON matching this schema (no extra text, no markdown fences):

{
  "modules": [
    {
      "name": "string (TitleCase, concise business module name)",
      "description": "string (1-2 sentences describing the module's business responsibility)",
      "confidence": 0.0-1.0,
      "pages": ["page_name", ...],
      "endpoints": ["Controller.method", ...],
      "actions": ["action_name", ...],
      "data_sources": ["database_table", "external_api", "cache", ...],
      "belongs_to": "string (parent module name, empty for top-level modules)"
    }
  ],
  "workflows": [
    {
      "from_module": "string",
      "to_module": "string",
      "description": "string (what data/operations flow between these modules)",
      "type": "data_flow | dependency | user_journey"
    }
  ],
  "endpoint_classifications": [
    {
      "endpoint": "METHOD /path",
      "business_purpose": "string (what business operation this endpoint performs)",
      "category": "crud | workflow | auth | reporting | admin | notification | health"
    }
  ]
}

Rules:
- Modules should represent coherent business capabilities, not technical layers.
- Group related entities together; avoid overly granular modules.
- Descriptions must be specific to this codebase based on actual content, not generic.
- Include only entities that actually appear in the metadata provided.
- For data_sources, identify database tables, external services, or caches from the code.
- If a module is clearly a sub-module of another, set belongs_to accordingly.
- endpoint_classifications should cover ALL endpoints in the metadata.
"""


class LLMAnalyzer:
    """Orchestrates LLM analysis on project metadata."""

    def __init__(self, provider: LLMProvider, store: GraphStore) -> None:
        self.provider = provider
        self.store = store

    def analyze(self, metadata: ProjectMetadata, extractor=None) -> tuple[
        list[LLMModuleResult],
        list[WorkflowInfo],
        list[EndpointClassification],
    ]:
        """Run LLM analysis on project metadata."""
        if extractor is not None:
            user_content = extractor.serialize_for_llm()
        else:
            user_content = str(metadata)

        raw = self.provider.chat_json(SYSTEM_PROMPT, user_content)

        modules = self._parse_modules(raw.get("modules", []))
        workflows = self._parse_workflows(raw.get("workflows", []))
        classifications = self._parse_classifications(
            raw.get("endpoint_classifications", [])
        )

        return modules, workflows, classifications

    def store_results(
        self,
        modules: list[LLMModuleResult],
        workflows: list[WorkflowInfo],
        classifications: list[EndpointClassification],
        model_name: str,
        raw_output: str = "",
    ) -> str:
        """Write LLM results to graph. Returns analysis_id."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        analysis_id = f"LLMAnalysis_{ts}"
        prompt_hash = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]

        # Create LLMAnalysis node
        self.store.insert_node("LLMAnalysis", {
            "id": analysis_id,
            "analysisType": "module_inference",
            "modelName": model_name,
            "promptHash": prompt_hash,
            "rawOutput": raw_output[:10000] if raw_output else "",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })

        # Track created data sources to avoid duplicates
        created_data_sources: dict[str, str] = {}

        # Create DataSource nodes and relations
        for mod in modules:
            for ds_name in mod.data_sources:
                ds_id = f"DataSource_{ds_name.replace(' ', '_')}"
                if ds_id not in created_data_sources:
                    # Infer source type from name
                    source_type = "unknown"
                    if "table" in ds_name.lower() or ds_name.endswith("_table"):
                        source_type = "database_table"
                    elif "api" in ds_name.lower() or "service" in ds_name.lower():
                        source_type = "external_api"
                    elif "cache" in ds_name.lower() or "redis" in ds_name.lower():
                        source_type = "cache"
                    elif "queue" in ds_name.lower() or "mq" in ds_name.lower():
                        source_type = "message_queue"

                    self.store.insert_node("DataSource", {
                        "id": ds_id,
                        "sourceType": source_type,
                        "name": ds_name,
                        "filePath": "",
                        "description": f"Data source for {mod.name} module",
                    })
                    created_data_sources[ds_id] = ds_name

                # LLMAnalysis → DataSource
                self.store.insert_relation(
                    "LLMAnalysis", "DataSource", analysis_id, ds_id,
                    "IDENTIFIES", mod.confidence,
                    f"LLM identified as data source for {mod.name}",
                )

                # DataSource → Module
                mod_id = f"Module_{mod.name}"
                self.store.insert_relation(
                    "DataSource", "Module", ds_id, mod_id,
                    "SUPPORTS", mod.confidence,
                    f"{ds_name} supports {mod.name} module",
                )

        # LLMAnalysis → Module relations (ANALYZED)
        for mod in modules:
            mod_id = f"Module_{mod.name}"
            reason_parts = []
            if mod.description:
                reason_parts.append(mod.description)
            if mod.page_names:
                reason_parts.append(f"Pages: {', '.join(mod.page_names[:5])}")
            if mod.endpoint_refs:
                reason_parts.append(f"Endpoints: {', '.join(mod.endpoint_refs[:5])}")

            self.store.insert_relation(
                "LLMAnalysis", "Module", analysis_id, mod_id,
                "ANALYZED", mod.confidence,
                " | ".join(reason_parts) if reason_parts else "LLM inferred module",
            )

            # Update Module node with LLM-enhanced description
            self._update_module_description(mod_id, mod.description)

        # Module → Module relations (WORKFLOW)
        for wf in workflows:
            from_id = f"Module_{wf.from_module}"
            to_id = f"Module_{wf.to_module}"
            self.store.insert_relation(
                "Module", "Module", from_id, to_id,
                "WORKFLOW", 0.8,
                f"[{wf.workflow_type}] {wf.description}",
            )

        # LLMAnalysis → Endpoint relations (CLASSIFIED)
        for cls in classifications:
            # Find matching endpoint by method + path
            parts = cls.endpoint.split(" ", 1)
            if len(parts) == 2:
                method, path = parts
                ep_result = self.store.query(
                    f"MATCH (e:Endpoint) WHERE e.method = '{method}' AND e.path = '{path}' "
                    "RETURN e.id LIMIT 1"
                )
                if ep_result:
                    ep_id = ep_result[0].get("e.id", "")
                    self.store.insert_relation(
                        "LLMAnalysis", "Endpoint", analysis_id, ep_id,
                        "CLASSIFIED", 0.85,
                        f"[{cls.category}] {cls.business_purpose}",
                    )

                    # DataSource → Endpoint relations
                    for ds_name in self._find_data_sources_for_endpoint(
                        modules, cls.endpoint
                    ):
                        ds_id = f"DataSource_{ds_name.replace(' ', '_')}"
                        if ds_id in created_data_sources:
                            self.store.insert_relation(
                                "DataSource", "Endpoint", ds_id, ep_id,
                                "SUPPORTS", 0.7,
                                f"{ds_name} supports {cls.business_purpose}",
                            )

        return analysis_id

    def update_modules(self, modules: list[LLMModuleResult]) -> None:
        """Update Module nodes with LLM-enhanced descriptions."""
        for mod in modules:
            mod_id = f"Module_{mod.name}"
            self._update_module_description(mod_id, mod.description)

    def _update_module_description(self, mod_id: str, description: str) -> None:
        """Update a Module node's description using Cypher SET."""
        if not description:
            return
        escaped = description.replace("'", "''")
        self.store._execute(
            f"MATCH (m:Module {{id: '{mod_id}'}}) SET m.description = '{escaped}'",
        )

    def _find_data_sources_for_endpoint(
        self, modules: list[LLMModuleResult], endpoint: str,
    ) -> list[str]:
        """Find data sources associated with modules that contain this endpoint."""
        sources: list[str] = []
        for mod in modules:
            if endpoint in mod.endpoint_refs:
                sources.extend(mod.data_sources)
        return sorted(set(sources))

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_modules(self, raw: list[dict]) -> list[LLMModuleResult]:
        results = []
        for item in raw:
            results.append(LLMModuleResult(
                name=item.get("name", ""),
                description=item.get("description", ""),
                confidence=float(item.get("confidence", 0.5)),
                page_names=item.get("pages", []),
                endpoint_refs=item.get("endpoints", []),
                action_names=item.get("actions", []),
                data_sources=item.get("data_sources", []),
                belongs_to=item.get("belongs_to", ""),
            ))
        return results

    def _parse_workflows(self, raw: list[dict]) -> list[WorkflowInfo]:
        results = []
        for item in raw:
            results.append(WorkflowInfo(
                from_module=item.get("from_module", ""),
                to_module=item.get("to_module", ""),
                description=item.get("description", ""),
                workflow_type=item.get("type", "data_flow"),
            ))
        return results

    def _parse_classifications(self, raw: list[dict]) -> list[EndpointClassification]:
        results = []
        for item in raw:
            results.append(EndpointClassification(
                endpoint=item.get("endpoint", ""),
                business_purpose=item.get("business_purpose", ""),
                category=item.get("category", "workflow"),
            ))
        return results
