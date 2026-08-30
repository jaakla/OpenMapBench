from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .evaluator import evaluate
from .models import FileRecord, OutputKind, RunManifest, RunStatus
from .pricing import estimate_cost
from .taskio import load_task, sha256_file
from .usage import parse_agent_usage
from .visual import image_metadata, is_supported_image_path


def _now() -> datetime:
    return datetime.now(UTC)


def _file_record(path: Path) -> FileRecord:
    return FileRecord(
        path=str(path.resolve()),
        sha256=sha256_file(path) if path.is_file() else None,
        size_bytes=path.stat().st_size if path.is_file() else None,
    )


def _git_commit(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _render_command(command: str, paths: dict[str, str]) -> list[str]:
    try:
        return [token.format_map(paths) for token in shlex.split(command)]
    except KeyError as exc:
        raise ValueError(f"unknown agent command placeholder: {exc.args[0]}") from exc


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def run_task(
    task_file: Path,
    reference: Path,
    agent_command: str,
    run_root: Path,
    *,
    timeout_seconds: float | None = None,
    agent: dict[str, Any] | None = None,
    agent_cwd: Path | None = None,
) -> tuple[RunManifest, Path]:
    """Execute an arbitrary agent command, evaluate its artifact, and always write a manifest."""
    task_file = task_file.resolve()
    reference = reference.resolve()
    spec = load_task(task_file)
    timestamp = _now().strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}-{spec.id}-{uuid.uuid4().hex[:8]}"
    run_dir = (run_root / run_id).resolve()
    output_dir = run_dir / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=False)
    candidate = spec.resolve_output_path(output_dir)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "agent.stdout.log"
    stderr_path = run_dir / "agent.stderr.log"

    paths = {
        "task_file": str(task_file),
        "task_dir": str(task_file.parent),
        "output_dir": str(output_dir),
        "output_path": str(candidate),
        "run_dir": str(run_dir),
    }
    command = _render_command(agent_command, paths)
    environment = os.environ.copy()
    environment.update(
        {
            "OPENMAPBENCH_TASK_FILE": paths["task_file"],
            "OPENMAPBENCH_TASK_DIR": paths["task_dir"],
            "OPENMAPBENCH_OUTPUT_DIR": paths["output_dir"],
            "OPENMAPBENCH_OUTPUT_PATH": paths["output_path"],
            "OPENMAPBENCH_RUN_DIR": paths["run_dir"],
        }
    )

    started = _now()
    start_clock = time.monotonic()
    status = RunStatus.AGENT_ERROR
    exit_code: int | None = None
    evaluation_payload: dict[str, Any] | None = None
    error: str | None = None
    stdout = ""
    stderr = ""
    try:
        process = subprocess.run(
            command,
            cwd=agent_cwd.resolve() if agent_cwd else task_file.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = process.returncode
        stdout = process.stdout
        stderr = process.stderr
        if exit_code != 0:
            error = f"agent command exited with code {exit_code}"
        elif not candidate.is_file():
            status = RunStatus.MISSING_OUTPUT
            error = f"agent did not create expected output: {candidate}"
        elif (
            spec.output.kind == OutputKind.FILE
            and is_supported_image_path(candidate)
            and is_supported_image_path(reference)
        ):
            try:
                candidate_metadata = image_metadata(candidate)
            except OSError as exc:
                evaluation_payload = {
                    "success": False,
                    "score": 0.0,
                    "checks": [
                        {
                            "id": "image_decodable",
                            "passed": False,
                            "required": True,
                            "details": {"candidate_error": f"{type(exc).__name__}: {exc}"},
                        }
                    ],
                    "diagnostics": {"strictly_scored": True},
                }
                status = RunStatus.FAILED
            else:
                try:
                    reference_metadata = image_metadata(reference)
                except OSError as exc:
                    status = RunStatus.EVALUATOR_ERROR
                    error = f"invalid reference image: {type(exc).__name__}: {exc}"
                else:
                    evaluation_payload = {
                        "success": None,
                        "score": None,
                        "checks": [
                            {
                                "id": "image_decodable",
                                "passed": True,
                                "required": True,
                                "details": {
                                    "candidate": candidate_metadata,
                                    "reference": reference_metadata,
                                },
                            }
                        ],
                        "diagnostics": {
                            "review_mode": "manual_side_by_side",
                            "strictly_scored": False,
                        },
                    }
                    status = RunStatus.NEEDS_REVIEW
        else:
            try:
                result = evaluate(spec, candidate, reference)
                evaluation_payload = result.to_dict()
                status = RunStatus.PASSED if result.success else RunStatus.FAILED
            except Exception as exc:  # noqa: BLE001 - plugins may raise arbitrary exceptions
                status = RunStatus.EVALUATOR_ERROR
                error = f"{type(exc).__name__}: {exc}"
    except subprocess.TimeoutExpired as exc:
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
        error = f"agent command timed out after {timeout_seconds} seconds"
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"

    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    finished = _now()
    agent_metadata = agent or {}
    token_usage = parse_agent_usage(
        stdout,
        stderr,
        declared_model=agent_metadata.get("model"),
    )
    cost_estimate = estimate_cost(token_usage) if token_usage is not None else None
    input_records = [
        _file_record(path) if path.is_file() else FileRecord(path=str(path))
        for path in spec.resolve_input_paths(task_file)
    ]
    manifest = RunManifest(
        run_id=run_id,
        status=status,
        task_id=spec.id,
        task_title=spec.title,
        category=spec.category,
        output_kind=spec.output.kind,
        task_file=_file_record(task_file),
        inputs=input_records,
        candidate=_file_record(candidate) if candidate.is_file() else None,
        reference=_file_record(reference)
        if reference.is_file()
        else FileRecord(path=str(reference)),
        command=command,
        agent=agent_metadata,
        environment={
            "openmapbench": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "openmapbench_env": sorted(
                key for key in environment if key.startswith("OPENMAPBENCH_")
            ),
        },
        benchmark_commit=_git_commit(task_file.parent),
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round(time.monotonic() - start_clock, 6),
        exit_code=exit_code,
        token_usage=token_usage,
        cost_estimate=cost_estimate,
        evaluation=evaluation_payload,
        error=error,
    )
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest, manifest_path
