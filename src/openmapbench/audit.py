from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from .models import (
    ArtifactLineageLink,
    AuditArtifact,
    AuditEvent,
    AuditToolInvocation,
    AuditTrail,
    OutputKind,
    RunStatus,
)
from .taskio import sha256_file

ACTION_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "web_search",
}
ARTIFACT_ROLES = {
    "task",
    "input",
    "intermediate",
    "working",
    "candidate",
    "reference",
    "log",
}
ROLE_PRIORITY = {
    "working": 0,
    "intermediate": 1,
    "log": 2,
    "input": 3,
    "task": 4,
    "reference": 5,
    "candidate": 6,
}


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {} if value is None else {"value": value}


def _result_fields(item: dict[str, Any], *, include_tool_result: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("status", "exit_code", "error"):
        if key in item and item[key] is not None:
            result[key] = item[key]
    output = item.get("aggregated_output")
    if isinstance(output, str):
        result["output_character_count"] = len(output)
    if include_tool_result and item.get("result") is not None:
        result["tool_result"] = item["result"]
    return result


def _codex_event(
    item: dict[str, Any],
    *,
    event_id: str,
    parent_event_id: str,
    source_lines: list[int],
) -> AuditEvent | None:
    item_type = str(item.get("type") or "")
    if item_type not in ACTION_ITEM_TYPES and not item_type.endswith("_tool_call"):
        return None

    common = {
        "sequence": 1,
        "event_id": event_id,
        "parent_event_id": parent_event_id,
        "source": "codex_jsonl:agent.stdout.log",
        "status": str(item["status"]) if item.get("status") is not None else None,
        "source_lines": source_lines,
    }
    if item_type == "command_execution":
        command = item.get("command")
        extras = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "id",
                "type",
                "status",
                "command",
                "aggregated_output",
                "exit_code",
                "error",
            }
        }
        return AuditEvent(
            **common,
            kind="command",
            name="Command execution",
            command=command if isinstance(command, (str, list)) else str(command or ""),
            parameters=extras,
            result=_result_fields(item),
        )
    if item_type == "mcp_tool_call" or item_type.endswith("_tool_call"):
        tool_name = str(item.get("tool") or item.get("name") or item_type)
        server = item.get("server")
        return AuditEvent(
            **common,
            kind="tool",
            name=f"Tool: {tool_name}",
            tool=AuditToolInvocation(
                name=tool_name,
                server=str(server) if server is not None else None,
                parameters=_mapping(item.get("arguments", item.get("parameters"))),
            ),
            result=_result_fields(item, include_tool_result=True),
        )
    if item_type == "web_search":
        parameters = {
            key: value
            for key, value in item.items()
            if key not in {"id", "type", "status", "result", "error"}
        }
        return AuditEvent(
            **common,
            kind="tool",
            name="Tool: web_search",
            tool=AuditToolInvocation(name="web_search", parameters=parameters),
            result=_result_fields(item, include_tool_result=True),
        )
    changes = item.get("changes")
    return AuditEvent(
        **common,
        kind="file_change",
        name="File changes",
        result=_result_fields(item),
        details={"changes": changes if isinstance(changes, list) else []},
    )


def parse_codex_audit_events(
    stdout: str,
    *,
    parent_event_id: str,
) -> tuple[list[AuditEvent], bool, list[str]]:
    """Normalize auditable Codex JSONL action items while preserving source-line order."""
    items: dict[str, dict[str, Any]] = {}
    source_lines: dict[str, list[int]] = {}
    order: list[str] = []
    detected = False
    notes: list[str] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        event_type = str(payload.get("type") or "")
        if event_type in {
            "thread.started",
            "turn.started",
            "turn.completed",
            "turn.failed",
            "error",
        } or event_type.startswith("item."):
            detected = True
        if not event_type.startswith("item.") or not isinstance(payload.get("item"), dict):
            continue
        item = payload["item"]
        item_id = str(item.get("id") or f"line-{line_number}")
        if item_id not in items:
            items[item_id] = {}
            source_lines[item_id] = []
            order.append(item_id)
        items[item_id].update(item)
        source_lines[item_id].append(line_number)

    events: list[AuditEvent] = []
    for item_id in order:
        event = _codex_event(
            items[item_id],
            event_id=f"codex:{item_id}",
            parent_event_id=parent_event_id,
            source_lines=source_lines[item_id],
        )
        if event is not None:
            events.append(event)
    if detected:
        notes.append(
            "Codex action order is preserved from agent.stdout.log JSONL source lines; "
            "full command output remains in that lossless log."
        )
    return events, detected, notes


