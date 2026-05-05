"""Core data models for TestNexus."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Page:
    """A frontend page."""
    id: str
    name: str
    file_path: str
    url: str = ""
    platform: str = ""  # vue / react / desktop


@dataclass
class Component:
    """A frontend component."""
    id: str
    name: str
    file_path: str
    type: str = "component"  # page / component / layout
    platform: str = ""


@dataclass
class Action:
    """A user interaction or API call in a component."""
    id: str
    name: str
    type: str = "fetch"  # click / submit / fetch / navigate / watch
    selector: str = ""
    component_name: str = ""
    http_method: str = ""
    api_path: str = ""
    line_number: int = 0


@dataclass
class Endpoint:
    """A backend API endpoint."""
    id: str
    method: str = ""  # GET / POST / PUT / DELETE / PATCH
    path: str = ""
    controller_name: str = ""
    function_name: str = ""
    parameters: str = "{}"  # JSON
    response_type: str = ""


@dataclass
class TestCase:
    """A generated test case."""
    id: str
    name: str
    type: str = "api"  # api / e2e
    file_path: str = ""
    target_id: str = ""  # Endpoint or Action ID
    status: str = "generated"  # generated / executed / passed / failed / skipped


@dataclass
class TestRecord:
    """A runtime execution record."""
    id: str
    test_case_id: str = ""
    request: str = "{}"  # JSON
    response: str = "{}"  # JSON
    status_code: int = 0
    timestamp: str = ""
    baseline_id: str = ""
    duration: int = 0  # ms


@dataclass
class Baseline:
    """A baseline snapshot."""
    id: str
    name: str
    created_at: str = ""
    test_count: int = 0
    source: str = "manual"  # manual / auto / scheduled


@dataclass
class APICall:
    """An API call extracted from frontend code."""
    http_method: str
    api_path: str
    line_number: int
    file_path: str
    component: str = ""
    receiver: str = ""  # axios / fetch / custom service
    has_await: bool = False


@dataclass
class LLMAnalysis:
    """An LLM-powered analysis result."""
    id: str
    analysis_type: str = "module_inference"
    model_name: str = ""
    prompt_hash: str = ""
    raw_output: str = ""
    created_at: str = ""


@dataclass
class DataSource:
    """A data source identified by LLM analysis."""
    id: str
    source_type: str = ""          # database_table, external_api, cache, message_queue
    name: str = ""
    file_path: str = ""
    description: str = ""
