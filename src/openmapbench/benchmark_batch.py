"""Run the native OpenMapBench task suite end to end and report the result.

Every native task ships its own frozen reference under ``<task-dir>/reference/``. This runner
discovers those pairs, runs one agent command against each task, and writes an isolated batch
bundle with a detailed HTML report next to the machine-readable ones. The agent never sees the
reference: it is resolved here, by the harness, exactly as the CLI does for a single run.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .batch import (
    failed_results,
    now,
    resolve_batch_id,
    status_counts,
    write_aggregate_reports,
    write_batch_html,
    write_json,
)
from .models import TaskSpec
from .runner import run_task
from .taskio import load_task, sha256_file, validate_task_files
from .visual import is_supported_image_path, visual_report_from_runs

REFERENCE_DIR_NAME = "reference"


@dataclass(frozen=True)
class SuiteTask:
    """One runnable task: its contract, its frozen reference, and where both live."""

    task_id: str
    task_path: Path
    reference_path: Path
    spec: TaskSpec


def reference_solver_command() -> str:
    """Agent command that runs each task's own bundled reference solver."""
    return f"{shlex.quote(sys.executable)} {{task_dir}}/tools/solve.py"


def discover_tasks(
    task_root: Path,
    *,
    only_ids: Sequence[str] | None = None,
    skip_ids: Sequence[str] | None = None,
    verify_inputs: bool = True,
) -> tuple[list[SuiteTask], list[dict[str, str]]]:
    """Pair every task contract under ``task_root`` with its reference artifact.

    A task that cannot be scored fairly — malformed contract, missing reference, missing or
    altered inputs — is reported as skipped rather than run, so a broken checkout is visible
    instead of silently lowering the score.
    """
    task_root = task_root.resolve()
    only = set(only_ids or [])
    skip = set(skip_ids or [])
    tasks: list[SuiteTask] = []
    skipped: list[dict[str, str]] = []
    for task_path in sorted(task_root.glob("*/task.yaml")):
        directory = task_path.parent.name
        try:
            spec = load_task(task_path)
        except Exception as exc:  # noqa: BLE001 - a broken contract must not stop the suite
            skipped.append(
                {
                    "task_id": directory,
                    "reason": f"invalid task contract: {type(exc).__name__}: {exc}",
                }
            )
            continue
        task_id = spec.id
        if only and task_id not in only:
            continue
        if task_id in skip:
            skipped.append({"task_id": task_id, "reason": "skipped via --skip flag"})
            continue
        reference = task_path.parent / REFERENCE_DIR_NAME / Path(spec.output.path).name
        if not reference.is_file():
            skipped.append(
                {"task_id": task_id, "reason": f"reference artifact missing: {reference}"}
            )
            continue
        if verify_inputs:
            findings = validate_task_files(spec, task_path)
            failures = [
                f"{finding['path']} ({finding['reason']})"
                for finding in findings
                if finding["status"] == "failed"
            ]
            if failures:
                skipped.append(
                    {"task_id": task_id, "reason": f"input check failed: {', '.join(failures)}"}
                )
                continue
        tasks.append(
            SuiteTask(
                task_id=task_id,
                task_path=task_path,
                reference_path=reference,
                spec=spec,
            )
        )
    seen = {task.task_id for task in tasks} | {item["task_id"] for item in skipped}
    for task_id in sorted(only - seen):
        skipped.append(
            {"task_id": task_id, "reason": f"no task named {task_id} under {task_root}"}
        )
    return tasks, skipped