def _custom_event(
    payload: dict[str, Any],
    *,
    line_number: int,
    parent_event_id: str,
) -> AuditEvent:
    kind = str(payload.get("kind") or payload.get("type") or "event")
    event_id = str(payload.get("event_id") or payload.get("id") or f"line-{line_number}")
    event_id = f"agent:{event_id}"
    command = payload.get("command")
    if not isinstance(command, (str, list)):
        command = None
    tool_payload = payload.get("tool")
    tool: AuditToolInvocation | None = None
    if isinstance(tool_payload, str):
        tool = AuditToolInvocation(
            name=tool_payload,
            server=str(payload["server"]) if payload.get("server") is not None else None,
            parameters=_mapping(payload.get("parameters", payload.get("arguments"))),
        )
    elif isinstance(tool_payload, dict):
        tool = AuditToolInvocation(
            name=str(tool_payload.get("name") or "tool"),
            server=(
                str(tool_payload["server"])
                if tool_payload.get("server") is not None
                else None
            ),
            parameters=_mapping(
                tool_payload.get(
                    "parameters",
                    tool_payload.get(
                        "arguments",
                        payload.get("parameters", payload.get("arguments")),
                    ),
                )
            ),
        )
    name = str(payload.get("name") or "")
    if not name:
        name = f"Tool: {tool.name}" if tool else kind.replace("_", " ").title()
    parent = str(payload.get("parent_event_id") or parent_event_id)
    if parent not in {parent_event_id} and not parent.startswith(
        ("agent:", "codex:", "runner-")
    ):
        parent = f"agent:{parent}"
    return AuditEvent(
        sequence=1,
        event_id=event_id,
        parent_event_id=parent,
        source="agent_audit:agent.audit.jsonl",
        kind=kind,
        name=name,
        status=str(payload["status"]) if payload.get("status") is not None else None,
        command=command,
        tool=tool,
        parameters=_mapping(payload.get("parameters")),
        result=_mapping(payload.get("result")),
        details=_mapping(payload.get("details")),
        started_at=(str(payload["started_at"]) if payload.get("started_at") else None),
        finished_at=(str(payload["finished_at"]) if payload.get("finished_at") else None),
        source_lines=[line_number],
    )


def parse_agent_audit(
    path: Path,
    *,
    parent_event_id: str,
) -> tuple[list[AuditEvent], list[tuple[dict[str, Any], str | None]], bool, list[str]]:
    """Read optional vendor-neutral events and artifact declarations emitted by an agent."""
    if not path.is_file():
        return [], [], False, []
    events: list[AuditEvent] = []
    artifacts: list[tuple[dict[str, Any], str | None]] = []
    notes: list[str] = []
    detected = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [], True, [f"Could not read agent audit JSONL: {type(exc).__name__}: {exc}"]

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        detected = True
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            notes.append(f"Ignored invalid agent audit JSONL line {line_number}: {exc.msg}")
            continue
        if not isinstance(payload, dict):
            notes.append(f"Ignored non-object agent audit JSONL line {line_number}")
            continue
        payload_type = str(payload.get("type") or payload.get("kind") or "")
        if payload_type == "artifact":
            declaration = payload.get("artifact")
            declaration = declaration if isinstance(declaration, dict) else payload
            artifacts.append((declaration, None))
            continue
        event = _custom_event(
            payload,
            line_number=line_number,
            parent_event_id=parent_event_id,
        )
        events.append(event)
        nested = payload.get("artifacts")
        if isinstance(nested, list):
            artifacts.extend(
                (artifact, event.event_id) for artifact in nested if isinstance(artifact, dict)
            )
    if detected:
        notes.append(
            "Agent-reported action and lineage records were read from agent.audit.jsonl "
            "in file order."
        )
    return events, artifacts, detected, notes


def _resolved(path: Path, base: Path) -> Path:
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _lineage(
    relationship: str,
    target_id: str,
    evidence: str,
) -> ArtifactLineageLink:
    return ArtifactLineageLink(
        relationship=relationship,
        target_id=target_id,
        evidence=evidence,
    )


