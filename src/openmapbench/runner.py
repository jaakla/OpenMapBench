from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .audit import build_audit_trail
from .capture import CaptureConfig, ContentCapture
from .evaluator import evaluate
from .integrity import check_run, withheld_paths
from .models import (
    FileRecord,
    OutputKind,
    RunManifest,
    RunStatus,
    TaskIsolation,
    TaskSpec,
)
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


def _stage_task(
    spec: TaskSpec, task_file: Path, staged_dir: Path
) -> tuple[Path, list[Path], list[str], dict[str, str]]:
    """Copy the contract and only the files it declares into the run directory.

    The agent is pointed at this copy, so the reference artifact, the solver that produced it,
    and the provenance notes that describe every wrong approach are simply not present. Input
    paths are rewritten to the copy, which also isolates tasks whose inputs live outside their
    own directory. Staging never raises: a file it cannot copy is recorded as a note, and the
    run proceeds and fails honestly.
    """
    payload = spec.model_dump(mode="json")
    # The contract the agent needs is the prompt, the inputs, the required artifact and the
    # tolerances it will be held to. Everything else in a task file is written for contributors
    # and reviewers: metadata.tolerance_rationale names every trap and the measurement that
    # discriminates it, metadata.reference_method names the solver and the libraries it used,
    # metadata.skills names the technique, and an input's provenance prose has been seen to
    # point at the notes beside it. None of it is withheld by hiding files, so strip it here.
    payload.pop("metadata", None)
    payload["inputs"] = [
        {key: value for key, value in declared.items() if key in {"path", "role"} and value}
        for declared in payload.get("inputs", [])
    ]
    notes: list[str] = []
    staged_inputs: list[Path] = []
    aliases: dict[str, str] = {}
    used: set[str] = set()
    sources = spec.resolve_input_paths(task_file)
    for index, (declared, source) in enumerate(zip(spec.inputs, sources, strict=True)):
        relative = Path(declared.path)
        # Keep the declared layout wherever it is safe, so the contract the agent reads is the
        # contract that was written. Only inputs living outside the task directory are moved.
        if relative.is_absolute() or ".." in relative.parts or not declared.path.strip():
            name = relative.name or f"input-{index:02d}"
            if name in used:
                name = f"{index:02d}-{name}"
            relative = Path("inputs") / name
            payload["inputs"][index]["path"] = str(relative)
        used.add(relative.name)
        destination = staged_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, destination)
            staged_inputs.append(destination)
            aliases[str(destination.resolve())] = str(source)
        except OSError as exc:
            notes.append(f"could not stage {source}: {type(exc).__name__}: {exc}")
    staged_task_file = staged_dir / "task.yaml"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_task_file.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    aliases[str(staged_task_file.resolve())] = str(task_file)
    return staged_task_file, staged_inputs, notes, aliases


def _render_command(command: str, paths: dict[str, str]) -> list[str]:
    try:
        return [token.format_map(paths) for token in shlex.split(command)]
    except KeyError as exc:
        raise ValueError(f"unknown agent command placeholder: {exc.args[0]}") from exc


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _short(value: str, limit: int = 500) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 3]}..."


