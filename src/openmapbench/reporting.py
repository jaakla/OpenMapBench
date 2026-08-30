from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import RunManifest, RunStatus


def _breakdown(manifests: list[RunManifest], field: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[RunManifest]] = defaultdict(list)
    for manifest in manifests:
        value = getattr(manifest, field)
        key = value.value if hasattr(value, "value") else str(value)
        grouped[key].append(manifest)
    result: dict[str, dict[str, Any]] = {}
    for key, items in sorted(grouped.items()):
        passed = sum(item.status == RunStatus.PASSED for item in items)
        needs_review = sum(item.status == RunStatus.NEEDS_REVIEW for item in items)
        strictly_scored = len(items) - needs_review
        result[key] = {
            "attempted": len(items),
            "strictly_scored": strictly_scored,
            "strict_successes": passed,
            "strict_success_rate": passed / strictly_scored if strictly_scored else None,
            "needs_manual_review": needs_review,
        }
    return result


def aggregate_manifests(run_root: Path) -> dict[str, Any]:
    manifests: list[RunManifest] = []
    invalid: list[dict[str, str]] = []
    for path in sorted(run_root.rglob("manifest.json")):
        try:
            manifests.append(RunManifest.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValidationError) as exc:
            invalid.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    passed = sum(manifest.status == RunStatus.PASSED for manifest in manifests)
    attempted = len(manifests)
    needs_review = sum(manifest.status == RunStatus.NEEDS_REVIEW for manifest in manifests)
    strictly_scored = attempted - needs_review
    return {
        "schema_version": "0.1",
        "attempted_tasks": attempted,
        "strictly_scored_tasks": strictly_scored,
        "strict_successes": passed,
        "strict_success_rate": passed / strictly_scored if strictly_scored else None,
        "needs_manual_review": needs_review,
        "status_counts": dict(
            sorted(Counter(manifest.status.value for manifest in manifests).items())
        ),
        "by_category": _breakdown(manifests, "category"),
        "by_output_kind": _breakdown(manifests, "output_kind"),
        "runs": [
            {
                "run_id": manifest.run_id,
                "task_id": manifest.task_id,
                "status": manifest.status.value,
                "strictly_scored": manifest.status != RunStatus.NEEDS_REVIEW,
                "strict_success": manifest.status == RunStatus.PASSED,
                "duration_seconds": manifest.duration_seconds,
            }
            for manifest in manifests
        ],
        "invalid_manifests": invalid,
    }


def report_markdown(report: dict[str, Any]) -> str:
    rate = report["strict_success_rate"]
    rate_text = f"{rate:.1%}" if rate is not None else "not available"
    lines = [
        "# OpenMapBench report",
        "",
        f"- Attempted tasks: {report['attempted_tasks']}",
        f"- Strictly scored tasks: {report['strictly_scored_tasks']}",
        f"- Strict successes: {report['strict_successes']}",
        f"- Strict success rate: {rate_text}",
        f"- Needs manual review: {report['needs_manual_review']}",
        "",
        "| Task | Status | Strict success | Duration (s) |",
        "| --- | --- | ---: | ---: |",
    ]
    lines.extend(
        f"| {run['task_id']} | {run['status']} | {'yes' if run['strict_success'] else 'no'} | "
        f"{run['duration_seconds']:.3f} |"
        for run in report["runs"]
    )
    return "\n".join(lines) + "\n"
