from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import RunManifest, RunStatus


def is_contaminated(manifest: RunManifest) -> bool:
    """Whether the run touched material that was withheld from it."""
    return bool(manifest.integrity and manifest.integrity.contaminated)


def _group_statistics(items: list[RunManifest]) -> dict[str, Any]:
    # A run that read the reference or its solver proves nothing about capability, so it is
    # reported separately and left out of both sides of the rate, the way needs_review is.
    contaminated = [item for item in items if is_contaminated(item)]
    admissible = [item for item in items if not is_contaminated(item)]
    passed = sum(item.status == RunStatus.PASSED for item in admissible)
    needs_review = sum(item.status == RunStatus.NEEDS_REVIEW for item in admissible)
    strictly_scored = len(admissible) - needs_review
    return {
        "attempted": len(items),
        "strictly_scored": strictly_scored,
        "strict_successes": passed,
        "strict_success_rate": passed / strictly_scored if strictly_scored else None,
        "needs_manual_review": needs_review,
        "contaminated": len(contaminated),
    }


def _breakdown(manifests: list[RunManifest], field: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[RunManifest]] = defaultdict(list)
    for manifest in manifests:
        value = getattr(manifest, field)
        key = value.value if hasattr(value, "value") else str(value)
        grouped[key].append(manifest)
    return {key: _group_statistics(items) for key, items in sorted(grouped.items())}


def _task_tags(manifest: RunManifest, field: str) -> list[str]:
    value = manifest.task_metadata.get(field)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _tag_breakdown(manifests: list[RunManifest], field: str) -> dict[str, dict[str, Any]]:
    """Group runs by each task tag; a run tagged twice contributes to both groups."""
    grouped: dict[str, list[RunManifest]] = defaultdict(list)
    for manifest in manifests:
        tags = _task_tags(manifest, field)
        if not tags:
            grouped["untagged"].append(manifest)
        for tag in tags:
            grouped[tag].append(manifest)
    return {key: _group_statistics(items) for key, items in sorted(grouped.items())}


def _token_statistics(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"minimum": None, "average": None, "maximum": None}
    return {
        "minimum": min(values),
        "average": round(sum(values) / len(values), 3),
        "maximum": max(values),
    }


def _cost_summary(manifests: list[RunManifest]) -> dict[str, Any]:
    priced = [manifest.cost_estimate for manifest in manifests if manifest.cost_estimate]
    exact = [cost for cost in priced if cost.estimated_cost_usd is not None]
    range_only = [cost for cost in priced if cost.basis == "total_tokens_range"]
    all_exact = bool(manifests) and len(exact) == len(manifests)
    return {
        "currency": "USD",
        "description": "API-equivalent list-price estimate; not actual ChatGPT billing",
        "priced_runs": len(priced),
        "unpriced_runs": len(manifests) - len(priced),
        "exact_cost_runs": len(exact),
        "range_only_cost_runs": len(range_only),
        "estimated_cost_usd": round(sum(cost.estimated_cost_usd or 0 for cost in exact), 9)
        if all_exact
        else None,
        "exact_components_cost_usd": round(
            sum(cost.estimated_cost_usd or 0 for cost in exact), 9
        ),
        "minimum_cost_usd": round(sum(cost.minimum_cost_usd for cost in priced), 9)
        if priced
        else None,
        "maximum_cost_usd": round(sum(cost.maximum_cost_usd for cost in priced), 9)
        if priced
        else None,
        "pricing_as_of": sorted({cost.pricing_as_of for cost in priced}),
        "pricing_sources": sorted({cost.pricing_source for cost in priced}),
    }


def _usage_summary(manifests: list[RunManifest]) -> dict[str, Any]:
    with_usage = [manifest for manifest in manifests if manifest.token_usage]
    grouped: dict[str, list[RunManifest]] = defaultdict(list)
    for manifest in with_usage:
        assert manifest.token_usage is not None
        model = manifest.token_usage.model or str(manifest.agent.get("model") or "unknown")
        grouped[model].append(manifest)

    by_model: dict[str, Any] = {}
    for model, items in sorted(grouped.items()):
        totals = [item.token_usage.total_tokens for item in items if item.token_usage]
        by_model[model] = {
            "runs": len(items),
            "total_tokens": sum(totals),
            "tokens_per_task": _token_statistics(totals),
            "cost": _cost_summary(items),
        }

    total_values = [manifest.token_usage.total_tokens for manifest in with_usage if manifest.token_usage]
    detailed = [
        manifest.token_usage
        for manifest in with_usage
        if manifest.token_usage
        and manifest.token_usage.input_tokens is not None
        and manifest.token_usage.output_tokens is not None
    ]
    return {
        "runs_with_usage": len(with_usage),
        "runs_without_usage": len(manifests) - len(with_usage),
        "runs_with_token_breakdown": len(detailed),
        "total_tokens": sum(total_values),
        "tokens_per_task": _token_statistics(total_values),
        "known_token_categories": {
            "input_tokens": sum(item.input_tokens or 0 for item in detailed),
            "cached_input_tokens": sum(item.cached_input_tokens or 0 for item in detailed),
            "cache_write_input_tokens": sum(
                item.cache_write_input_tokens or 0 for item in detailed
            ),
            "output_tokens": sum(item.output_tokens or 0 for item in detailed),
            "reasoning_output_tokens": sum(
                item.reasoning_output_tokens or 0 for item in detailed
            ),
        },
        "cost": _cost_summary(with_usage),
        "by_model": by_model,
    }


