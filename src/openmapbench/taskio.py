from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from .models import TaskSpec


def load_task(path: str | Path) -> TaskSpec:
    task_path = Path(path)
    payload = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    return TaskSpec.model_validate(payload)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_task_files(task: TaskSpec, task_file: Path) -> list[dict[str, str]]:
    """Return explicit file/provenance validation findings for a task."""
    findings: list[dict[str, str]] = []
    for input_spec, path in zip(task.inputs, task.resolve_input_paths(task_file), strict=True):
        if not path.exists():
            findings.append({"status": "failed", "path": str(path), "reason": "missing input"})
            continue
        if not path.is_file():
            findings.append({"status": "failed", "path": str(path), "reason": "not a file"})
            continue
        if input_spec.checksum:
            actual = sha256_file(path)
            expected = input_spec.checksum.removeprefix("sha256:")
            status = "passed" if actual == expected else "failed"
            findings.append(
                {
                    "status": status,
                    "path": str(path),
                    "reason": "checksum matches" if status == "passed" else "checksum mismatch",
                }
            )
        else:
            findings.append({"status": "warning", "path": str(path), "reason": "no checksum"})
    return findings