def _verbose_message(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _action_summary(item: dict[str, Any]) -> str | None:
    item_type = str(item.get("type") or "")
    if item_type == "command_execution":
        return f"command: {_short(str(item.get('command') or 'unknown'))}"
    if item_type == "mcp_tool_call" or item_type.endswith("_tool_call"):
        server = f"{item.get('server')}/" if item.get("server") else ""
        tool = str(item.get("tool") or item.get("name") or item_type)
        arguments = item.get("arguments", item.get("parameters"))
        suffix = ""
        if arguments is not None:
            suffix = f" {_short(json.dumps(arguments, ensure_ascii=False, sort_keys=True))}"
        return f"tool: {server}{tool}{suffix}"
    if item_type == "web_search":
        query = item.get("query", item.get("queries", ""))
        return f"web search: {_short(str(query))}"
    if item_type == "file_change":
        changes = item.get("changes")
        paths = []
        if isinstance(changes, list):
            paths = [
                str(change.get("path", change.get("file_path", "")))
                for change in changes
                if isinstance(change, dict)
            ]
        return f"file changes: {_short(', '.join(path for path in paths if path) or 'recorded')}"
    return None


def _json_object(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _emit_agent_line(
    line: str,
    *,
    stream_name: str,
    shown_items: set[str],
    payload: dict[str, Any] | None = None,
) -> None:
    text = line.rstrip("\r\n")
    if not text:
        return
    if stream_name == "stdout":
        if payload is None:
            _verbose_message(f"[agent:stdout] {text}")
            return
        event_type = str(payload.get("type") or "")
        item = payload.get("item")
        if event_type.startswith("item.") and isinstance(item, dict):
            item_id = str(item.get("id") or "")
            summary = _action_summary(item)
            if summary and item_id not in shown_items:
                _verbose_message(f"[agent] {summary}")
                shown_items.add(item_id)
            if event_type == "item.completed" and summary:
                status = item.get("status") or "completed"
                exit_code = item.get("exit_code")
                exit_text = f", exit {exit_code}" if exit_code is not None else ""
                _verbose_message(f"[agent] {item.get('type')}: {status}{exit_text}")
            return
        if event_type == "turn.completed":
            usage = payload.get("usage")
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            suffix = f" ({total:,} tokens)" if isinstance(total, int) else ""
            _verbose_message(f"[agent] turn completed{suffix}")
            return
        if event_type in {"turn.failed", "error"}:
            detail = payload.get("error", payload.get("message", "unknown error"))
            _verbose_message(f"[agent] {event_type}: {_short(str(detail))}")
        return
    _verbose_message(f"[agent:stderr] {text}")


def _run_streaming_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: float | None,
    verbose: bool,
    capture: ContentCapture | None,
) -> subprocess.CompletedProcess[str]:
    """Run the agent while reading its output live, so transient files can be preserved."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        # A benchmark run is non-interactive. Without this an agent CLI that reads stdin blocks
        # on the harness's inherited handle and burns its whole timeout waiting for a prompt.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    shown_items: set[str] = set()
    output_lock = threading.Lock()

    def drain(stream: Any, parts: list[str], stream_name: str) -> None:
        for line in stream:
            parts.append(line)
            payload = (
                _json_object(line.rstrip("\r\n"))
                if stream_name == "stdout" and (capture is not None or verbose)
                else None
            )
            if capture is not None and stream_name == "stdout" and payload is not None:
                capture.observe_codex_event(payload)
            if verbose:
                with output_lock:
                    _emit_agent_line(
                        line,
                        stream_name=stream_name,
                        shown_items=shown_items,
                        payload=payload,
                    )
        stream.close()

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = threading.Thread(
        target=drain,
        args=(process.stdout, stdout_parts, "stdout"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, stderr_parts, "stderr"),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        stdout_thread.join()
        stderr_thread.join()
        raise subprocess.TimeoutExpired(
            exc.cmd,
            exc.timeout,
            output="".join(stdout_parts),
            stderr="".join(stderr_parts),
        ) from exc
    stdout_thread.join()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        command,
        return_code,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def run_task(
    task_file: Path,
    reference: Path,
    agent_command: str,
    run_root: Path,
    *,
    timeout_seconds: float | None = None,
    agent: dict[str, Any] | None = None,
    agent_cwd: Path | None = None,
    verbose: bool = False,
    isolate_task: bool = True,
) -> tuple[RunManifest, Path]:
    """Execute an arbitrary agent command, evaluate its artifact, and always write a manifest.

    The agent runs from ``<run_dir>/workspace`` unless ``agent_cwd`` is given, and is pointed at
    a staged copy of the task holding only the contract and its declared inputs unless
    ``isolate_task`` is disabled. Evaluation always uses the original task and reference.
    """
    task_file = task_file.resolve()
    reference = reference.resolve()
    spec = load_task(task_file)
    timestamp = _now().strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}-{spec.id}-{uuid.uuid4().hex[:8]}"
    run_dir = (run_root / run_id).resolve()
    output_dir = run_dir / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=False)
    workspace_dir = run_dir / "workspace"
    workspace_dir.mkdir()
    # What the agent sees. Staging withholds the reference, the reference solver, and the
    # provenance notes that would otherwise sit one directory listing away from the contract.
    if isolate_task:
        agent_task_file, staged_inputs, staging_notes, staged_aliases = _stage_task(
            spec, task_file, run_dir / "task"
        )
    else:
        agent_task_file, staged_inputs, staging_notes, staged_aliases = task_file, [], [], {}
    candidate = spec.resolve_output_path(output_dir)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "agent.stdout.log"
    stderr_path = run_dir / "agent.stderr.log"
    agent_audit_path = run_dir / "agent.audit.jsonl"

    paths = {
        "task_file": str(agent_task_file),
        "task_dir": str(agent_task_file.parent),
        "output_dir": str(output_dir),
        "output_path": str(candidate),
        "run_dir": str(run_dir),
        "workspace_dir": str(workspace_dir),
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
            "OPENMAPBENCH_WORKSPACE_DIR": paths["workspace_dir"],
            "OPENMAPBENCH_AUDIT_PATH": str(agent_audit_path),
        }
    )

    started = _now()
    agent_started = started
    agent_finished = started
    start_clock = time.monotonic()
    status = RunStatus.AGENT_ERROR
    exit_code: int | None = None
    evaluation_payload: dict[str, Any] | None = None
    error: str | None = None
    stdout = ""
    stderr = ""
    evaluation_started: datetime | None = None
    evaluation_finished: datetime | None = None
    # Scratch files an agent writes to its working directory land inside the isolated run
    # directory by default, so they are inventoried by the audit and never leak into the project.
    execution_cwd = agent_cwd.resolve() if agent_cwd else workspace_dir
    capture_config = CaptureConfig.from_environment()
    content_capture = (
        ContentCapture(
            run_dir=run_dir,
            execution_cwd=execution_cwd,
            started_at=started,
            config=capture_config,
            exempt_paths=[task_file, reference, *spec.resolve_input_paths(task_file)],
            exempt_dirs=[run_root.resolve()],
        )
        if capture_config.enabled
        else None
    )
    if verbose:
        _verbose_message(f"[openmapbench] task {spec.id}: {spec.title}")
        _verbose_message(f"[openmapbench] run directory: {run_dir}")
        _verbose_message(f"[openmapbench] starting agent: {shlex.join(command)}")
    try:
        if verbose or content_capture is not None:
            process = _run_streaming_process(
                command,
                cwd=execution_cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                verbose=verbose,
                capture=content_capture,
            )
        else:
            process = subprocess.run(
                command,
                cwd=execution_cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        exit_code = process.returncode
        stdout = process.stdout
        stderr = process.stderr
        agent_finished = _now()
        if content_capture is not None:
            content_capture.sweep(reason="final_state", force=True)
        if verbose:
            _verbose_message(f"[openmapbench] agent exited with code {exit_code}")
        if exit_code != 0:
            error = f"agent command exited with code {exit_code}"
        elif not candidate.is_file():
            status = RunStatus.MISSING_OUTPUT
            error = f"agent did not create expected output: {candidate}"
        else:
            evaluation_started = _now()
            if verbose:
                _verbose_message(f"[openmapbench] evaluating {candidate.name}")
            if (
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
                                "details": {
                                    "candidate_error": f"{type(exc).__name__}: {exc}"
                                },
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
                    result = evaluate(spec, candidate, reference, task_file=task_file)
                    evaluation_payload = result.to_dict()
                    status = RunStatus.PASSED if result.success else RunStatus.FAILED
                except Exception as exc:  # noqa: BLE001 - plugins may raise arbitrary exceptions
                    status = RunStatus.EVALUATOR_ERROR
                    error = f"{type(exc).__name__}: {exc}"
            evaluation_finished = _now()
            if verbose:
                _verbose_message(f"[openmapbench] evaluation status: {status.value}")
    except subprocess.TimeoutExpired as exc:
        agent_finished = _now()
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
        if content_capture is not None:
            content_capture.sweep(reason="final_state", force=True)
        error = f"agent command timed out after {timeout_seconds} seconds"
        if verbose:
            _verbose_message(f"[openmapbench] {error}")
    except OSError as exc:
        agent_finished = _now()
        error = f"{type(exc).__name__}: {exc}"
        if verbose:
            _verbose_message(f"[openmapbench] could not start agent: {error}")

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
    process_status = "completed" if exit_code == 0 else "failed"
    if exit_code is None and error and "timed out" in error:
        process_status = "timed_out"
    openmapbench_environment = {
        key: environment[key]
        for key in sorted(environment)
        if key.startswith("OPENMAPBENCH_")
    }
    audit = build_audit_trail(
        command=command,
        execution_cwd=execution_cwd,
        openmapbench_environment=openmapbench_environment,
        timeout_seconds=timeout_seconds,
        agent_started_at=agent_started.isoformat(),
        agent_finished_at=agent_finished.isoformat(),
        exit_code=exit_code,
        process_status=process_status,
        stdout=stdout,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        agent_audit_path=agent_audit_path,
        task_file=task_file,
        input_paths=spec.resolve_input_paths(task_file),
        staged_aliases=staged_aliases,
        candidate=candidate,
        reference=reference,
        output_dir=output_dir,
        run_dir=run_dir,
        content_capture=content_capture,
        output_kind=spec.output.kind,
        run_status=status,
        evaluation=evaluation_payload,
        error=error,
        evaluation_started_at=(evaluation_started.isoformat() if evaluation_started else None),
        evaluation_finished_at=(
            evaluation_finished.isoformat() if evaluation_finished else None
        ),
    )
    isolation = TaskIsolation(
        mode="staged" if isolate_task else "direct",
        task_file=str(agent_task_file),
        staged_inputs=[str(path) for path in staged_inputs],
        withheld_paths=[
            str(path) for path in withheld_paths(task_file, reference, staged=isolate_task)
        ],
        notes=staging_notes,
    )
    # Detection is the second defence: prevention can be defeated by an agent that goes looking
    # on the host filesystem, so a run that touched withheld material is marked, not counted.
    integrity = check_run(
        audit, withheld_paths(task_file, reference, staged=isolate_task)
    )
    if integrity.contaminated and verbose:
        _verbose_message(
            f"[openmapbench] contaminated run: {len(integrity.findings)} contacts with "
            "withheld material"
        )
    manifest = RunManifest(
        run_id=run_id,
        status=status,
        task_id=spec.id,
        task_title=spec.title,
        category=spec.category,
        task_metadata=spec.metadata,
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
        audit=audit,
        isolation=isolation,
        integrity=integrity,
        evaluation=evaluation_payload,
        error=error,
    )
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if verbose:
        _verbose_message(f"[openmapbench] manifest: {manifest_path}")
    return manifest, manifest_path
