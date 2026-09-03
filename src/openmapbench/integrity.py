"""Decide whether a run is admissible as evidence of an agent's capability.

A benchmark task directory holds the answer as well as the question: the reference artifact,
the solver that built it, and provenance notes describing exactly what each wrong approach gets
wrong. An agent with a shell will find those if they are reachable, and a run that copied the
reference tells you nothing about GIS skill.

Two defences, and this module is the second one. The runner withholds the material by staging
only the contract and the declared inputs; this module reads the execution audit afterwards and
records any contact with what was withheld. Prevention can be defeated by an agent that goes
looking on the host filesystem, so detection is what keeps the score honest: a contaminated run
is reported as contaminated rather than counted.

It reads the audit only. It never inspects the artifact, and it never changes an evaluation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AuditTrail, IntegrityFinding, IntegrityReport

# The harness's own invocation names the task and reference paths; that is not the agent looking.
HARNESS_EVENT_KINDS = {"agent_process", "evaluation"}

MAX_DETAIL_CHARS = 300


def withheld_paths(task_file: Path, reference: Path, *, staged: bool) -> list[Path]:
    """The paths this run must not see.

    When the task is staged the agent was given a copy, so any mention of the original task
    directory means it went looking for the source. When it is not staged the task directory is
    intentionally reachable, and only the answer inside it is out of bounds.
    """
    task_dir = task_file.parent
    if staged:
        return [task_dir, reference]
    return [task_dir / "tools", task_dir / "reference", reference]


def _haystack(event: dict[str, Any]) -> str:
    command = event.get("command")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    parts = [str(command or ""), str(event.get("name") or "")]
    for field in ("parameters", "result", "details", "tool"):
        value = event.get(field)
        if value:
            parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return " ".join(parts)


def _detail(text: str, needle: str) -> str:
    index = text.find(needle)
    start = max(index - 60, 0)
    excerpt = text[start : index + len(needle) + 120].strip()
    if len(excerpt) > MAX_DETAIL_CHARS:
        excerpt = f"{excerpt[:MAX_DETAIL_CHARS]}…"
    return excerpt


def check_run(
    audit: AuditTrail | None,
    withheld: list[Path],
    *,
    checked: bool = True,
) -> IntegrityReport:
    """Report every recorded contact between the agent and withheld material."""
    # Longest first, so a finding names the most specific thing that was touched: the
    # reference artifact itself rather than the directory that happens to contain it.
    paths = sorted({str(path.resolve()) for path in withheld}, key=len, reverse=True)
    if not checked or audit is None:
        return IntegrityReport(checked=False, contaminated=False, withheld_paths=paths)
    findings: list[IntegrityFinding] = []
    for event in audit.events:
        if event.kind in HARNESS_EVENT_KINDS:
            continue
        text = _haystack(event.model_dump(mode="json"))
        if not text.strip():
            continue
        for path in paths:
            if path in text:
                findings.append(
                    IntegrityFinding(
                        path=path,
                        detail=_detail(text, path),
                        event_id=event.event_id,
                        sequence=event.sequence,
                    )
                )
                break
    return IntegrityReport(
        checked=True,
        contaminated=bool(findings),
        withheld_paths=paths,
        findings=findings,
    )