def load_manifests(run_root: Path) -> tuple[list[tuple[Path, RunManifest]], list[dict[str, str]]]:
    """Load every run manifest under a run root, reporting unreadable ones instead of raising."""
    loaded: list[tuple[Path, RunManifest]] = []
    invalid: list[dict[str, str]] = []
    for path in sorted(run_root.rglob("manifest.json")):
        try:
            loaded.append(
                (path, RunManifest.model_validate_json(path.read_text(encoding="utf-8")))
            )
        except (OSError, ValidationError) as exc:
            invalid.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    return loaded, invalid


def _task_reliability(manifests: list[RunManifest]) -> dict[str, dict[str, Any]]:
    """Pass rate per task across repeats: a task passed once is not a task solved."""
    grouped: dict[str, list[RunManifest]] = defaultdict(list)
    for manifest in manifests:
        grouped[manifest.task_id].append(manifest)
    reliability: dict[str, dict[str, Any]] = {}
    for task_id, items in sorted(grouped.items()):
        admissible = [item for item in items if not is_contaminated(item)]
        passes = sum(item.status == RunStatus.PASSED for item in admissible)
        reliability[task_id] = {
            "runs": len(items),
            "admissible_runs": len(admissible),
            "passes": passes,
            "pass_rate": passes / len(admissible) if admissible else None,
            "contaminated": len(items) - len(admissible),
            "statuses": dict(sorted(Counter(item.status.value for item in items).items())),
        }
    return reliability


def aggregate_manifests(run_root: Path) -> dict[str, Any]:
    loaded, invalid = load_manifests(run_root)
    manifests = [manifest for _, manifest in loaded]
    contaminated = [manifest for manifest in manifests if is_contaminated(manifest)]
    admissible = [manifest for manifest in manifests if not is_contaminated(manifest)]
    passed = sum(manifest.status == RunStatus.PASSED for manifest in admissible)
    attempted = len(manifests)
    needs_review = sum(manifest.status == RunStatus.NEEDS_REVIEW for manifest in admissible)
    strictly_scored = len(admissible) - needs_review
    by_task = _task_reliability(manifests)
    unstable = sorted(
        task_id
        for task_id, stats in by_task.items()
        if stats["pass_rate"] is not None and 0 < stats["pass_rate"] < 1
    )
    return {
        "schema_version": "0.5",
        "attempted_tasks": attempted,
        "distinct_tasks": len(by_task),
        "runs_per_task": _token_statistics([stats["runs"] for stats in by_task.values()]),
        "strictly_scored_tasks": strictly_scored,
        "strict_successes": passed,
        "strict_success_rate": passed / strictly_scored if strictly_scored else None,
        "needs_manual_review": needs_review,
        "contaminated_runs": len(contaminated),
        "contaminated_task_ids": sorted({manifest.task_id for manifest in contaminated}),
        "status_counts": dict(
            sorted(Counter(manifest.status.value for manifest in manifests).items())
        ),
        "by_task": by_task,
        "unstable_tasks": unstable,
        "by_category": _breakdown(manifests, "category"),
        "by_output_kind": _breakdown(manifests, "output_kind"),
        "by_failure_mode": _tag_breakdown(manifests, "failure_modes"),
        "usage": _usage_summary(manifests),
        "runs": [
            {
                "run_id": manifest.run_id,
                "task_id": manifest.task_id,
                "status": manifest.status.value,
                "contaminated": is_contaminated(manifest),
                "strictly_scored": (
                    manifest.status != RunStatus.NEEDS_REVIEW and not is_contaminated(manifest)
                ),
                "strict_success": (
                    manifest.status == RunStatus.PASSED and not is_contaminated(manifest)
                ),
                "duration_seconds": manifest.duration_seconds,
                "model": (
                    manifest.token_usage.model
                    if manifest.token_usage and manifest.token_usage.model
                    else manifest.agent.get("model")
                ),
                "total_tokens": manifest.token_usage.total_tokens
                if manifest.token_usage
                else None,
                "estimated_cost_usd": manifest.cost_estimate.estimated_cost_usd
                if manifest.cost_estimate
                else None,
                "minimum_cost_usd": manifest.cost_estimate.minimum_cost_usd
                if manifest.cost_estimate
                else None,
                "maximum_cost_usd": manifest.cost_estimate.maximum_cost_usd
                if manifest.cost_estimate
                else None,
            }
            for manifest in manifests
        ],
        "invalid_manifests": invalid,
    }


