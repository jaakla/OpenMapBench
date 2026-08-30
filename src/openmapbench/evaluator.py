from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import OutputKind, TaskSpec


@dataclass
class EvaluationCheck:
    id: str
    passed: bool
    required: bool = True
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    success: bool
    score: float
    diagnostics: dict[str, Any]
    checks: list[EvaluationCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _numeric_equal(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol)


def _try_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compare_json_value(
    candidate: Any,
    reference: Any,
    *,
    abs_tol: float,
    rel_tol: float,
    path: str = "$",
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    c_number = _try_number(candidate)
    r_number = _try_number(reference)
    if c_number is not None and r_number is not None:
        if not _numeric_equal(c_number, r_number, abs_tol, rel_tol):
            mismatches.append({"path": path, "candidate": candidate, "reference": reference})
        return mismatches
    if isinstance(candidate, dict) and isinstance(reference, dict):
        if set(candidate) != set(reference):
            mismatches.append(
                {
                    "path": path,
                    "candidate_keys": sorted(candidate),
                    "reference_keys": sorted(reference),
                }
            )
        for key in sorted(set(candidate) & set(reference)):
            mismatches.extend(
                _compare_json_value(
                    candidate[key],
                    reference[key],
                    abs_tol=abs_tol,
                    rel_tol=rel_tol,
                    path=f"{path}.{key}",
                )
            )
        return mismatches
    if isinstance(candidate, list) and isinstance(reference, list):
        if len(candidate) != len(reference):
            mismatches.append(
                {"path": path, "candidate_length": len(candidate), "reference_length": len(reference)}
            )
        for index, (c_value, r_value) in enumerate(zip(candidate, reference, strict=False)):
            mismatches.extend(
                _compare_json_value(
                    c_value,
                    r_value,
                    abs_tol=abs_tol,
                    rel_tol=rel_tol,
                    path=f"{path}[{index}]",
                )
            )
        return mismatches
    if candidate != reference:
        mismatches.append({"path": path, "candidate": candidate, "reference": reference})
    return mismatches


def _json_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current


def _predicate_passed(candidate: Any, operator: str, expected: Any) -> bool:
    if operator == "==":
        return candidate == expected
    if operator == "!=":
        return candidate != expected
    operations = {
        "<": lambda: candidate < expected,
        "<=": lambda: candidate <= expected,
        ">": lambda: candidate > expected,
        ">=": lambda: candidate >= expected,
    }
    if operator not in operations:
        raise ValueError(f"unsupported JSON check operator: {operator}")
    return bool(operations[operator]())


def evaluate_scalar(candidate: Path, reference: Path, config: dict[str, Any]) -> EvaluationResult:
    c_text = candidate.read_text(encoding="utf-8").strip()
    r_text = reference.read_text(encoding="utf-8").strip()
    abs_tol = float(config.get("absolute_tolerance", 0.0))
    rel_tol = float(config.get("relative_tolerance", 0.0))
    try:
        c_value = json.loads(c_text)
        r_value = json.loads(r_text)
    except json.JSONDecodeError:
        c_value = c_text
        r_value = r_text

    json_checks = config.get("json_checks", [])
    if json_checks:
        checks: list[EvaluationCheck] = []
        outcomes: list[dict[str, Any]] = []
        for index, check in enumerate(json_checks):
            path = str(check["path"])
            operator = str(check["operator"])
            expected = check["value"]
            try:
                actual = _json_path(c_value, path)
                passed = _predicate_passed(actual, operator, expected)
                details = {
                    "path": path,
                    "operator": operator,
                    "expected": expected,
                    "actual": actual,
                }
            except (KeyError, TypeError) as exc:
                passed = False
                details = {
                    "path": path,
                    "operator": operator,
                    "expected": expected,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            checks.append(EvaluationCheck(id=f"json_check_{index + 1}", passed=passed, details=details))
            outcomes.append(details | {"passed": passed})
        success = all(check.passed for check in checks)
        return EvaluationResult(
            success=success,
            score=1.0 if success else 0.0,
            checks=checks,
            diagnostics={"candidate": c_value, "json_checks": outcomes},
        )

    mismatches = _compare_json_value(
        c_value,
        r_value,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
    )
    success = not mismatches
    return EvaluationResult(
        success=success,
        score=1.0 if success else 0.0,
        checks=[EvaluationCheck(id="value", passed=success)],
        diagnostics={
            "candidate": c_value,
            "reference": r_value,
            "absolute_tolerance": abs_tol,
            "relative_tolerance": rel_tol,
            "mismatches": mismatches[:50],
            "mismatch_count": len(mismatches),
        },
    )


def _read_table(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("rows", payload)
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ValueError("JSON table must be a list of objects or an object with a 'rows' list")
        return payload
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _column_tolerance(config: dict[str, Any], column: str) -> tuple[float, float]:
    tolerances = config.get("numeric_tolerance", {})
    selected = tolerances.get(column, tolerances.get("default", {}))
    if isinstance(selected, (int, float)):
        return float(selected), 0.0
    return float(selected.get("absolute", 0.0)), float(selected.get("relative", 0.0))


def _canonical_row(row: dict[str, Any], ignore_columns: set[str]) -> str:
    return json.dumps(
        {key: value for key, value in row.items() if key not in ignore_columns},
        sort_keys=True,
        ensure_ascii=False,
    )


def _compare_rows(
    candidate_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[EvaluationCheck], dict[str, Any]]:
    key = config.get("key")
    ignore_order = bool(config.get("ignore_order", True))
    ignore_columns = set(config.get("ignore_columns", []))
    required_columns = set(config.get("required_columns", []))
    candidate_columns = set().union(*(row.keys() for row in candidate_rows)) if candidate_rows else set()
    reference_columns = set().union(*(row.keys() for row in reference_rows)) if reference_rows else set()
    expected_columns = (reference_columns | required_columns) - ignore_columns
    columns_ok = expected_columns <= candidate_columns

    duplicate_keys: list[str] = []
    if key:
        values = [str(row.get(key, "")) for row in candidate_rows]
        duplicate_keys = sorted({value for value in values if values.count(value) > 1})
        candidate_rows = sorted(candidate_rows, key=lambda row: str(row.get(key, "")))
        reference_rows = sorted(reference_rows, key=lambda row: str(row.get(key, "")))
    elif ignore_order:
        candidate_rows = sorted(candidate_rows, key=lambda row: _canonical_row(row, ignore_columns))
        reference_rows = sorted(reference_rows, key=lambda row: _canonical_row(row, ignore_columns))

    mismatches: list[dict[str, Any]] = []
    for row_index, (candidate_row, reference_row) in enumerate(
        zip(candidate_rows, reference_rows, strict=False)
    ):
        for column in sorted(expected_columns):
            c_value = candidate_row.get(column)
            r_value = reference_row.get(column)
            c_number = _try_number(c_value)
            r_number = _try_number(r_value)
            if c_number is not None and r_number is not None:
                abs_tol, rel_tol = _column_tolerance(config, column)
                equal = _numeric_equal(c_number, r_number, abs_tol, rel_tol)
            else:
                equal = c_value == r_value
            if not equal:
                mismatches.append(
                    {
                        "row": row_index,
                        "key": candidate_row.get(key) if key else None,
                        "column": column,
                        "candidate": c_value,
                        "reference": r_value,
                    }
                )

    row_count_ok = len(candidate_rows) == len(reference_rows)
    values_ok = row_count_ok and not mismatches
    checks = [
        EvaluationCheck(
            id="columns",
            passed=columns_ok,
            details={"missing": sorted(expected_columns - candidate_columns)},
        ),
        EvaluationCheck(
            id="row_count",
            passed=row_count_ok,
            details={"candidate": len(candidate_rows), "reference": len(reference_rows)},
        ),
        EvaluationCheck(
            id="unique_key",
            passed=not duplicate_keys,
            required=bool(key),
            details={"duplicate_keys": duplicate_keys[:50]},
        ),
        EvaluationCheck(
            id="values",
            passed=values_ok,
            details={"mismatch_count": len(mismatches)},
        ),
    ]
    diagnostics = {
        "candidate_rows": len(candidate_rows),
        "reference_rows": len(reference_rows),
        "key": key,
        "missing_columns": sorted(expected_columns - candidate_columns),
        "duplicate_keys": duplicate_keys[:50],
        "mismatches": mismatches[:50],
        "mismatch_count": len(mismatches),
    }
    return checks, diagnostics


def evaluate_table(candidate: Path, reference: Path, config: dict[str, Any]) -> EvaluationResult:
    checks, diagnostics = _compare_rows(_read_table(candidate), _read_table(reference), config)
    success = all(check.passed for check in checks if check.required)
    return EvaluationResult(
        success=success,
        score=1.0 if success else 0.0,
        checks=checks,
        diagnostics=diagnostics,
    )


def _geometry_family(geometry_types: Iterable[str]) -> set[str]:
    return {geometry_type.lower().removeprefix("multi") for geometry_type in geometry_types}


def _crs_equal(candidate_crs: Any, expected_crs: Any) -> bool:
    if candidate_crs is None or expected_crs is None:
        return candidate_crs is expected_crs
    from pyproj import CRS

    return CRS.from_user_input(candidate_crs) == CRS.from_user_input(expected_crs)


def _comparison_crs(candidate: Any, reference: Any, configured: str | None) -> str | Any:
    from pyproj import CRS

    if configured:
        return configured
    if reference.crs and CRS.from_user_input(reference.crs).is_projected:
        return reference.crs
    if reference.crs:
        bounds = reference.total_bounds
        longitude_span = abs(float(bounds[2] - bounds[0]))
        latitude_span = abs(float(bounds[3] - bounds[1]))
        if longitude_span <= 12 and latitude_span <= 12:
            estimated = reference.estimate_utm_crs()
            if estimated:
                return estimated
        return "EPSG:6933"
    if candidate.crs and CRS.from_user_input(candidate.crs).is_projected:
        return candidate.crs
    raise ValueError("geometry comparison requires a known CRS")


def _read_vector(gpd: Any, path: Path, layer: str | None) -> Any:
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return gpd.read_parquet(path)
    return gpd.read_file(path, layer=layer)


def evaluate_vector(
    candidate: Path,
    reference: Path,
    config: dict[str, Any],
    output: Any | None = None,
) -> EvaluationResult:
    """Compare vector artifacts semantically, independent of order and partitioning."""
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise RuntimeError(
            'Vector evaluation requires the "geo" extra: pip install -e ".[geo]"'
        ) from exc

    layer = getattr(output, "layer", None)
    candidate_frame = _read_vector(gpd, candidate, layer)
    reference_frame = _read_vector(gpd, reference, layer)
    checks: list[EvaluationCheck] = []

    crs_mode = config.get("crs", "exact")
    expected_crs = getattr(output, "crs", None) or reference_frame.crs
    crs_ok = True if crs_mode == "ignore" else _crs_equal(candidate_frame.crs, expected_crs)
    checks.append(
        EvaluationCheck(
            id="crs",
            passed=crs_ok,
            details={"candidate": str(candidate_frame.crs), "expected": str(expected_crs)},
        )
    )

    required_fields = set(getattr(output, "required_fields", []))
    fields_ok = required_fields <= set(candidate_frame.columns)
    checks.append(
        EvaluationCheck(
            id="required_fields",
            passed=fields_ok,
            required=bool(required_fields),
            details={"missing": sorted(required_fields - set(candidate_frame.columns))},
        )
    )

    expected_geometry_type = getattr(output, "geometry_type", None)
    candidate_families = _geometry_family(candidate_frame.geom_type.dropna().unique())
    expected_family = _geometry_family([expected_geometry_type]) if expected_geometry_type else set()
    geometry_type_ok = not expected_family or candidate_families <= expected_family
    checks.append(
        EvaluationCheck(
            id="geometry_type",
            passed=geometry_type_ok,
            required=bool(expected_family),
            details={"candidate": sorted(candidate_families), "expected": sorted(expected_family)},
        )
    )

    geometry_config = config.get("geometry", {})
    require_valid = bool(geometry_config.get("require_valid", True))
    candidate_invalid = int((~candidate_frame.geometry.is_valid).sum()) if not candidate_frame.empty else 0
    reference_invalid = int((~reference_frame.geometry.is_valid).sum()) if not reference_frame.empty else 0
    valid_ok = candidate_invalid == 0 and reference_invalid == 0
    checks.append(
        EvaluationCheck(
            id="geometry_validity",
            passed=valid_ok,
            required=require_valid,
            details={"candidate_invalid": candidate_invalid, "reference_invalid": reference_invalid},
        )
    )

    count_mode = config.get("feature_count", "ignore")
    count_ok = count_mode == "ignore" or len(candidate_frame) == len(reference_frame)
    checks.append(
        EvaluationCheck(
            id="feature_count",
            passed=count_ok,
            required=count_mode != "ignore",
            details={
                "mode": count_mode,
                "candidate": len(candidate_frame),
                "reference": len(reference_frame),
            },
        )
    )

    if candidate_frame.empty and reference_frame.empty:
        metric_name, metric_value = "empty_equivalence", 0.0
    elif candidate_frame.empty != reference_frame.empty:
        metric_name, metric_value = "empty_equivalence", 1.0
    elif require_valid and not valid_ok:
        metric_name, metric_value = "not_comparable_invalid_geometry", None
    elif candidate_frame.crs is None or reference_frame.crs is None:
        metric_name, metric_value = "not_comparable_missing_crs", None
    else:
        comparison_crs = _comparison_crs(
            candidate_frame,
            reference_frame,
            geometry_config.get("comparison_crs"),
        )
        candidate_projected = candidate_frame.to_crs(comparison_crs)
        reference_projected = reference_frame.to_crs(comparison_crs)
        candidate_union = candidate_projected.geometry.union_all()
        reference_union = reference_projected.geometry.union_all()
        metric_name = geometry_config.get("metric", "auto")
        if metric_name == "auto":
            families = _geometry_family(reference_projected.geom_type.dropna().unique())
            metric_name = (
                "symmetric_difference_ratio" if families <= {"polygon"} else "hausdorff_distance"
            )
        if metric_name == "symmetric_difference_ratio":
            denominator = max(float(reference_union.area), 1e-15)
            metric_value = float(
                candidate_union.symmetric_difference(reference_union).area / denominator
            )
        elif metric_name == "iou":
            union_area = float(candidate_union.union(reference_union).area)
            intersection_area = float(candidate_union.intersection(reference_union).area)
            metric_value = 1.0 if union_area == 0 else intersection_area / union_area
        elif metric_name == "hausdorff_distance":
            metric_value = float(candidate_union.hausdorff_distance(reference_union))
        else:
            raise ValueError(f"unsupported vector geometry metric: {metric_name}")

    tolerance = float(geometry_config.get("tolerance", 0.0))
    geometry_ok = (
        False
        if metric_value is None
        else metric_value >= tolerance
        if metric_name == "iou"
        else metric_value <= tolerance
    )
    checks.append(
        EvaluationCheck(
            id="geometry",
            passed=geometry_ok,
            details={"metric": metric_name, "value": metric_value, "tolerance": tolerance},
        )
    )

    attributes_config = config.get("attributes")
    attribute_diagnostics: dict[str, Any] | None = None
    if attributes_config:
        columns = attributes_config.get("columns") or sorted(required_fields)
        candidate_rows = candidate_frame.reindex(columns=columns).to_dict("records")
        reference_rows = reference_frame.reindex(columns=columns).to_dict("records")
        attribute_checks, attribute_diagnostics = _compare_rows(
            candidate_rows,
            reference_rows,
            attributes_config,
        )
        for check in attribute_checks:
            check.id = f"attributes.{check.id}"
        checks.extend(attribute_checks)

    success = all(check.passed for check in checks if check.required)
    closeness = (
        0.0
        if metric_value is None
        else metric_value
        if metric_name == "iou"
        else max(0.0, 1.0 - metric_value)
    )
    return EvaluationResult(
        success=success,
        score=1.0 if success else min(1.0, closeness),
        checks=checks,
        diagnostics={
            "candidate_features": len(candidate_frame),
            "reference_features": len(reference_frame),
            "geometry_metric": metric_name,
            "geometry_value": metric_value,
            "geometry_tolerance": tolerance,
            "attributes": attribute_diagnostics,
        },
    )


def evaluate(task: TaskSpec, candidate: Path, reference: Path) -> EvaluationResult:
    config = task.evaluation.strict
    if task.output.kind == OutputKind.SCALAR:
        return evaluate_scalar(candidate, reference, config)
    if task.output.kind == OutputKind.TABLE:
        return evaluate_table(candidate, reference, config)
    if task.output.kind == OutputKind.VECTOR:
        return evaluate_vector(candidate, reference, config, task.output)
    raise NotImplementedError(f"No evaluator implemented for {task.output.kind.value!r}")