def run_benchmark_batch(
    task_root: Path,
    agent_command: str,
    output_root: Path,
    *,
    batch_id: str | None = None,
    timeout_seconds: float | None = None,
    agent: dict[str, Any] | None = None,
    agent_cwd: Path | None = None,
    only_ids: Sequence[str] | None = None,
    skip_ids: Sequence[str] | None = None,
    verify_inputs: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Run every discovered native task with one agent command and write a batch bundle."""
    task_root = task_root.resolve()
    if not task_root.is_dir():
        raise ValueError(f"task root does not exist: {task_root}")
    if not agent_command.strip():
        raise ValueError("agent command must be non-empty")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout must be greater than zero")
    batch_id = resolve_batch_id(batch_id)

    tasks, skipped = discover_tasks(
        task_root,
        only_ids=only_ids,
        skip_ids=skip_ids,
        verify_inputs=verify_inputs,
    )
    batch_dir = (output_root / batch_id).resolve()
    task_runs_dir = batch_dir / "task-runs"
    visual_dir = batch_dir / "visual-review"
    task_runs_dir.mkdir(parents=True, exist_ok=False)

    total = len(tasks) + len(skipped)
    started = now()
    start_clock = time.monotonic()
    results: list[dict[str, Any]] = []

    print(f"OpenMapBench benchmark batch: {batch_id}")
    print(f"Task root: {task_root}")
    print(f"Runnable tasks: {len(tasks)} (skipped before running: {len(skipped)})")
    print(f"Batch folder: {batch_dir}")
    for entry in skipped:
        print(f"[skip] {entry['task_id']}: {entry['reason']}")

    for index, task in enumerate(tasks, start=1):
        prefix = f"[{index:03d}/{len(tasks):03d}] {task.task_id}"
        try:
            run_manifest, run_manifest_path = run_task(
                task.task_path,
                task.reference_path,
                agent_command,
                task_runs_dir,
                timeout_seconds=timeout_seconds,
                agent=agent,
                agent_cwd=agent_cwd,
            )
        except Exception as exc:  # noqa: BLE001 - one bad task must not stop the batch
            reason = f"runner exception: {type(exc).__name__}: {exc}"
            print(f"{prefix}: ERROR - {reason}")
            skipped.append({"task_id": task.task_id, "reason": reason})
            continue

        print(f"{prefix}: {run_manifest.status.value} ({run_manifest.duration_seconds:.2f}s)")
        cost = run_manifest.cost_estimate
        usage = run_manifest.token_usage
        results.append(
            {
                "task_id": task.task_id,
                "status": run_manifest.status.value,
                "category": run_manifest.category,
                "output_kind": run_manifest.output_kind.value,
                "failure_modes": run_manifest.task_metadata.get("failure_modes") or [],
                "duration_seconds": run_manifest.duration_seconds,
                "model": (
                    usage.model if usage and usage.model else run_manifest.agent.get("model")
                ),
                "total_tokens": usage.total_tokens if usage else None,
                "estimated_cost_usd": cost.estimated_cost_usd if cost else None,
                "minimum_cost_usd": cost.minimum_cost_usd if cost else None,
                "maximum_cost_usd": cost.maximum_cost_usd if cost else None,
                "run_id": run_manifest.run_id,
                "manifest_path": str(run_manifest_path),
                "task_path": str(task.task_path),
                "reference_path": str(task.reference_path),
                "reference_sha256": sha256_file(task.reference_path),
            }
        )

    reports = write_aggregate_reports(batch_dir, task_runs_dir)
    aggregate = reports["aggregate"]
    has_images = any(
        is_supported_image_path(Path(task.spec.output.path)) for task in tasks
    )
    visual = (
        visual_report_from_runs(task_runs_dir, visual_dir)
        if has_images
        else {"comparison_count": 0, "skipped_count": 0}
    )

    finished = now()
    failures = failed_results(results)
    batch = {
        "schema_version": "0.1",
        "suite": "openmapbench-native",
        "batch_id": batch_id,
        "task_root": str(task_root),
        "reference_source": (
            f"<task-dir>/{REFERENCE_DIR_NAME}/<artifact>; resolved by the runner, never by the "
            "task contract the agent reads"
        ),
        "input_verification": "checksums verified" if verify_inputs else "not verified",
        "agent_command": agent_command,
        "agent": agent or {},
        "agent_cwd": str(agent_cwd.resolve()) if agent_cwd else None,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(time.monotonic() - start_clock, 6),
        "task_count": total,
        "executed_count": len(results),
        "skipped_count": len(skipped),
        "completed_without_failures": not skipped and not failures and len(results) == total,
        "status_counts": status_counts(results),
        "strict_success_rate": aggregate["strict_success_rate"],
        "usage": aggregate["usage"],
        "results": results,
        "skipped": skipped,
        "aggregate_report": {
            "json": str(reports["json"]),
            "markdown": str(reports["markdown"]),
            "html": str(batch_dir / "report.html"),
        },
        "visual_review": {
            "index": str(visual_dir / "index.html") if visual["comparison_count"] else None,
            "comparison_count": visual["comparison_count"],
            "skipped_count": visual["skipped_count"],
        },
    }
    batch_manifest_path = batch_dir / "batch.json"
    write_json(batch_manifest_path, batch)
    agent_label = (agent or {}).get("name") or Path(shlex.split(agent_command)[0]).name
    write_batch_html(
        batch_dir,
        task_runs_dir,
        aggregate,
        batch,
        title="OpenMapBench native task suite",
        subtitle=(
            f"Batch {batch_id} · {len(results)} of {total} tasks executed by {agent_label}"
        ),
        extra_links=(
            [("visual review", visual_dir / "index.html")]
            if visual["comparison_count"]
            else None
        ),
    )

    rate = aggregate["strict_success_rate"]
    print("Batch complete")
    print(f"Executed: {len(results)}/{total}; skipped: {len(skipped)}")
    print(f"Statuses: {batch['status_counts']}")
    print(f"Strict success rate: {f'{rate:.1%}' if rate is not None else 'not available'}")
    if aggregate["usage"]["runs_with_usage"]:
        print(f"Tokens: {aggregate['usage']['total_tokens']:,} total")
    print(f"Report: {batch_dir / 'report.html'}")
    return batch, batch_manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run every native OpenMapBench task against one agent command and write an "
            "isolated batch bundle with JSON, Markdown, and HTML reports."
        )
    )
    parser.add_argument(
        "--task-root",
        type=Path,
        default=Path("benchmark/tasks"),
        help="Directory holding <task-id>/task.yaml (default: benchmark/tasks)",
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
        "--reference-solver",
        action="store_true",
        help=(
            "Use each task's bundled tools/solve.py as the agent. This is the suite smoke "
            "test: every task must pass its own strict contract."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/benchmark"),
        help="Parent folder for timestamped batch directories (default: runs/benchmark)",
    )
    parser.add_argument("--batch-id", help="Optional stable batch directory name")
    parser.add_argument("--timeout-seconds", type=float, help="Per-task timeout")
    parser.add_argument(
        "--agent-cwd",
        type=Path,
        default=None,
        help=(
            "Agent working directory. By default each task runs from its own "
            "<run_dir>/workspace so agent scratch files stay inside the run."
        ),
    )
    parser.add_argument("--agent-name", help="Agent name recorded in run manifests")
    parser.add_argument("--model", help="Model recorded in run manifests")
    parser.add_argument("--skill", action="append", default=[], help="Repeatable skill metadata")
    parser.add_argument("--tool", action="append", default=[], help="Repeatable tool metadata")
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Repeatable task ID to run; the default is every discovered task",
    )
    parser.add_argument("--skip", action="append", default=[], help="Repeatable task ID to skip")
    parser.add_argument(
        "--no-verify-inputs",
        action="store_true",
        help="Run tasks whose frozen inputs are missing or no longer match their checksums",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    agent_command = args.agent_command
    if args.reference_solver:
        if agent_command:
            parser.error("--reference-solver and --agent-command are mutually exclusive")
        agent_command = reference_solver_command()
    if not agent_command:
        parser.error("--agent-command, OPENMAPBENCH_AGENT_COMMAND, or --reference-solver required")
    if not args.task_root.is_dir():
        parser.error(f"task root does not exist: {args.task_root}")
    if args.agent_cwd is not None and not args.agent_cwd.is_dir():
        parser.error(f"agent working directory does not exist: {args.agent_cwd}")
    agent = {
        key: value
        for key, value in {
            "name": args.agent_name or ("reference-solver" if args.reference_solver else None),
            "model": args.model,
            "skills": args.skill,
            "tools": args.tool,
        }.items()
        if value not in (None, [])
    }
    try:
        batch, batch_manifest_path = run_benchmark_batch(
            args.task_root,
            agent_command,
            args.output_root,
            batch_id=args.batch_id,
            timeout_seconds=args.timeout_seconds,
            agent=agent,
            agent_cwd=args.agent_cwd,
            only_ids=args.task,
            skip_ids=args.skip,
            verify_inputs=not args.no_verify_inputs,
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Batch manifest: {batch_manifest_path}")
    return 0 if batch["completed_without_failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