def _cost_text(cost: dict[str, Any]) -> str:
    if cost["estimated_cost_usd"] is not None:
        return f"${cost['estimated_cost_usd']:.6f}"
    if cost["minimum_cost_usd"] is not None:
        return f"${cost['minimum_cost_usd']:.6f}–${cost['maximum_cost_usd']:.6f}"
    return "not available"


def _run_cost_text(run: dict[str, Any]) -> str:
    if run["estimated_cost_usd"] is not None:
        return f"${run['estimated_cost_usd']:.6f}"
    if run["minimum_cost_usd"] is not None:
        return f"${run['minimum_cost_usd']:.6f}–${run['maximum_cost_usd']:.6f}"
    return "—"


def report_markdown(report: dict[str, Any]) -> str:
    rate = report["strict_success_rate"]
    rate_text = f"{rate:.1%}" if rate is not None else "not available"
    usage = report["usage"]
    stats = usage["tokens_per_task"]
    lines = [
        "# OpenMapBench report",
        "",
        f"- Attempted tasks: {report['attempted_tasks']}",
        f"- Strictly scored tasks: {report['strictly_scored_tasks']}",
        f"- Strict successes: {report['strict_successes']}",
        f"- Strict success rate: {rate_text}",
        f"- Needs manual review: {report['needs_manual_review']}",
        f"- Contaminated runs (excluded from the rate): {report['contaminated_runs']}",
        "",
        "## Token usage and cost",
        "",
        f"- Runs with token usage: {usage['runs_with_usage']}/{report['attempted_tasks']}",
        f"- Total tokens: {usage['total_tokens']:,}",
    ]
    if stats["minimum"] is not None:
        lines.extend(
            [
                (
                    "- Tokens per task (min / average / max): "
                    f"{stats['minimum']:,} / {stats['average']:,.1f} / {stats['maximum']:,}"
                ),
                f"- Estimated cost: {_cost_text(usage['cost'])}",
                "- Cost basis: API-equivalent list prices, not actual ChatGPT billing",
            ]
        )
    if usage["by_model"]:
        lines.extend(
            [
                "",
                "| Model | Runs | Total tokens | Min | Average | Max | Estimated cost |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for model, model_usage in usage["by_model"].items():
            model_stats = model_usage["tokens_per_task"]
            lines.append(
                f"| {model} | {model_usage['runs']} | {model_usage['total_tokens']:,} | "
                f"{model_stats['minimum']:,} | {model_stats['average']:,.1f} | "
                f"{model_stats['maximum']:,} | {_cost_text(model_usage['cost'])} |"
            )
    failure_modes = {
        key: value for key, value in report.get("by_failure_mode", {}).items() if key != "untagged"
    }
    if failure_modes:
        lines.extend(
            [
                "",
                "## Strict success by tagged failure mode",
                "",
                "| Failure mode | Attempted | Strictly scored | Strict successes | Rate |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for mode, stats in failure_modes.items():
            mode_rate = stats["strict_success_rate"]
            lines.append(
                f"| {mode} | {stats['attempted']} | {stats['strictly_scored']} | "
                f"{stats['strict_successes']} | "
                f"{f'{mode_rate:.1%}' if mode_rate is not None else '—'} |"
            )
    by_task = report.get("by_task") or {}
    if any(stats["runs"] > 1 for stats in by_task.values()):
        lines.extend(
            [
                "",
                "## Reliability per task",
                "",
                "| Task | Runs | Passes | Pass rate | Statuses |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for task_id, stats in by_task.items():
            rate = stats["pass_rate"]
            statuses = ", ".join(f"{key} x{value}" for key, value in stats["statuses"].items())
            lines.append(
                f"| {task_id} | {stats['runs']} | {stats['passes']} | "
                f"{f'{rate:.0%}' if rate is not None else '—'} | {statuses} |"
            )
        if report.get("unstable_tasks"):
            lines.extend(
                [
                    "",
                    "Unstable across repeats: "
                    + ", ".join(f"`{task}`" for task in report["unstable_tasks"])
                    + ". A task that sometimes passes is not a task an agent can do.",
                ]
            )
    lines.extend(
        [
            "",
            "## Per-task results",
            "",
            "| Task | Status | Model | Tokens | Estimated cost | Strict success | Duration (s) |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for run in report["runs"]:
        token_text = f"{run['total_tokens']:,}" if run["total_tokens"] is not None else "—"
        success_text = "contaminated" if run["contaminated"] else (
            "yes" if run["strict_success"] else "no"
        )
        lines.append(
            f"| {run['task_id']} | {run['status']} | {run['model'] or '—'} | "
            f"{token_text} | {_run_cost_text(run)} | "
            f"{success_text} | {run['duration_seconds']:.3f} |"
        )
    return "\n".join(lines) + "\n"
