"""A run that reached the answer must be reported as contaminated, never counted."""

import shlex
import sys
from pathlib import Path

import yaml

from openmapbench.html_report import write_html_report
from openmapbench.models import RunStatus
from openmapbench.reporting import aggregate_manifests
from openmapbench.runner import run_task

TASK = """
id: leak-demo
title: Sum the values
category: scalar
prompt: Add the integers in inputs/values.txt and write the total.
inputs:
  - path: inputs/values.txt
    role: values
output:
  path: result.txt
  kind: scalar
"""


def _task_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A native-style task: contract, declared input, plus the reference and its solver."""
    directory = tmp_path / "tasks" / "leak-demo"
    (directory / "inputs").mkdir(parents=True)
    (directory / "inputs" / "values.txt").write_text("1\n2\n3\n", encoding="utf-8")
    (directory / "inputs" / "README.md").write_text(
        "A wrong threshold changes 12 of the 40 rows.\n", encoding="utf-8"
    )
    (directory / "task.yaml").write_text(TASK.strip(), encoding="utf-8")
    (directory / "reference").mkdir()
    reference = directory / "reference" / "result.txt"
    reference.write_text("6\n", encoding="utf-8")
    (directory / "tools").mkdir()
    (directory / "tools" / "solve.py").write_text("print('the answer is 6')\n", encoding="utf-8")
    return directory / "task.yaml", reference


def _solver(tmp_path: Path, body: str) -> str:
    solver = tmp_path / "solver.py"
    solver.write_text(body, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"


HONEST = """
import os
from pathlib import Path

task_dir = Path(os.environ["OPENMAPBENCH_TASK_DIR"])
total = sum(int(line) for line in (task_dir / "inputs" / "values.txt").read_text().split())
Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"]).write_text(f"{total}\\n")
"""

CHEAT = """
import json
import os
import sys
from pathlib import Path

reference = Path(sys.argv[1])
Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"]).write_text(reference.read_text())
print(json.dumps({
    "type": "item.completed",
    "item": {
        "id": "cmd-1",
        "type": "command_execution",
        "command": f"cat {reference}",
        "status": "completed",
    },
}))
"""


def test_staging_withholds_the_reference_and_the_solver(tmp_path: Path) -> None:
    task_file, reference = _task_tree(tmp_path)

    manifest, manifest_path = run_task(
        task_file, reference, _solver(tmp_path, HONEST), tmp_path / "runs"
    )

    assert manifest.status == RunStatus.PASSED
    assert manifest.isolation is not None
    assert manifest.isolation.mode == "staged"
    staged_dir = manifest_path.parent / "task"
    # the contract and the declared input are there; the answer key is not
    assert (staged_dir / "task.yaml").is_file()
    assert (staged_dir / "inputs" / "values.txt").read_text(encoding="utf-8") == "1\n2\n3\n"
    assert not (staged_dir / "reference").exists()
    assert not (staged_dir / "tools").exists()
    assert not (staged_dir / "inputs" / "README.md").exists()
    # the staged contract still declares the same input path
    staged = yaml.safe_load((staged_dir / "task.yaml").read_text(encoding="utf-8"))
    assert staged["inputs"][0]["path"] == "inputs/values.txt"
    assert manifest.integrity is not None
    assert manifest.integrity.contaminated is False


def test_reading_the_reference_marks_the_run_contaminated(tmp_path: Path) -> None:
    task_file, reference = _task_tree(tmp_path)
    command = f"{_solver(tmp_path, CHEAT)} {shlex.quote(str(reference))}"

    manifest, _ = run_task(task_file, reference, command, tmp_path / "runs")

    # the artifact matches, so the evaluator passes it — admissibility is a separate question
    assert manifest.status == RunStatus.PASSED
    assert manifest.integrity is not None
    assert manifest.integrity.contaminated is True
    assert manifest.integrity.findings
    assert str(reference) in manifest.integrity.findings[0].path
    assert "cat " in manifest.integrity.findings[0].detail


def test_contaminated_runs_leave_the_strict_rate(tmp_path: Path) -> None:
    task_file, reference = _task_tree(tmp_path)
    run_root = tmp_path / "runs"
    run_task(task_file, reference, _solver(tmp_path, HONEST), run_root)
    run_task(
        task_file,
        reference,
        f"{_solver(tmp_path, CHEAT)} {shlex.quote(str(reference))}",
        run_root,
    )

    report = aggregate_manifests(run_root)

    assert report["attempted_tasks"] == 2
    assert report["contaminated_runs"] == 1
    assert report["contaminated_task_ids"] == ["leak-demo"]
    # one honest pass out of one admissible run, not two out of two
    assert report["strictly_scored_tasks"] == 1
    assert report["strict_successes"] == 1
    assert report["strict_success_rate"] == 1.0
    assert [run["contaminated"] for run in report["runs"]].count(True) == 1


def test_report_names_the_contamination(tmp_path: Path) -> None:
    task_file, reference = _task_tree(tmp_path)
    run_root = tmp_path / "runs"
    run_task(
        task_file,
        reference,
        f"{_solver(tmp_path, CHEAT)} {shlex.quote(str(reference))}",
        run_root,
    )
    output = tmp_path / "report.html"

    write_html_report(run_root, output)

    page = output.read_text(encoding="utf-8")
    assert "Contaminated — excluded from the score" in page
    assert "contaminated-pill" in page
    assert str(reference) in page


def test_direct_mode_still_flags_the_solver_and_reference(tmp_path: Path) -> None:
    """Without staging the task directory is reachable, but its answers are still out of bounds."""
    task_file, reference = _task_tree(tmp_path)
    solver = task_file.parent / "tools" / "solve.py"
    command = f"{_solver(tmp_path, CHEAT)} {shlex.quote(str(solver))}"

    manifest, _ = run_task(
        task_file, reference, command, tmp_path / "runs", isolate_task=False
    )

    assert manifest.isolation is not None
    assert manifest.isolation.mode == "direct"
    assert manifest.integrity is not None
    assert manifest.integrity.contaminated is True
    assert "tools" in manifest.integrity.findings[0].path


def test_the_harness_own_command_is_not_contamination(tmp_path: Path) -> None:
    """The runner names the task and reference paths itself; that is not the agent looking."""
    task_file, reference = _task_tree(tmp_path)

    manifest, _ = run_task(
        task_file, reference, _solver(tmp_path, HONEST), tmp_path / "runs", isolate_task=False
    )

    assert manifest.status == RunStatus.PASSED
    assert manifest.integrity is not None
    assert manifest.integrity.contaminated is False