def collect_audit_artifacts(
    *,
    task_file: Path,
    input_paths: list[Path],
    candidate: Path,
    reference: Path,
    output_dir: Path,
    run_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    agent_audit_path: Path,
    execution_cwd: Path,
    agent_event_id: str,
    events: list[AuditEvent],
    declarations: list[tuple[dict[str, Any], str | None]],
) -> list[AuditArtifact]:
    drafts: dict[str, dict[str, Any]] = {}
    used_ids: set[str] = set()
    input_ids: list[str] = []

    def unique_id(preferred: str) -> str:
        if preferred not in used_ids:
            used_ids.add(preferred)
            return preferred
        index = 2
        while f"{preferred}-{index}" in used_ids:
            index += 1
        value = f"{preferred}-{index}"
        used_ids.add(value)
        return value

    def add(
        path: Path,
        role: str,
        preferred_id: str,
        *,
        lineage: list[ArtifactLineageLink] | None = None,
        metadata: dict[str, Any] | None = None,
        prefer_explicit_id: bool = False,
    ) -> dict[str, Any]:
        resolved = path.resolve()
        key = str(resolved)
        existing = drafts.get(key)
        if existing is None:
            existing = {
                "artifact_id": unique_id(preferred_id),
                "path": key,
                "role": role,
                "lineage": [],
                "metadata": {},
            }
            drafts[key] = existing
        elif ROLE_PRIORITY.get(role, 0) > ROLE_PRIORITY.get(existing["role"], 0):
            existing["role"] = role
        if prefer_explicit_id and existing["artifact_id"].startswith(
            ("intermediate-", "working-", "agent-artifact-")
        ):
            used_ids.discard(existing["artifact_id"])
            existing["artifact_id"] = unique_id(preferred_id)
        for link in lineage or []:
            if link not in existing["lineage"]:
                existing["lineage"].append(link)
        existing["metadata"].update(metadata or {})
        return existing

    add(task_file, "task", "task-contract")
    for index, input_path in enumerate(input_paths, start=1):
        draft = add(input_path, "input", f"input-{index:03d}")
        input_ids.append(draft["artifact_id"])
    add(reference, "reference", "reference")
    candidate_links = [
        _lineage("produced_by", agent_event_id, "runner_observation"),
        *[
            _lineage("declared_input", input_id, "task_contract")
            for input_id in input_ids
        ],
    ]
    add(candidate, "candidate", "candidate", lineage=candidate_links)
    add(stdout_path, "log", "log-agent-stdout")
    add(stderr_path, "log", "log-agent-stderr")
    if agent_audit_path.is_file():
        add(agent_audit_path, "log", "log-agent-audit")

    known_paths = set(drafts)
    intermediate_index = 1
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or str(path.resolve()) in known_paths:
            continue
        add(
            path,
            "intermediate",
            f"intermediate-{intermediate_index:03d}",
            lineage=[_lineage("produced_by", agent_event_id, "runner_observation")],
            metadata={"observed_scope": "run_directory"},
        )
        intermediate_index += 1

    working_index = 1
    for event in events:
        if event.kind != "file_change":
            continue
        changes = event.details.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            path_value = change.get("path", change.get("file_path"))
            if not isinstance(path_value, str) or not path_value.strip():
                continue
            path = _resolved(Path(path_value), execution_cwd)
            if path == candidate:
                role = "candidate"
            elif path == output_dir or output_dir in path.parents:
                role = "intermediate"
            else:
                role = "working"
            add(
                path,
                role,
                f"working-{working_index:03d}",
                lineage=[_lineage("produced_by", event.event_id, "codex_jsonl")],
                metadata={"file_change": change},
            )
            working_index += 1

    for index, (declaration, producer_event_id) in enumerate(declarations, start=1):
        path_value = declaration.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            continue
        role = str(declaration.get("role") or "intermediate")
        if role not in ARTIFACT_ROLES:
            role = "intermediate"
        path = _resolved(Path(path_value), execution_cwd)
        links: list[ArtifactLineageLink] = []
        producers = declaration.get("produced_by")
        if isinstance(producers, str):
            producers = [producers]
        if not isinstance(producers, list):
            producers = [producer_event_id] if producer_event_id else []
        for producer in producers:
            if producer:
                producer_id = str(producer)
                if not producer_id.startswith(("agent:", "codex:", "runner-")):
                    producer_id = f"agent:{producer_id}"
                links.append(_lineage("produced_by", producer_id, "agent_reported"))
        derived = declaration.get("derived_from")
        if isinstance(derived, str):
            derived = [derived]
        if isinstance(derived, list):
            for source in derived:
                source_value = str(source)
                resolved_source = _resolved(Path(source_value), execution_cwd)
                target = drafts.get(str(resolved_source), {}).get("artifact_id", source_value)
                links.append(_lineage("derived_from", target, "agent_reported"))
        add(
            path,
            role,
            str(declaration.get("artifact_id") or f"agent-artifact-{index:03d}"),
            lineage=links,
            metadata=_mapping(declaration.get("metadata")),
            prefer_explicit_id=declaration.get("artifact_id") is not None,
        )

    records: list[AuditArtifact] = []
    for draft in drafts.values():
        path = Path(draft["path"])
        exists = path.is_file()
        sha256: str | None = None
        size_bytes: int | None = None
        if exists:
            try:
                sha256 = sha256_file(path)
                size_bytes = path.stat().st_size
            except OSError as exc:
                draft["metadata"]["inspection_error"] = f"{type(exc).__name__}: {exc}"
        media_type, _ = mimetypes.guess_type(path.name)
        records.append(
            AuditArtifact(
                artifact_id=draft["artifact_id"],
                path=draft["path"],
                role=draft["role"],
                exists_at_finish=exists,
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
                lineage=draft["lineage"],
                metadata=draft["metadata"],
            )
        )
    return records


