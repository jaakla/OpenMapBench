import json
import shlex
import sys
from pathlib import Path

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
