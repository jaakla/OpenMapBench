from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .models import OutputKind, TaskSpec


@dataclass
class EvaluationResult:
    success: bool
    score: float
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _numeric_equal(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol)


def evaluate_scalar(candidate: Path, reference: Path, config: dict[str, Any]) -> EvaluationResult:
    c_text = candidate.read_text(encoding="utf-8").strip()
    r_text = reference.read_text(encoding="utf-8").strip()

    try:
        c = float(c_text)
        r = float(r_text)
        abs_tol = float(config.get("absolute_tolerance", 0.0))
        rel_tol = float(config.get("relative_tolerance", 0.0))
        success = _numeric_equal(c, r, abs_tol=abs_tol, rel_tol=rel_tol)
        return EvaluationResult(
            success=success,
            score=1.0 if success else 0.0,
            diagnostics={
                "candidate": c,
                "reference": r,
                "absolute_error": abs(c - r),
                "absolute_tolerance": abs_tol,
                "relative_tolerance": rel_tol,
            },
        )
    except ValueError:
        success = c_text == r_text
        return EvaluationResult(
            success=success,
            score=1.0 if success else 0.0,
            diagnostics={"candidate": c_text, "reference": r_text},
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def evaluate_table(candidate: Path, reference: Path, config: dict[str, Any]) -> EvaluationResult:
    candidate_rows = _read_csv(candidate)
    reference_rows = _read_csv(reference)

    key = config.get("key")
    ignore_order = bool(config.get("ignore_order", True))

    if key:
        candidate_rows = sorted(candidate_rows, key=lambda r: r.get(key, ""))
        reference_rows = sorted(reference_rows, key=lambda r: r.get(key, ""))
    elif ignore_order:
        canonical = lambda row: json.dumps(row, sort_keys=True)
        candidate_rows = sorted(candidate_rows, key=canonical)
        reference_rows = sorted(reference_rows, key=canonical)

    success = candidate_rows == reference_rows
    return EvaluationResult(
        success=success,
        score=1.0 if success else 0.0,
        diagnostics={
            "candidate_rows": len(candidate_rows),
            "reference_rows": len(reference_rows),
            "key": key,
        },
    )


def evaluate_vector(candidate: Path, reference: Path, config: dict[str, Any]) -> EvaluationResult:
    """
    First deterministic vector evaluator.

    v0 compares:
      - feature count
      - CRS
      - unioned geometry via symmetric-difference ratio

    This is intentionally independent of vertex ordering and feature ordering.
    Attribute-level matching belongs in the next iteration.
    """
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise RuntimeError(
            'Vector evaluation requires the "geo" extra: pip install -e ".[geo]"'
        ) from exc

    c = gpd.read_file(candidate)
    r = gpd.read_file(reference)

    crs_mode = config.get("crs", "exact")
    crs_ok = True if crs_mode == "ignore" else str(c.crs) == str(r.crs)

    count_ok = len(c) == len(r)

    if c.empty and r.empty:
        ratio = 0.0
    elif c.empty != r.empty:
        ratio = 1.0
    else:
        if c.crs != r.crs and c.crs and r.crs:
            c = c.to_crs(r.crs)
        cg = c.geometry.union_all()
        rg = r.geometry.union_all()
        denom = max(rg.area, 1e-15)
        ratio = cg.symmetric_difference(rg).area / denom

    tolerance = float(
        config.get("geometry", {}).get("tolerance", 0.0)
        if isinstance(config.get("geometry"), dict)
        else 0.0
    )
    geom_ok = ratio <= tolerance

    success = crs_ok and count_ok and geom_ok
    closeness = max(0.0, 1.0 - ratio)

    return EvaluationResult(
        success=success,
        score=1.0 if success else closeness,
        diagnostics={
            "candidate_features": len(c),
            "reference_features": len(r),
            "crs_ok": crs_ok,
            "feature_count_ok": count_ok,
            "symmetric_difference_ratio": ratio,
            "geometry_tolerance": tolerance,
        },
    )


def evaluate(task: TaskSpec, candidate: Path, reference: Path) -> EvaluationResult:
    config = task.evaluation.strict

    if task.output.kind == OutputKind.SCALAR:
        return evaluate_scalar(candidate, reference, config)
    if task.output.kind == OutputKind.TABLE:
        return evaluate_table(candidate, reference, config)
    if task.output.kind == OutputKind.VECTOR:
        return evaluate_vector(candidate, reference, config)

    raise NotImplementedError(f"No evaluator implemented yet for {task.output.kind.value!r}")
