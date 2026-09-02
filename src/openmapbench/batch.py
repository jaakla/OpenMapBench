"""Shared plumbing for OpenMapBench batch runners.

Both batch runners — the native benchmark suite and the GABench bridge — write the same
bundle: an isolated directory holding every run, one aggregate report in three formats, and a
batch manifest describing provenance and outcomes. The pieces that must stay identical between
them live here.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .html_report import write_html_report
from .models import RunStatus
from .reporting import aggregate_manifests, report_markdown

COMPLETED_STATUSES = {RunStatus.PASSED, RunStatus.NEEDS_REVIEW}
BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def now() -> datetime:
    return datetime.now(UTC)


def new_batch_id() -> str:
    return f"{now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def resolve_batch_id(batch_id: str | None) -> str:
    """Accept a caller-supplied batch ID only when it is a safe single path segment."""
    resolved = batch_id or new_batch_id()
    if not BATCH_ID_PATTERN.fullmatch(resolved):
        raise ValueError(
            "batch ID may contain only letters, numbers, dots, underscores, and dashes"
        )
    return resolved


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(item["status"]) for item in results).items()))


def failed_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Results that neither passed nor landed in manual review."""
    return [item for item in results if RunStatus(item["status"]) not in COMPLETED_STATUSES]


def write_aggregate_reports(batch_dir: Path, task_runs_dir: Path) -> dict[str, Any]:
    """Aggregate the batch's run manifests into report.json and report.md."""
    aggregate = aggregate_manifests(task_runs_dir)
    json_path = batch_dir / "report.json"
    markdown_path = batch_dir / "report.md"
    write_json(json_path, aggregate)
    markdown_path.write_text(report_markdown(aggregate), encoding="utf-8")
    return {"aggregate": aggregate, "json": json_path, "markdown": markdown_path}


def write_batch_html(
    batch_dir: Path,
    task_runs_dir: Path,
    aggregate: dict[str, Any],
    batch: dict[str, Any],
    *,
    title: str,
    subtitle: str,
    extra_links: list[tuple[str, Path]] | None = None,
    notice: str | None = None,
) -> Path:
    """Write the detailed, self-contained HTML report for a finished batch."""
    output = batch_dir / "report.html"
    links = [
        ("report.json", batch_dir / "report.json"),
        ("report.md", batch_dir / "report.md"),
        ("batch.json", batch_dir / "batch.json"),
        *(extra_links or []),
    ]
    write_html_report(
        task_runs_dir,
        output,
        title=title,
        subtitle=subtitle,
        batch=batch,
        aggregate=aggregate,
        extra_links=links,
        **({"notice": notice} if notice else {}),
    )
    return output