def build_audit_trail(
    *,
    command: list[str],
    execution_cwd: Path,
    openmapbench_environment: dict[str, str],
    timeout_seconds: float | None,
    agent_started_at: str,
    agent_finished_at: str,
    exit_code: int | None,
    process_status: str,
    stdout: str,
    stdout_path: Path,
    stderr_path: Path,
    agent_audit_path: Path,
    task_file: Path,
    input_paths: list[Path],
    candidate: Path,
    reference: Path,
    output_dir: Path,
    run_dir: Path,
    output_kind: OutputKind,
    run_status: RunStatus,
    evaluation: dict[str, Any] | None,
    error: str | None,
    evaluation_started_at: str | None,
    evaluation_finished_at: str | None,
) -> AuditTrail:
    agent_event_id = "runner-agent-process"
    process_result: dict[str, Any] = {"exit_code": exit_code}
    if process_status != "completed" and error:
        process_result["error"] = error
    outer = AuditEvent(
        sequence=1,
        event_id=agent_event_id,
        source="openmapbench_runner",
        kind="agent_process",
        name="Agent subprocess",
        status=process_status,
        command=command,
        parameters={
            "cwd": str(execution_cwd),
            "timeout_seconds": timeout_seconds,
            "environment": openmapbench_environment,
        },
        result=process_result,
        started_at=agent_started_at,
        finished_at=agent_finished_at,
    )
    codex_events, codex_detected, codex_notes = parse_codex_audit_events(
        stdout,
        parent_event_id=agent_event_id,
    )
    custom_events, declarations, custom_detected, custom_notes = parse_agent_audit(
        agent_audit_path,
        parent_event_id=agent_event_id,
    )
    evaluation_status = "skipped"
    if run_status == RunStatus.EVALUATOR_ERROR:
        evaluation_status = "error"
    elif run_status == RunStatus.NEEDS_REVIEW:
        evaluation_status = "needs_review"
    elif evaluation is not None:
        evaluation_status = "passed" if evaluation.get("success") else "failed"
    evaluation_result = {
        "run_status": run_status.value,
        "success": evaluation.get("success") if evaluation else None,
        "score": evaluation.get("score") if evaluation else None,
        "error": error,
    }
    evaluator = AuditEvent(
        sequence=1,
        event_id="runner-evaluation",
        source="openmapbench_runner",
        kind="evaluation",
        name="Evaluate candidate artifact",
        status=evaluation_status,
        parameters={
            "candidate": str(candidate),
            "reference": str(reference),
            "output_kind": output_kind.value,
        },
        result=evaluation_result,
        started_at=evaluation_started_at,
        finished_at=evaluation_finished_at,
    )
    events = [outer, *codex_events, *custom_events, evaluator]
    events = [event.model_copy(update={"sequence": index}) for index, event in enumerate(events, 1)]
    artifacts = collect_audit_artifacts(
        task_file=task_file,
        input_paths=input_paths,
        candidate=candidate,
        reference=reference,
        output_dir=output_dir,
        run_dir=run_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        agent_audit_path=agent_audit_path,
        execution_cwd=execution_cwd,
        agent_event_id=agent_event_id,
        events=events,
        declarations=declarations,
    )

    capture_sources = ["openmapbench_runner"]
    if codex_detected:
        capture_sources.append("codex_jsonl:agent.stdout.log")
    if custom_detected:
        capture_sources.append("agent_audit:agent.audit.jsonl")
    notes = [*codex_notes, *custom_notes]
    parse_warnings = [note for note in notes if note.startswith(("Ignored", "Could not"))]
    if codex_detected and custom_detected:
        notes.append(
            "Codex and agent-audit actions each preserve their source order; because the streams "
            "have no shared clock, Codex actions are listed before agent-audit actions."
        )
    if codex_detected or custom_detected:
        inner_trace_status = "partial" if parse_warnings else "captured"
    else:
        inner_trace_status = "unavailable"
        notes.append(
            "The agent emitted no recognized inner trace. This does not mean no tools ran; use "
            "Codex --json or append JSONL events to OPENMAPBENCH_AUDIT_PATH."
        )
    return AuditTrail(
        inner_trace_status=inner_trace_status,
        capture_sources=capture_sources,
        events=events,
        artifacts=artifacts,
        notes=notes,
    )
