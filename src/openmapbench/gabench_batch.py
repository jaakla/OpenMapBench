from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import RunStatus
from .reporting import aggregate_manifests, report_markdown
from .runner import run_task
from .taskio import load_task, sha256_file
from .visual import visual_report_from_runs

COMPLETED_STATUSES = {RunStatus.PASSED, RunStatus.NEEDS_REVIEW}
BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_entry_path(value: Any, manifest_dir: Path) -> Path:
    path = Path(str(value or ""))
    return path.resolve() if path.is_absolute() else (manifest_dir / path).resolve()


def _load_import_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read GABench manifest: {exc}") from exc
    if payload.get("adapter") != "gabench" or not isinstance(payload.get("tasks"), list):
        raise ValueError("not an OpenMapBench GABench import manifest")
    if not payload["tasks"]:
        raise ValueError("GABench import manifest contains no tasks")
    task_ids = [str(entry.get("task_id", "")) for entry in payload["tasks"]]
    if any(not task_id for task_id in task_ids):
        raise ValueError("every GABench manifest entry must have a task_id")
    duplicates = sorted(task_id for task_id, count in Counter(task_ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate task IDs in GABench manifest: {', '.join(duplicates)}")
    return payload


def _new_batch_id() -> str:
    return f"{_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def run_gabench_batch(
    manifest_path: Path,
    agent_command: str,
    output_root: Path,
    *,
    batch_id: str | None = None,
    timeout_seconds: float | None = None,
    agent: dict[str, Any] | None = None,
    agent_cwd: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run every task in a GABench import manifest and write an isolated batch bundle."""
    manifest_path = manifest_path.resolve()
    if not agent_command.strip():
        raise ValueError("agent command must be non-empty")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout must be greater than zero")
    batch_id = batch_id or _new_batch_id()
    if not BATCH_ID_PATTERN.fullmatch(batch_id):
        raise ValueError(
            "batch ID may contain only letters, numbers, dots, underscores, and dashes"
        )

    imported = _load_import_manifest(manifest_path)
    batch_dir = (output_root / batch_id).resolve()
    task_runs_dir = batch_dir / "task-runs"
    visual_dir = batch_dir / "visual-review"
    task_runs_dir.mkdir(parents=True, exist_ok=False)
    manifest_dir = manifest_path.parent
    total = len(imported["tasks"])
    started = _now()
    start_clock = time.monotonic()
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    print(f"OpenMapBench GABench batch: {batch_id}")
    print(f"Imported tasks: {total}")
    print(f"Batch folder: {batch_dir}")
    for index, entry in enumerate(imported["tasks"], start=1):
        task_id = str(entry["task_id"])
        task_path = _resolve_entry_path(entry.get("task_path"), manifest_dir)
        reference_path = _resolve_entry_path(entry.get("reference_path"), manifest_dir)
        prefix = f"[{index:03d}/{total:03d}] {task_id}"

        reason: str | None = None
        if not task_path.is_file():
            reason = f"task file missing: {task_path}"
        elif not reference_path.is_file():
            reason = f"reference file missing: {reference_path}"
        else:
            try:
                task = load_task(task_path)
            except Exception as exc:  # noqa: BLE001 - report malformed generated contracts
                reason = f"invalid task contract: {type(exc).__name__}: {exc}"
            else:
                missing_inputs = [
                    str(path) for path in task.resolve_input_paths(task_path) if not path.is_file()
                ]
                if task.id != task_id:
                    reason = f"task ID mismatch: manifest={task_id}, contract={task.id}"
                elif missing_inputs:
                    reason = f"input files missing: {', '.join(missing_inputs)}"
                elif entry.get("reference_sha256"):
                    actual_reference_sha256 = sha256_file(reference_path)
                    if actual_reference_sha256 != entry["reference_sha256"]:
                        reason = (
                            "reference checksum mismatch: "
                            f"expected {entry['reference_sha256']}, got {actual_reference_sha256}"
                        )

        if reason:
            print(f"{prefix}: SKIPPED - {reason}")
            skipped.append({"task_id": task_id, "reason": reason})
            continue

        try:
            run_manifest, run_manifest_path = run_task(
                task_path,
                reference_path,
                agent_command,
                task_runs_dir,
                timeout_seconds=timeout_seconds,
                agent=agent,
                agent_cwd=agent_cwd,
            )
        except Exception as exc:  # noqa: BLE001 - one bad task must not stop the batch
            reason = f"runner exception: {type(exc).__name__}: {exc}"
            print(f"{prefix}: ERROR - {reason}")
            skipped.append({"task_id": task_id, "reason": reason})
            continue

        print(f"{prefix}: {run_manifest.status.value} ({run_manifest.duration_seconds:.2f}s)")
        results.append(
            {
                "task_id": task_id,
                "status": run_manifest.status.value,
                "duration_seconds": run_manifest.duration_seconds,
                "run_id": run_manifest.run_id,
                "manifest_path": str(run_manifest_path),
            }
        )

    aggregate = aggregate_manifests(task_runs_dir)
    report_json_path = batch_dir / "report.json"
    report_markdown_path = batch_dir / "report.md"
    _write_json(report_json_path, aggregate)
    report_markdown_path.write_text(report_markdown(aggregate), encoding="utf-8")
    visual = visual_report_from_runs(task_runs_dir, visual_dir)

    failed_results = [
        item for item in results if RunStatus(item["status"]) not in COMPLETED_STATUSES
    ]
    completed_without_failures = not skipped and not failed_results and len(results) == total
    finished = _now()
    batch = {
        "schema_version": "0.1",
        "batch_id": batch_id,
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_commit": imported.get("source_commit"),
        "upstream_license": imported.get("upstream_license"),
        "agent_command": agent_command,
        "agent": agent or {},
        "agent_cwd": str(agent_cwd.resolve()) if agent_cwd else None,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(time.monotonic() - start_clock, 6),
        "task_count": total,
        "executed_count": len(results),
        "skipped_count": len(skipped),
        "completed_without_failures": completed_without_failures,
        "status_counts": dict(sorted(Counter(item["status"] for item in results).items())),
        "results": results,
        "skipped": skipped,
        "aggregate_report": {
            "json": str(report_json_path),
            "markdown": str(report_markdown_path),
        },
        "visual_review": {
            "index": str(visual_dir / "index.html"),
            "comparison_count": visual["comparison_count"],
            "skipped_count": visual["skipped_count"],
        },
        "notice": (
            "Local batch metadata only. GABench content remains in the external checkout and "
            "retains its upstream terms. Manual image reviews are not strict passes."
        ),
    }
    batch_manifest_path = batch_dir / "batch.json"
    _write_json(batch_manifest_path, batch)

    print("Batch complete")
    print(f"Executed: {len(results)}/{total}; skipped: {len(skipped)}")
    print(f"Statuses: {batch['status_counts']}")
    print(f"Report: {report_markdown_path}")
    if visual["comparison_count"]:
        print(f"Visual review: {visual_dir / 'index.html'}")
    return batch, batch_manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run every task in .openmapbench/gabench/manifest.json and create an isolated "
            "batch report plus visual-review gallery."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(".openmapbench/gabench/manifest.json"),
        help="GABench import manifest (default: .openmapbench/gabench/manifest.json)",
    )
    parser.add_argument(
        "--agent-command",
        default=os.environ.get("OPENMAPBENCH_AGENT_COMMAND"),
        help=(
            "Agent command with OpenMapBench placeholders. May also be supplied through "
            "OPENMAPBENCH_AGENT_COMMAND. The command is parsed directly, without a shell."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/gabench"),
        help="Parent folder for timestamped batch directories (default: runs/gabench)",
    )
    parser.add_argument("--batch-id", help="Optional stable batch directory name")
    parser.add_argument("--timeout-seconds", type=float, help="Per-task timeout")
    parser.add_argument(
        "--agent-cwd",
        type=Path,
        default=Path.cwd(),
        help="Agent working directory (default: current directory)",
    )
    parser.add_argument("--agent-name", help="Agent name recorded in run manifests")
    parser.add_argument("--model", help="Model recorded in run manifests")
    parser.add_argument("--skill", action="append", default=[], help="Repeatable skill metadata")
    parser.add_argument("--tool", action="append", default=[], help="Repeatable tool metadata")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.agent_command:
        parser.error("--agent-command or OPENMAPBENCH_AGENT_COMMAND is required")
    if not args.manifest.is_file():
        parser.error(f"manifest does not exist: {args.manifest}")
    if not args.agent_cwd.is_dir():
        parser.error(f"agent working directory does not exist: {args.agent_cwd}")
    agent = {
        key: value
        for key, value in {
            "name": args.agent_name,
            "model": args.model,
            "skills": args.skill,
            "tools": args.tool,
        }.items()
        if value not in (None, [])
    }
    try:
        batch, batch_manifest_path = run_gabench_batch(
            args.manifest,
            args.agent_command,
            args.output_root,
            batch_id=args.batch_id,
            timeout_seconds=args.timeout_seconds,
            agent=agent,
            agent_cwd=args.agent_cwd,
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Batch manifest: {batch_manifest_path}")
    return 0 if batch["completed_without_failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
