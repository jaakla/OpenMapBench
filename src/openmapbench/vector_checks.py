"""Reference-independent predicate checks for vector artifacts.

These checks relate a candidate layer to the task's own declared inputs or to itself, so a
task can score properties such as "every point lies inside its source polygon" without a
reference artifact that fixes one particular correct answer. Each check returns a
``(check_id, passed, details)`` triple; the evaluator wraps them into its check list.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

RELATION_PREDICATES = {"within", "covered_by", "intersects", "contains", "covers", "disjoint"}
DETAIL_LIMIT = 50


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, float) and math.isnan(value):
        return True
    return type(value).__name__ in {"NAType", "NaTType"}


def _load_input(
    spec: dict[str, Any],
    inputs: dict[str, Path],
    read_vector: Callable[[Path], Any],
    target_crs: Any,
) -> Any:
    role = spec.get("input_role")
    if not role:
        raise ValueError(f"vector check {spec.get('type')!r} requires input_role")
    if role not in inputs:
        raise ValueError(
            f"vector check {spec.get('type')!r} references input role {role!r}, which the task "
            "does not declare or which is unavailable to the evaluator"
        )
    frame = read_vector(inputs[role])
    if frame.crs is not None and target_crs is not None and frame.crs != target_crs:
        frame = frame.to_crs(target_crs)
    return frame


def _group_union(frame: Any, key: str) -> dict[str, Any]:
    from shapely import union_all

    groups: dict[str, list[Any]] = {}
    for value, geometry in zip(frame[key].astype(str), frame.geometry, strict=True):
        groups.setdefault(value, []).append(geometry)
    return {value: union_all(geometries) for value, geometries in groups.items()}


def _require_field(frame: Any, field: str, check_type: str) -> None:
    if field not in frame.columns:
        raise ValueError(f"vector check {check_type!r} needs field {field!r} in the candidate")


def _check_relation(
    spec: dict[str, Any],
    frame: Any,
    inputs: dict[str, Path],
    read_vector: Callable[[Path], Any],
) -> tuple[bool, dict[str, Any]]:
    predicate = spec.get("predicate") or spec.get("type")
    if predicate not in RELATION_PREDICATES:
        raise ValueError(f"unsupported spatial relation predicate: {predicate}")
    key = spec.get("key")
    if not key:
        raise ValueError("relation check requires key naming the candidate field to join on")
    input_key = spec.get("input_key", key)
    _require_field(frame, key, "relation")
    input_frame = _load_input(spec, inputs, read_vector, frame.crs)
    if input_key not in input_frame.columns:
        raise ValueError(f"relation check needs field {input_key!r} in input {spec['input_role']!r}")
    targets = _group_union(input_frame, input_key)
    failures: list[dict[str, Any]] = []
    for value, geometry in zip(frame[key].astype(str), frame.geometry, strict=True):
        target = targets.get(value)
        if target is None:
            failures.append({"key": value, "reason": "no input feature with this key"})
        elif not getattr(geometry, predicate)(target):
            failures.append({"key": value, "reason": f"not {predicate}"})
    details = {
        "predicate": predicate,
        "input_role": spec["input_role"],
        "checked": len(frame),
        "failed": len(failures),
        "failures": failures[:DETAIL_LIMIT],
    }
    return not failures, details


def _check_count_per_input(
    spec: dict[str, Any],
    frame: Any,
    inputs: dict[str, Path],
    read_vector: Callable[[Path], Any],
) -> tuple[bool, dict[str, Any]]:
    key = spec.get("key")
    if not key:
        raise ValueError("count_per_input check requires key")
    input_key = spec.get("input_key", key)
    expected = int(spec.get("count", 1))
    _require_field(frame, key, "count_per_input")
    input_frame = _load_input(spec, inputs, read_vector, frame.crs)
    if input_key not in input_frame.columns:
        raise ValueError(
            f"count_per_input check needs field {input_key!r} in input {spec['input_role']!r}"
        )
    counts = Counter(frame[key].astype(str))
    expected_keys = set(input_frame[input_key].astype(str))
    wrong = [
        {"key": value, "count": counts.get(value, 0), "expected": expected}
        for value in sorted(expected_keys)
        if counts.get(value, 0) != expected
    ]
    extra = sorted(set(counts) - expected_keys)
    details = {
        "input_role": spec["input_role"],
        "expected_count": expected,
        "input_keys": len(expected_keys),
        "wrong_count": wrong[:DETAIL_LIMIT],
        "wrong_count_total": len(wrong),
        "extra_keys": extra[:DETAIL_LIMIT],
        "extra_keys_total": len(extra),
    }
    return not wrong and not extra, details


def _check_field_equals_geometry(spec: dict[str, Any], frame: Any) -> tuple[bool, dict[str, Any]]:
    x_field = spec.get("x")
    y_field = spec.get("y")
    if not x_field or not y_field:
        raise ValueError("field_equals_geometry check requires x and y field names")
    _require_field(frame, x_field, "field_equals_geometry")
    _require_field(frame, y_field, "field_equals_geometry")
    tolerance = float(spec.get("tolerance", 0.0))
    crs = spec.get("crs")
    projected = frame.to_crs(crs) if crs and frame.crs is not None and frame.crs != crs else frame
    failures: list[dict[str, Any]] = []
    for index, (geometry, x_value, y_value) in enumerate(
        zip(projected.geometry, projected[x_field], projected[y_field], strict=True)
    ):
        if geometry is None or geometry.geom_type != "Point":
            failures.append({"row": index, "reason": "geometry is not a single point"})
            continue
        if _is_null(x_value) or _is_null(y_value):
            failures.append({"row": index, "reason": "coordinate field is null"})
            continue
        dx = abs(float(x_value) - geometry.x)
        dy = abs(float(y_value) - geometry.y)
        if dx > tolerance or dy > tolerance:
            failures.append({"row": index, "dx": dx, "dy": dy})
    details = {
        "x": x_field,
        "y": y_field,
        "crs": str(projected.crs),
        "tolerance": tolerance,
        "checked": len(frame),
        "failed": len(failures),
        "failures": failures[:DETAIL_LIMIT],
    }
    return not failures, details


def _check_field_equals_measure(spec: dict[str, Any], frame: Any) -> tuple[bool, dict[str, Any]]:
    field_name = spec.get("field")
    measure = spec.get("measure")
    if not field_name or measure not in {"area", "length"}:
        raise ValueError("field_equals_measure check requires field and measure (area or length)")
    _require_field(frame, field_name, "field_equals_measure")
    absolute = float(spec.get("absolute", 0.0))
    relative = float(spec.get("relative", 0.0))
    crs = spec.get("crs")
    projected = frame.to_crs(crs) if crs and frame.crs is not None and frame.crs != crs else frame
    measured = projected.geometry.area if measure == "area" else projected.geometry.length
    failures: list[dict[str, Any]] = []
    for index, (value, actual) in enumerate(zip(projected[field_name], measured, strict=True)):
        if _is_null(value):
            failures.append({"row": index, "reason": "field is null"})
            continue
        if not math.isclose(float(value), float(actual), abs_tol=absolute, rel_tol=relative):
            failures.append({"row": index, "field": float(value), "measured": float(actual)})
    details = {
        "field": field_name,
        "measure": measure,
        "crs": str(projected.crs),
        "absolute": absolute,
        "relative": relative,
        "checked": len(frame),
        "failed": len(failures),
        "failures": failures[:DETAIL_LIMIT],
    }
    return not failures, details


def _check_field_range(spec: dict[str, Any], frame: Any) -> tuple[bool, dict[str, Any]]:
    field_name = spec.get("field")
    if not field_name:
        raise ValueError("field_range check requires field")
    _require_field(frame, field_name, "field_range")
    minimum = spec.get("min")
    maximum = spec.get("max")
    allow_null = bool(spec.get("allow_null", False))
    failures: list[dict[str, Any]] = []
    for index, value in enumerate(frame[field_name]):
        if _is_null(value):
            if not allow_null:
                failures.append({"row": index, "reason": "null"})
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            failures.append({"row": index, "reason": "not numeric", "value": str(value)})
            continue
        if (minimum is not None and number < float(minimum)) or (
            maximum is not None and number > float(maximum)
        ):
            failures.append({"row": index, "value": number})
    details = {
        "field": field_name,
        "min": minimum,
        "max": maximum,
        "allow_null": allow_null,
        "checked": len(frame),
        "failed": len(failures),
        "failures": failures[:DETAIL_LIMIT],
    }
    return not failures, details


def _check_unique(spec: dict[str, Any], frame: Any) -> tuple[bool, dict[str, Any]]:
    field_name = spec.get("field")
    if not field_name:
        raise ValueError("unique check requires field")
    _require_field(frame, field_name, "unique")
    counts = Counter(frame[field_name].astype(str))
    duplicates = sorted(value for value, count in counts.items() if count > 1)
    details = {
        "field": field_name,
        "duplicates": duplicates[:DETAIL_LIMIT],
        "duplicate_total": len(duplicates),
    }
    return not duplicates, details


def run_vector_checks(
    frame: Any,
    specs: list[dict[str, Any]],
    inputs: dict[str, Path],
    read_vector: Callable[[Path], Any],
) -> list[tuple[str, bool, dict[str, Any]]]:
    """Evaluate every configured check; all are strict and each yields one result triple."""
    results: list[tuple[str, bool, dict[str, Any]]] = []
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict) or not spec.get("type"):
            raise ValueError(f"vector_checks[{index}] must be a mapping with a type")
        check_type = str(spec["type"])
        if check_type == "relation" or check_type in RELATION_PREDICATES:
            passed, details = _check_relation(spec, frame, inputs, read_vector)
        elif check_type == "count_per_input":
            passed, details = _check_count_per_input(spec, frame, inputs, read_vector)
        elif check_type == "field_equals_geometry":
            passed, details = _check_field_equals_geometry(spec, frame)
        elif check_type == "field_equals_measure":
            passed, details = _check_field_equals_measure(spec, frame)
        elif check_type == "field_range":
            passed, details = _check_field_range(spec, frame)
        elif check_type == "unique":
            passed, details = _check_unique(spec, frame)
        else:
            raise ValueError(f"unsupported vector check type: {check_type}")
        check_id = str(spec.get("id") or f"vector_checks.{index}.{check_type}")
        results.append((check_id, passed, details))
    return results
