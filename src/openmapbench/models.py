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
    relationship: Literal["produced_by", "derived_from", "declared_input"]
    target_id: str
    evidence: str


class AuditArtifact(BaseModel):
    artifact_id: str
    path: str
    role: Literal["task", "input", "intermediate", "working", "candidate", "reference", "log"]
    exists_at_finish: bool
    sha256: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None
    lineage: list[ArtifactLineageLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditTrail(BaseModel):
    schema_version: Literal["0.1"] = "0.1"
    inner_trace_status: Literal["captured", "partial", "unavailable"]
    capture_sources: list[str] = Field(default_factory=list)
    events: list[AuditEvent] = Field(default_factory=list)
    artifacts: list[AuditArtifact] = Field(default_factory=list)
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


class RunManifest(BaseModel):
    schema_version: Literal["0.1", "0.2", "0.3"] = "0.3"
    run_id: str
    status: RunStatus
    task_id: str
    task_title: str
    category: str
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
    evaluation: dict[str, Any] | None = None
    error: str | None = None
