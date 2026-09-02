"""The native suite runner must run every scoreable task and report the rest as skipped."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from openmapbench.benchmark_batch import (
    discover_tasks,
    reference_solver_command,
    run_benchmark_batch,
)
from openmapbench.cli import app

SOLVER = """
import os
import sys
from pathlib import Path

mode = Path(os.environ["OPENMAPBENCH_TASK_DIR"]).name.rsplit("-", 1)[-1]
if mode == "crash":
    sys.stderr.write("solver could not open the input\\n")
    raise SystemExit(3)
if mode == "nofile":
    raise SystemExit(0)
Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"]).write_text(
    "6\\n" if mode == "pass" else "7\\n", encoding="utf-8"
)
""".strip()


def _task(
    task_root: Path,
    task_id: str,
    *,
    reference: str | None = "6\n",
    checksum: str | None = None,
    with_solver: bool = False,
) -> Path:
    directory = task_root / task_id
    (directory / "inputs").mkdir(parents=True)
    values = directory / "inputs" / "values.txt"
    values.write_text("1\n2\n3\n", encoding="utf-8")
    declared = checksum or hashlib.sha256(values.read_bytes()).hexdigest()
    task_file = directory / "task.yaml"
    task_file.write_text(
        yaml.safe_dump(
            {
                "schema_version": "0.1",
                "id": task_id,
                "title": f"Sum the values ({task_id})",
                "category": "scalar",
                "prompt": "Add the integers in inputs/values.txt and write the total.",
                "inputs": [
                    {
                        "path": "inputs/values.txt",
                        "role": "values",
                        "source": "synthetic fixture",
                        "as_of": "2026-09-02",
                        "license": "CC0-1.0",
                        "checksum": f"sha256:{declared}",
                    }
                ],
                "output": {"path": "result.txt", "kind": "scalar"},
                "evaluation": {
                    "strict": {"numeric_tolerance": {"absolute": 0, "relative": 0}}
                },
                "metadata": {"difficulty": "easy", "failure_modes": ["contract_literalism"]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if reference is not None:
        (directory / "reference").mkdir()
        (directory / "reference" / "result.txt").write_text(reference, encoding="utf-8")
    if with_solver:
        (directory / "tools").mkdir()
        (directory / "tools" / "solve.py").write_text(SOLVER, encoding="utf-8")
    return task_file


def _solver_command(tmp_path: Path) -> str:
    solver = tmp_path / "solve.py"
    solver.write_text(SOLVER, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"


def test_benchmark_batch_runs_every_task_and_writes_three_reports(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    for task_id in ("demo-pass", "demo-fail", "demo-crash", "demo-nofile"):
        _task(task_root, task_id)

    batch, batch_manifest = run_benchmark_batch(
        task_root,
        _solver_command(tmp_path),
        tmp_path / "runs",
        batch_id="suite-batch",
        agent={"name": "fixture-agent"},
    )

    batch_dir = batch_manifest.parent
    assert batch["task_count"] == 4
    assert batch["executed_count"] == 4
    assert batch["skipped_count"] == 0
    assert batch["completed_without_failures"] is False
    assert batch["status_counts"] == {
        "agent_error": 1,
        "failed": 1,
        "missing_output": 1,
        "passed": 1,
    }
    assert batch["strict_success_rate"] == 0.25
    assert batch["input_verification"] == "checksums verified"
    assert batch["visual_review"]["comparison_count"] == 0
    for name in ("report.json", "report.md", "report.html", "batch.json"):
        assert (batch_dir / name).is_file(), name
    assert batch["aggregate_report"]["html"] == str(batch_dir / "report.html")

    report = json.loads((batch_dir / "report.json").read_text(encoding="utf-8"))
    assert report["strict_success_rate"] == 0.25
    assert report["attempted_tasks"] == 4

    page = (batch_dir / "report.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert page.count('<article class="card"') == 4
    for task_id in ("demo-pass", "demo-fail", "demo-crash", "demo-nofile"):
        assert task_id in page
    assert "25.0%" in page
    assert 'data-status="agent_error"' in page
    assert "solver could not open the input" in page  # the failing agent's own stderr
    assert "Failed required checks" in page


def test_benchmark_batch_skips_tasks_it_cannot_score_fairly(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _task(task_root, "demo-pass")
    _task(task_root, "no-reference-pass", reference=None)
    _task(task_root, "bad-checksum-pass", checksum="0" * 64)

    batch, _ = run_benchmark_batch(
        task_root,
        _solver_command(tmp_path),
        tmp_path / "runs",
        batch_id="skip-batch",
        only_ids=["demo-pass", "no-reference-pass", "bad-checksum-pass", "not-a-task"],
    )

    reasons = {item["task_id"]: item["reason"] for item in batch["skipped"]}
    assert batch["executed_count"] == 1
    assert batch["skipped_count"] == 3
    assert "reference artifact missing" in reasons["no-reference-pass"]
    assert "checksum mismatch" in reasons["bad-checksum-pass"]
    assert "no task named not-a-task" in reasons["not-a-task"]
    assert batch["completed_without_failures"] is False

    page = (Path(batch["aggregate_report"]["html"])).read_text(encoding="utf-8")
    assert "reference artifact missing" in page
    assert "Not scored" in page


def test_input_verification_can_be_disabled(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _task(task_root, "bad-checksum-pass", checksum="0" * 64)

    tasks, skipped = discover_tasks(task_root, verify_inputs=False)

    assert [task.task_id for task in tasks] == ["bad-checksum-pass"]
    assert skipped == []


def test_skip_ids_are_recorded_rather_than_dropped(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _task(task_root, "demo-pass")
    _task(task_root, "other-pass")

    tasks, skipped = discover_tasks(task_root, skip_ids=["other-pass"])

    assert [task.task_id for task in tasks] == ["demo-pass"]
    assert skipped == [{"task_id": "other-pass", "reason": "skipped via --skip flag"}]


def test_reference_solver_mode_scores_each_task_with_its_own_solver(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _task(task_root, "bundled-pass", with_solver=True)

    batch, _ = run_benchmark_batch(
        task_root,
        reference_solver_command(),
        tmp_path / "runs",
        batch_id="reference-solver",
        agent={"name": "reference-solver"},
    )

    assert batch["status_counts"] == {"passed": 1}
    assert batch["completed_without_failures"] is True


def test_cli_run_suite_exits_nonzero_when_a_task_fails(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _task(task_root, "demo-pass")
    _task(task_root, "demo-fail")

    result = CliRunner().invoke(
        app,
        [
            "run-suite",
            str(task_root),
            "--agent-command",
            _solver_command(tmp_path),
            "--output-root",
            str(tmp_path / "runs"),
            "--batch-id",
            "cli-batch",
        ],
    )

    assert result.exit_code == 1, result.output
    assert (tmp_path / "runs" / "cli-batch" / "report.html").is_file()


def test_cli_run_suite_requires_an_agent(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _task(task_root, "demo-pass")

    result = CliRunner().invoke(app, ["run-suite", str(task_root)])

    assert result.exit_code != 0
    assert "--agent-command" in result.output


def test_repository_suite_script_has_runnable_help() -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    source_path = str(repository / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, str(repository / "scripts" / "run_benchmark_all.py"), "--help"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--reference-solver" in result.stdout
