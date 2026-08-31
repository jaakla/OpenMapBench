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
        "content_capture:captured-files",
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
    assert any("This does not mean no tools ran" in note for note in manifest.audit.notes)


CAPTURE_SOLVER = '''
import json
import os
import time
from pathlib import Path

cwd = Path.cwd()
store = Path(os.environ["OPENMAPBENCH_RUN_DIR"]) / "captured-files"
patched = cwd / "render_heat.py"
untracked = cwd / ".tmp_make_map.py"
patched.write_text({patched_body}, encoding="utf-8")
untracked.write_text({untracked_body}, encoding="utf-8")


def emit(payload):
    print(json.dumps(payload), flush=True)


def wait_for(suffix):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any(entry.name.endswith(suffix) for entry in store.glob("*")):
            return True
        time.sleep(0.02)
    return False


emit(
    {{
        "type": "item.completed",
        "item": {{
            "id": "files-1",
            "type": "file_change",
            "changes": [{{"path": str(patched), "kind": "add"}}],
            "status": "completed",
        }},
    }}
)
emit(
    {{
        "type": "item.completed",
        "item": {{
            "id": "cmd-1",
            "type": "command_execution",
            "command": "/bin/bash -lc 'python3 .tmp_make_map.py && python3 render_heat.py'",
            "aggregated_output": "wrote the artifact",
            "exit_code": 0,
            "status": "completed",
        }},
    }}
)
assert wait_for("-render_heat.py"), "runner did not preserve render_heat.py in time"
assert wait_for("-.tmp_make_map.py"), "runner did not preserve .tmp_make_map.py in time"

Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"]).write_text("42\\n", encoding="utf-8")
patched.unlink()
untracked.unlink()
emit(
    {{
        "type": "item.completed",
        "item": {{
            "id": "files-2",
            "type": "file_change",
            "changes": [{{"path": str(patched), "kind": "delete"}}],
            "status": "completed",
        }},
    }}
)
'''

PATCHED_BODY = "# render_heat.py\nprint('interpolating urban heat')\n"
UNTRACKED_BODY = "# .tmp_make_map.py\nprint('drawing the map')\n"


def _capture_task(tmp_path: Path) -> tuple[Path, Path, Path]:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: capture-demo
title: Capture demo
category: scalar
prompt: Build the map with a throwaway script.
output: {path: result.txt, kind: scalar}
""".strip(),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text(
        CAPTURE_SOLVER.format(
            patched_body=repr(PATCHED_BODY),
            untracked_body=repr(UNTRACKED_BODY),
        ),
        encoding="utf-8",
    )
    return task, reference, solver


def test_runner_preserves_content_of_deleted_working_files(tmp_path: Path) -> None:
    task, reference, solver = _capture_task(tmp_path)

    manifest, manifest_path = run_task(
        task,
        reference,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}",
        tmp_path / "runs",
    )

    assert manifest.audit is not None
    run_dir = manifest_path.parent
    artifacts = {Path(item.path).name: item for item in manifest.audit.artifacts}

    for name, body in (
        ("render_heat.py", PATCHED_BODY),
        (".tmp_make_map.py", UNTRACKED_BODY),
    ):
        artifact = artifacts[name]
        assert artifact.exists_at_finish is False
        assert artifact.sha256 is None
        assert len(artifact.content_captures) == 1
        capture = artifact.content_captures[0]
        assert capture.encoding == "utf-8"
        assert capture.line_count == 2
        stored = run_dir / capture.stored_path
        assert stored.read_text(encoding="utf-8") == body
        assert capture.size_bytes == len(body.encode("utf-8"))

    assert any(
        link.relationship == "produced_by"
        and link.target_id == "codex:files-1"
        and link.evidence == "runner_content_capture"
        for link in artifacts["render_heat.py"].lineage
    )
    assert any(
        observation.reason == "command_reference" and observation.event_id == "codex:cmd-1"
        for capture in artifacts[".tmp_make_map.py"].content_captures
        for observation in capture.observations
    )

    store = manifest.audit.content_store
    assert store is not None
    assert store.path == "captured-files"
    assert store.file_count == 2
    assert store.version_count == 2
    assert store.skipped == []
    assert "content_capture:captured-files" in manifest.audit.capture_sources

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored_paths = {
        capture["stored_path"]
        for artifact in payload["audit"]["artifacts"]
        for capture in artifact["content_captures"]
    }
    assert stored_paths == {
        entry.relative_to(run_dir).as_posix() for entry in (run_dir / "captured-files").iterdir()
    }


def test_content_store_is_not_reported_as_a_run_artifact(tmp_path: Path) -> None:
    task, reference, solver = _capture_task(tmp_path)

    manifest, manifest_path = run_task(
        task,
        reference,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}",
        tmp_path / "runs",
    )

    assert manifest.audit is not None
    store_dir = manifest_path.parent / "captured-files"
    assert list(store_dir.iterdir())
    assert not [
        artifact
        for artifact in manifest.audit.artifacts
        if store_dir in Path(artifact.path).parents
    ]


def test_content_capture_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMAPBENCH_AUDIT_CAPTURE", "0")
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: capture-off
title: Capture off
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

    manifest, manifest_path = run_task(
        task,
        reference,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}",
        tmp_path / "runs",
    )

    assert manifest.audit is not None
    assert manifest.audit.content_store is None
    assert not (manifest_path.parent / "captured-files").exists()
    assert all(not artifact.content_captures for artifact in manifest.audit.artifacts)
