from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from ..models import EvaluationSpec, InputSpec, OutputKind, OutputSpec, TaskSpec
from ..taskio import sha256_file

LFS_PREFIX = "version https://git-lfs.github.com/spec/v1"
VECTOR_SUFFIXES = {".fgb", ".geojson", ".gpkg", ".jsonl", ".parquet", ".shp"}
JSON_RESULT_PATTERN = re.compile(
    r"^CHECK:JSON_VALUE:(?P<file>[^:]+):(?P<path>[^:]+):(?P<operator><=|>=|==|!=|<|>)(?P<value>.+)$",
    re.IGNORECASE,
)


def _is_lfs_pointer(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(len(LFS_PREFIX)).decode(errors="ignore") == LFS_PREFIX


def _git_commit(source: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _kind(path: Path) -> OutputKind:
    suffix = path.suffix.lower()
    if suffix in VECTOR_SUFFIXES:
        return OutputKind.VECTOR
    if suffix == ".csv":
        return OutputKind.TABLE
    if suffix in {".json", ".txt"}:
        return OutputKind.SCALAR
    if suffix in {".tif", ".tiff", ".nc"}:
        return OutputKind.RASTER
    return OutputKind.FILE


def _evaluation(kind: OutputKind) -> EvaluationSpec:
    if kind == OutputKind.TABLE:
        return EvaluationSpec(strict={"ignore_order": True})
    if kind == OutputKind.VECTOR:
        return EvaluationSpec(
            strict={
                "crs": "exact",
                "feature_count": "ignore",
                "geometry": {"metric": "auto", "tolerance": 1e-8, "require_valid": True},
            }
        )
    return EvaluationSpec(strict={"absolute_tolerance": 0.0, "relative_tolerance": 0.0})


def _result_contract(value: str, result_root: Path) -> tuple[Path, Path, EvaluationSpec | None]:
    match = JSON_RESULT_PATTERN.match(value.strip())
    if not match:
        name = Path(value.strip()).name
        return Path(name), result_root / name, None
    pattern = Path(match.group("file")).name
    matches = sorted(result_root.glob(pattern))
    name = matches[0].name if len(matches) == 1 else pattern.replace("*", "")
    predicate = {
        "path": match.group("path"),
        "operator": match.group("operator"),
        "value": yaml.safe_load(match.group("value")),
    }
    return Path(name), result_root / name, EvaluationSpec(strict={"json_checks": [predicate]})


def _dataset_index(source: Path) -> list[Path]:
    dataset = source / "dataset"
    return sorted(
        (path for path in dataset.rglob("*") if path.is_file() and "result" not in path.parts),
        key=lambda path: len(path.name),
        reverse=True,
    )


def _input_paths(description: str, index: list[Path]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for path in index:
        relative = path.as_posix().split("/dataset/", 1)[-1]
        if (path.name in description or f"dataset/{relative}" in description) and path not in seen:
            paths.append(path)
            seen.add(path)
    return sorted(paths)


def _source_entry(
    row: dict[str, str],
    *,
    source_row: int,
    output_path: Path,
    reference_path: Path,
    input_paths: list[Path],
    source_commit: str | None,
    hash_cache: dict[Path, str],
    hash_inputs: bool,
    evaluation_override: EvaluationSpec | None = None,
    suffix: str = "",
) -> tuple[TaskSpec, dict[str, Any]]:
    upstream_id = str(row.get("ID", source_row - 1)).strip()
    task_id = f"gabench-{int(upstream_id):03d}{suffix}"
    kind = _kind(output_path)
    inputs: list[InputSpec] = []
    for path in input_paths:
        if _is_lfs_pointer(path):
            raise ValueError(f"{path} is a Git LFS pointer; run 'git lfs pull' in GABench")
        checksum = None
        if hash_inputs:
            hash_cache.setdefault(path, sha256_file(path))
            checksum = f"sha256:{hash_cache[path]}"
        inputs.append(
            InputSpec(
                path=str(path.resolve()),
                role=path.stem,
                source=f"GeoX-Lab/GABench@{source_commit or 'unknown'}",
                checksum=checksum,
                license="UNDECLARED",
            )
        )
    prompt = row.get("Task Description", "").strip()
    if suffix:
        prompt += f"\n\nWrite the analytical artifact to {output_path.name}."
    spec = TaskSpec(
        id=task_id,
        title=f"GABench {upstream_id}: {row.get('Domain', 'GIS task').strip()}",
        category=row.get("Domain", "unknown").strip(),
        prompt=prompt,
        inputs=inputs,
        output=OutputSpec(path=output_path.name, kind=kind),
        evaluation=evaluation_override or _evaluation(kind),
        metadata={
            "adapter": "gabench",
            "upstream_id": upstream_id,
            "upstream_source_row": source_row,
            "upstream_commit": source_commit,
            "upstream_license": "UNDECLARED",
            "toolchain_length": row.get("Toolchain Length", "").strip(),
        },
    )
    supported = kind in {OutputKind.SCALAR, OutputKind.TABLE, OutputKind.VECTOR}
    if _is_lfs_pointer(reference_path):
        raise ValueError(
            f"{reference_path} is a Git LFS pointer; run 'git lfs pull' in GABench"
        )
    reference_exists = reference_path.is_file()
    entry = {
        "task_id": task_id,
        "upstream_id": upstream_id,
        "output_kind": kind.value,
        "reference_path": str(reference_path.resolve()),
        "reference_exists": reference_exists,
        "reference_sha256": sha256_file(reference_path) if reference_exists else None,
        "deterministic_supported": supported and reference_exists,
        "classification_reason": (
            "supported deterministic artifact"
            if supported and reference_exists
            else "reference file missing"
            if supported
            else f"{kind.value} evaluator is outside the MVP"
        ),
    }
    return spec, entry


def import_gabench(
    source: Path,
    output: Path,
    *,
    reference_root: Path | None = None,
    hash_inputs: bool = True,
) -> dict[str, Any]:
    """Create local bridge tasks and a manifest without copying upstream data."""
    source = source.resolve()
    output = output.resolve()
    csv_path = source / "benchmark" / "benchmark.csv"
    if not csv_path.is_file():
        raise ValueError(f"missing {csv_path}")
    if _is_lfs_pointer(csv_path):
        raise ValueError("benchmark.csv is a Git LFS pointer; run 'git lfs pull' in GABench")
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = [column for column in (reader.fieldnames or []) if column]
    required = {"ID", "Domain", "Task Description", "Data Description", "Result", "Layers"}
    missing = sorted(required - set(columns))
    if missing:
        raise ValueError(f"GABench benchmark.csv is missing columns: {', '.join(missing)}")

    source_commit = _git_commit(source)
    index = _dataset_index(source)
    hash_cache: dict[Path, str] = {}
    tasks_dir = output / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for source_row, row in enumerate(rows, start=2):
        inputs = _input_paths(row.get("Data Description", ""), index)
        result_path, result_reference, result_evaluation = _result_contract(
            row["Result"], source / "dataset" / "result"
        )
        candidates = [(result_path, result_reference, "", result_evaluation)]
        if reference_root:
            for layer_index, layer_name in enumerate(row.get("Layers", "").splitlines(), start=1):
                relative_layer = Path(layer_name.strip())
                clean_name = relative_layer.name
                if not clean_name:
                    continue
                layer_reference = reference_root.resolve() / relative_layer
                if not layer_reference.is_file():
                    layer_reference = reference_root.resolve() / clean_name
                if layer_reference.is_file():
                    candidates.append(
                        (Path(clean_name), layer_reference, f"-layer-{layer_index:02d}", None)
                    )
        for output_path, reference_path, suffix, evaluation_override in candidates:
            spec, entry = _source_entry(
                row,
                source_row=source_row,
                output_path=output_path,
                reference_path=reference_path,
                input_paths=inputs,
                source_commit=source_commit,
                hash_cache=hash_cache,
                hash_inputs=hash_inputs,
                evaluation_override=evaluation_override,
                suffix=suffix,
            )
            task_dir = tasks_dir / spec.id
            task_dir.mkdir(parents=True, exist_ok=True)
            task_path = task_dir / "task.yaml"
            task_path.write_text(
                yaml.safe_dump(
                    spec.model_dump(mode="json", exclude_none=True),
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            entry["task_path"] = str(task_path.resolve())
            entries.append(entry)

    kinds = Counter(entry["output_kind"] for entry in entries)
    manifest = {
        "adapter": "gabench",
        "schema_version": "0.1",
        "source_root": str(source),
        "source_commit": source_commit,
        "source_csv": str(csv_path.resolve()),
        "source_csv_sha256": sha256_file(csv_path),
        "upstream_license": "UNDECLARED" if not (source / "LICENSE").exists() else "SEE_UPSTREAM",
        "source_task_count": len(rows),
        "generated_task_count": len(entries),
        "deterministic_supported_count": sum(
            bool(entry["deterministic_supported"]) for entry in entries
        ),
        "output_kind_counts": dict(sorted(kinds.items())),
        "hash_inputs": hash_inputs,
        "reference_root": str(reference_root.resolve()) if reference_root else None,
        "tasks": entries,
        "notice": (
            "Local interoperability metadata only. No GABench task, dataset, or reference "
            "artifact was copied. Keep this generated directory outside version control while "
            "the upstream repository has no declared license."
        ),
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
