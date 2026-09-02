from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .adapters.gabench import import_gabench
from .evaluator import evaluate as evaluate_result
from .models import RunStatus
from .reporting import aggregate_manifests, report_markdown
from .runner import run_task
from .taskio import load_task, validate_task_files
from .usage import backfill_usage
from .visual import visual_report_from_gabench, visual_report_from_runs

app = typer.Typer(no_args_is_help=True, help="OpenMapBench: artifact-first GIS agent benchmark")


@app.command()
def validate(task: Path) -> None:
    """Validate a task contract, input paths, and declared checksums."""
    spec = load_task(task)
    findings = validate_task_files(spec, task.resolve())
    typer.echo(f"valid contract: {spec.id}")
    for finding in findings:
        typer.echo(f"{finding['status']}: {finding['path']} ({finding['reason']})")
    if any(finding["status"] == "failed" for finding in findings):
        raise typer.Exit(code=1)


@app.command("evaluate")
def evaluate_command(
    task: Path,
    candidate: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    reference: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Evaluate one candidate artifact against reference ground truth."""
    spec = load_task(task)
    result = evaluate_result(spec, candidate, reference)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    raise typer.Exit(code=0 if result.success else 1)


@app.command()
def run(
    task: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    reference: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    agent_command: Annotated[
        str,
        typer.Option(
            help=(
                "Command to run without a shell. Supports {task_file}, {task_dir}, "
                "{output_dir}, {output_path}, and {run_dir} placeholders."
            )
        ),
    ],
    run_root: Annotated[Path, typer.Option(help="Directory that receives immutable runs.")] = Path(
        "runs"
    ),
    timeout_seconds: Annotated[float | None, typer.Option(min=0.001)] = None,
    agent_name: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    skill: Annotated[list[str] | None, typer.Option()] = None,
    tool: Annotated[list[str] | None, typer.Option()] = None,
    agent_cwd: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            file_okay=False,
            help="Agent working directory (default: the run's own workspace/ folder).",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show runner stages and live agent command/tool progress.",
        ),
    ] = False,
) -> None:
    """Run one task end to end and write artifact, logs, and a run manifest."""
    manifest, manifest_path = run_task(
        task,
        reference,
        agent_command,
        run_root,
        timeout_seconds=timeout_seconds,
        agent={
            key: value
            for key, value in {
                "name": agent_name,
                "model": model,
                "skills": skill or [],
                "tools": tool or [],
            }.items()
            if value not in (None, [])
        },
        agent_cwd=agent_cwd,
        verbose=verbose,
    )
    typer.echo(str(manifest_path))
    typer.echo(f"status: {manifest.status.value}")
    completed = {RunStatus.PASSED, RunStatus.NEEDS_REVIEW}
    raise typer.Exit(code=0 if manifest.status in completed else 1)


@app.command()
def report(
    run_root: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[
        Path | None, typer.Option(help="Optional JSON or Markdown report path.")
    ] = None,
) -> None:
    """Aggregate run manifests and calculate the strict success score."""
    aggregate = aggregate_manifests(run_root)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() in {".md", ".markdown"}:
            output.write_text(report_markdown(aggregate), encoding="utf-8")
        else:
            output.write_text(json.dumps(aggregate, indent=2, sort_keys=True), encoding="utf-8")
        typer.echo(str(output.resolve()))
    else:
        typer.echo(json.dumps(aggregate, indent=2, sort_keys=True))


@app.command("usage-backfill")
def usage_backfill(
    run_root: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Recover token usage and cost estimates in existing manifests from agent logs."""
    summary = backfill_usage(run_root)
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    if summary["invalid_manifests"]:
        raise typer.Exit(code=1)


@app.command("gabench-import")
def gabench_import(
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option()] = Path(".openmapbench/gabench"),
    reference_root: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            file_okay=False,
            help="Optional trusted-run directory containing GABench layer references.",
        ),
    ] = None,
    hash_inputs: Annotated[bool, typer.Option(help="Hash referenced source inputs.")] = True,
) -> None:
    """Create local OpenMapBench bridge tasks from an external GABench checkout."""
    manifest = import_gabench(
        source,
        output,
        reference_root=reference_root,
        hash_inputs=hash_inputs,
    )
    typer.echo(str((output / "manifest.json").resolve()))
    typer.echo(f"source tasks: {manifest['source_task_count']}")
    typer.echo(f"generated tasks: {manifest['generated_task_count']}")
    typer.echo(f"deterministic MVP tasks: {manifest['deterministic_supported_count']}")


@app.command("visual-report")
def visual_report(
    run_root: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option(help="Folder for HTML, CSV, manifest, and PNGs.")] = Path(
        "visual-reviews"
    ),
    max_panel_width: Annotated[int, typer.Option(min=100)] = 1200,
    max_panel_height: Annotated[int, typer.Option(min=100)] = 1000,
) -> None:
    """Create a manual side-by-side image review from OpenMapBench run manifests."""
    result = visual_report_from_runs(
        run_root,
        output,
        max_panel_width=max_panel_width,
        max_panel_height=max_panel_height,
    )
    typer.echo(str((output / "index.html").resolve()))
    typer.echo(f"comparisons: {result['comparison_count']}")
    typer.echo(f"skipped: {result['skipped_count']}")
    if result["comparison_count"] == 0:
        raise typer.Exit(code=1)


@app.command("gabench-visual-report")
def gabench_visual_report(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    candidate_root: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option(help="Folder for HTML, CSV, manifest, and PNGs.")] = Path(
        "visual-reviews/gabench"
    ),
    max_panel_width: Annotated[int, typer.Option(min=100)] = 1200,
    max_panel_height: Annotated[int, typer.Option(min=100)] = 1000,
) -> None:
    """Create a manual visual report from a GABench import manifest and generated images."""
    result = visual_report_from_gabench(
        manifest,
        candidate_root,
        output,
        max_panel_width=max_panel_width,
        max_panel_height=max_panel_height,
    )
    typer.echo(str((output / "index.html").resolve()))
    typer.echo(f"comparisons: {result['comparison_count']}")
    typer.echo(f"skipped: {result['skipped_count']}")
    if result["comparison_count"] == 0:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
