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


class RunManifest(BaseModel):
    schema_version: Literal["0.1"] = "0.1"
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
    evaluation: dict[str, Any] | None = None
    error: str | None = None
