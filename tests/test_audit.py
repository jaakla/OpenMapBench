import json
import shlex
import sys
from pathlib import Path

from openmapbench.runner import run_task


def test_runner_records_ordered_actions_parameters_and_artifact_lineage(
    tmp_path: Path,
) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: audit-demo
title: Audit demo
category: scalar
prompt: Read the input and write 42.
inputs:
  - path: values.txt
    role: source
output:
  path: result.txt
  kind: scalar
""".strip(),
        encoding="utf-8",
    )
    source = tmp_path / "values.txt"
    source.write_text("40,2\n", encoding="utf-8")
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text(
        """
import json
import os
from pathlib import Path

output = Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"])
intermediate = Path(os.environ["OPENMAPBENCH_OUTPUT_DIR"]) / "working.txt"
source = Path(os.environ["OPENMAPBENCH_TASK_DIR"]) / "values.txt"
intermediate.write_text("42\\n", encoding="utf-8")
output.write_text(intermediate.read_text(encoding="utf-8"), encoding="utf-8")

events = [
    {
        "type": "item.started",
        "item": {
            "id": "cmd-1",
            "type": "command_execution",
            "command": "python calculate.py --input values.txt --output working.txt",
            "status": "in_progress",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "cmd-1",
            "type": "command_execution",
            "command": "python calculate.py --input values.txt --output working.txt",
            "aggregated_output": "created working.txt",
            "exit_code": 0,
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "tool-1",
            "type": "mcp_tool_call",
            "server": "qgis",
            "tool": "buffer",
            "arguments": {"distance": 500, "units": "metres"},
            "result": {"feature_count": 3},
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "files-1",
            "type": "file_change",
            "changes": [{"path": str(output), "kind": "create"}],
            "status": "completed",
        },
    },
]
for event in events:
    print(json.dumps(event))

audit_event = {
    "type": "tool",
    "event_id": "translate-1",
    "name": "Translate intermediate",
    "status": "completed",
    "tool": {
        "server": "gdal",
        "name": "vector_translate",
        "parameters": {"format": "GeoJSON", "overwrite": True},
    },
    "artifacts": [
        {
            "path": str(intermediate),
            "role": "intermediate",
            "derived_from": [str(source)],
        }
    ],
}
Path(os.environ["OPENMAPBENCH_AUDIT_PATH"]).write_text(
    json.dumps(audit_event) + "\\n", encoding="utf-8"
)
""".strip(),
        encoding="utf-8",
    )

    manifest, manifest_path = run_task(
        task,
        reference,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}",
        tmp_path / "runs",
    )

    assert manifest.schema_version == "0.3"
    assert manifest.audit is not None
    assert manifest.audit.inner_trace_status == "captured"
    assert manifest.audit.capture_sources == [
        "openmapbench_runner",
        "codex_jsonl:agent.stdout.log",
        "agent_audit:agent.audit.jsonl",
    ]
    assert [event.sequence for event in manifest.audit.events] == list(
        range(1, len(manifest.audit.events) + 1)
    )
    assert [event.kind for event in manifest.audit.events] == [
        "agent_process",
        "command",
        "tool",
        "file_change",
        "tool",
        "evaluation",
    ]
    command = manifest.audit.events[1]
    assert command.command == "python calculate.py --input values.txt --output working.txt"
    assert command.result == {
        "status": "completed",
        "exit_code": 0,
        "output_character_count": 19,
    }
    assert command.source_lines == [1, 2]
    tool = manifest.audit.events[2]
    assert tool.tool is not None
    assert tool.tool.server == "qgis"
    assert tool.tool.name == "buffer"
    assert tool.tool.parameters == {"distance": 500, "units": "metres"}
    assert manifest.audit.events[4].tool is not None
    assert manifest.audit.events[4].tool.parameters == {
        "format": "GeoJSON",
        "overwrite": True,
    }

    artifacts = {artifact.artifact_id: artifact for artifact in manifest.audit.artifacts}
    assert artifacts["candidate"].exists_at_finish is True
    assert {link.target_id for link in artifacts["candidate"].lineage} >= {
        "runner-agent-process",
        "input-001",
        "codex:files-1",
    }
    intermediate = next(
        artifact for artifact in artifacts.values() if Path(artifact.path).name == "working.txt"
    )
    assert any(
        link.relationship == "derived_from"
        and link.target_id == "input-001"
        and link.evidence == "agent_reported"
        for link in intermediate.lineage
    )
    assert any(
        link.relationship == "produced_by" and link.target_id == "agent:translate-1"
        for link in intermediate.lineage
    )
    assert {artifact.role for artifact in manifest.audit.artifacts} >= {
        "task",
        "input",
        "intermediate",
        "candidate",
        "reference",
        "log",
    }
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["audit"]["events"][2]["tool"]["parameters"]["distance"] == 500
    assert payload["audit"]["artifacts"]


def test_runner_marks_missing_inner_trace_as_unavailable(tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: no-trace
title: No inner trace
category: scalar
prompt: Write 42.
output: {path: result.txt, kind: scalar}
""".strip(),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import os, pathlib; "
        "pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text('42\\n')",
        encoding="utf-8",
    )

    manifest, _ = run_task(
        task,
        reference,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}",
        tmp_path / "runs",
    )

    assert manifest.audit is not None
    assert manifest.audit.inner_trace_status == "unavailable"
    assert "This does not mean no tools ran" in manifest.audit.notes[-1]
