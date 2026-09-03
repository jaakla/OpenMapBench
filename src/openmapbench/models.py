from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class OutputKind(str, Enum):
    SCALAR = "scalar"
    TABLE = "table"
    VECTOR = "vector"
    RASTER = "raster"
    FILE = "file"


class RunStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    AGENT_ERROR = "agent_error"
    MISSING_OUTPUT = "missing_output"
    EVALUATOR_ERROR = "evaluator_error"


class InputSpec(BaseModel):
    path: str
    role: str | None = None
    source: str | None = None
    checksum: str | None = None
    as_of: str | None = None
    license: str | None = None

    @model_validator(mode="after")
    def path_is_present(self) -> InputSpec:
        if not self.path.strip():
            raise ValueError("input path must be non-empty")
        return self


class OutputSpec(BaseModel):
    path: str
    kind: OutputKind
    layer: str | None = None
    geometry_type: str | None = None
    crs: str | None = None
    required_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def output_path_is_safe(self) -> OutputSpec:
        output_path = Path(self.path)
        if output_path.is_absolute() or ".." in output_path.parts or not self.path.strip():
            raise ValueError("output path must be a non-empty relative path without '..'")
        return self


class EvaluationSpec(BaseModel):
    strict: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class TaskSpec(BaseModel):
    schema_version: Literal["0.1"] = "0.1"
    id: str
    title: str
    category: str
    prompt: str
    inputs: list[InputSpec] = Field(default_factory=list)
    output: OutputSpec
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def task_id_is_safe(self) -> TaskSpec:
        if not self.id or any(ch.isspace() for ch in self.id):
            raise ValueError("task id must be non-empty and contain no whitespace")
        return self

    def resolve_input_paths(self, task_file: Path) -> list[Path]:
        base = task_file.parent
        return [(base / item.path).resolve() for item in self.inputs]

    def resolve_output_path(self, output_dir: Path) -> Path:
        return (output_dir / self.output.path).resolve()


class FileRecord(BaseModel):
    path: str
    sha256: str | None = None
    size_bytes: int | None = None


class AuditToolInvocation(BaseModel):
    name: str
    server: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    sequence: int = Field(ge=1)
    event_id: str
    parent_event_id: str | None = None
    source: str
    kind: str
    name: str
    status: str | None = None
    command: str | list[str] | None = None
    tool: AuditToolInvocation | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    source_lines: list[int] = Field(default_factory=list)


class ArtifactLineageLink(BaseModel):
    relationship: Literal["produced_by", "derived_from", "declared_input", "referenced_by"]
    target_id: str
    evidence: str


class ContentCaptureObservation(BaseModel):
    """One moment at which the runner saw a file holding a specific content version."""

    observed_at: str
    reason: Literal[
        "file_change",
        "command_reference",
        "directory_sweep",
        "final_state",
    ]
    relationship: Literal["produced_by", "referenced_by"] = "referenced_by"
    event_id: str | None = None


class ArtifactContentCapture(BaseModel):
    """A preserved copy of one content version of a transient file."""

    sha256: str
    size_bytes: int = Field(ge=0)
    stored_path: str
    encoding: Literal["utf-8", "binary"]
    line_count: int | None = Field(default=None, ge=0)
    media_type: str | None = None
    first_observed_at: str
    last_observed_at: str
    observations: list[ContentCaptureObservation] = Field(default_factory=list)


class SkippedContentCapture(BaseModel):
    """A file the runner wanted to preserve but deliberately or unavoidably did not."""

    path: str
    reason: Literal["exceeds_max_file_bytes", "capture_budget_exhausted", "unreadable"]
    observed_at: str
    size_bytes: int | None = Field(default=None, ge=0)
    detail: str | None = None


class AuditContentStore(BaseModel):
    """Index of the run-local store that holds preserved content of transient files."""

    path: str
    file_count: int = Field(ge=0)
    version_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    policy: dict[str, Any] = Field(default_factory=dict)
    skipped: list[SkippedContentCapture] = Field(default_factory=list)


class AuditArtifact(BaseModel):
    artifact_id: str
    path: str
    role: Literal["task", "input", "intermediate", "working", "candidate", "reference", "log"]
    exists_at_finish: bool
    sha256: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None
    lineage: list[ArtifactLineageLink] = Field(default_factory=list)
    content_captures: list[ArtifactContentCapture] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditTrail(BaseModel):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    inner_trace_status: Literal["captured", "partial", "unavailable"]
    capture_sources: list[str] = Field(default_factory=list)
    events: list[AuditEvent] = Field(default_factory=list)
    artifacts: list[AuditArtifact] = Field(default_factory=list)
    content_store: AuditContentStore | None = None
    notes: list[str] = Field(default_factory=list)


class TokenUsage(BaseModel):
    source: str
    model: str | None = None
    reasoning_effort: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int = Field(ge=0)


class CostEstimate(BaseModel):
    currency: Literal["USD"] = "USD"
    model: str
    basis: Literal["token_breakdown", "total_tokens_range"]
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    minimum_cost_usd: float = Field(ge=0)
    maximum_cost_usd: float = Field(ge=0)
    pricing_as_of: str
    pricing_source: str
    rates_per_million_usd: dict[str, float]
    note: str


class TaskIsolation(BaseModel):
    """What the agent was actually pointed at, and what was withheld from it."""

    mode: Literal["staged", "direct"]
    task_file: str
    staged_inputs: list[str] = Field(default_factory=list)
    withheld_paths: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IntegrityFinding(BaseModel):
    """One recorded contact between the agent and material it was not meant to see."""

    path: str
    detail: str
    event_id: str | None = None
    sequence: int | None = None


class IntegrityReport(BaseModel):
    """Whether the run is admissible as evidence of capability."""

    checked: bool
    contaminated: bool
    withheld_paths: list[str] = Field(default_factory=list)
    findings: list[IntegrityFinding] = Field(default_factory=list)


class RunManifest(BaseModel):
    schema_version: Literal["0.1", "0.2", "0.3", "0.4", "0.5"] = "0.5"
    run_id: str
    status: RunStatus
    task_id: str
    task_title: str
    category: str
    task_metadata: dict[str, Any] = Field(default_factory=dict)
    output_kind: OutputKind
    task_file: FileRecord
    inputs: list[FileRecord] = Field(default_factory=list)
    candidate: FileRecord | None = None
    reference: FileRecord | None = None
    command: list[str]
    agent: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    benchmark_commit: str | None = None
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: int | None = None
    token_usage: TokenUsage | None = None
    cost_estimate: CostEstimate | None = None
    audit: AuditTrail | None = None
    isolation: TaskIsolation | None = None
    integrity: IntegrityReport | None = None
    evaluation: dict[str, Any] | None = None
    error: str | None = None
