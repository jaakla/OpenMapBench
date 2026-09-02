import json
import shlex
import sys
from pathlib import Path

from typer.testing import CliRunner

from openmapbench.cli import app
from openmapbench.models import RunStatus
from openmapbench.reporting import aggregate_manifests
from openmapbench.runner import run_task


def test_runner_writes_manifest_and_report(tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: runner-demo
title: Runner demo
category: scalar
prompt: Write 42.
output:
  path: result.txt
  kind: scalar
""".strip(),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import os, pathlib; pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text('42\\n')",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"

    manifest, manifest_path = run_task(task, reference, command, tmp_path / "runs")

    assert manifest.status == RunStatus.PASSED
    assert manifest_path.is_file()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["candidate"]["sha256"]
    assert payload["evaluation"]["success"] is True
    report = aggregate_manifests(tmp_path / "runs")
    assert report["attempted_tasks"] == 1
    assert report["strict_success_rate"] == 1.0

    solver.write_text(
        "import os, pathlib; pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text('41\\n')",
        encoding="utf-8",
    )
    failed_manifest, _ = run_task(task, reference, command, tmp_path / "runs")
    assert failed_manifest.status == RunStatus.FAILED
    report = aggregate_manifests(tmp_path / "runs")
    assert report["attempted_tasks"] == 2
    assert report["strict_successes"] == 1
    assert report["strict_success_rate"] == 0.5


def test_runner_defaults_agent_cwd_to_run_workspace(tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: workspace-demo
title: Workspace demo
category: scalar
prompt: Write 42.
output:
  path: result.txt
  kind: scalar
""".strip(),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import os, pathlib\n"
        "pathlib.Path('scratch.py').write_text('helper')\n"
        "assert pathlib.Path.cwd() == pathlib.Path(os.environ['OPENMAPBENCH_WORKSPACE_DIR'])\n"
        "pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text('42\\n')\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))} {{workspace_dir}}"

    manifest, manifest_path = run_task(task, reference, command, tmp_path / "runs")

    assert manifest.status == RunStatus.PASSED
    run_dir = manifest_path.parent
    workspace = run_dir / "workspace"
    assert (workspace / "scratch.py").read_text(encoding="utf-8") == "helper"
    assert not (tmp_path / "scratch.py").exists()
    assert manifest.command[-1] == str(workspace)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_event = payload["audit"]["events"][0]
    assert first_event["parameters"]["cwd"] == str(workspace)
    inventoried = {artifact["path"] for artifact in payload["audit"]["artifacts"]}
    assert str(workspace / "scratch.py") in inventoried


def test_run_cli_verbose_short_flag_streams_readable_progress(tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: verbose-demo
title: Verbose demo
category: scalar
prompt: Write 42.
output:
  path: result.txt
  kind: scalar
""".strip(),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "verbose_solver.py"
    solver.write_text(
        """
import json
import os
import sys
from pathlib import Path

Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"]).write_text("42\\n", encoding="utf-8")
print(json.dumps({
    "type": "item.completed",
    "item": {
        "id": "buffer-1",
        "type": "mcp_tool_call",
        "server": "qgis",
        "tool": "buffer",
        "arguments": {"distance": 500},
        "status": "completed",
    },
}))
sys.stderr.write("loading source layer\\n")
""".strip(),
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"
    run_root = tmp_path / "runs"

    result = CliRunner().invoke(
        app,
        [
            "run",
            str(task),
            "--reference",
            str(reference),
            "--agent-command",
            command,
            "--run-root",
            str(run_root),
            "-v",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[openmapbench] task verbose-demo: Verbose demo" in result.output
    assert '[agent] tool: qgis/buffer {"distance": 500}' in result.output
    assert "[agent:stderr] loading source layer" in result.output
    assert "[openmapbench] evaluation status: passed" in result.output
    manifest_path = next(run_root.rglob("manifest.json"))
    stdout_log = manifest_path.parent / "agent.stdout.log"
    assert '"type": "item.completed"' in stdout_log.read_text(encoding="utf-8")
