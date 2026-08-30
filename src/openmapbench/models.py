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


class InputSpec(BaseModel):
    path: str
    role: str | None = None
    source: str | None = None
    checksum: str | None = None
    as_of: str | None = None
    license: str | None = None


class OutputSpec(BaseModel):
    path: str
    kind: OutputKind
    geometry_type: str | None = None
    crs: str | None = None
    required_fields: list[str] = Field(default_factory=list)


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
    def task_id_is_safe(self) -> "TaskSpec":
        if not self.id or any(ch.isspace() for ch in self.id):
            raise ValueError("task id must be non-empty and contain no whitespace")
        return self

    def resolve_input_paths(self, task_file: Path) -> list[Path]:
        base = task_file.parent
        return [(base / item.path).resolve() for item in self.inputs]
