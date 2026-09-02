"""The HTML report must present every run's evidence without inventing or leaking anything."""

import shlex
import sys
from pathlib import Path

from typer.testing import CliRunner

from openmapbench.cli import app
from openmapbench.html_report import write_html_report
from openmapbench.models import RunStatus
from openmapbench.runner import run_task

TASK = """
id: html-demo
title: Write the total <b>exactly</b>
category: scalar
prompt: |
  Write 42 to the artifact. Mind the <script>alert(1)</script> characters.
output:
  path: result.txt
  kind: scalar
metadata:
  difficulty: easy
  failure_modes: [contract_literalism]
""".strip()


def _run(tmp_path: Path, value: str) -> Path:
    task = tmp_path / "task.yaml"
    task.write_text(TASK, encoding="utf-8")
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import os, pathlib\n"
        f"pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text({value!r})\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"
    run_task(task, reference, command, tmp_path / "runs")
    return tmp_path / "runs"


def test_html_report_renders_summary_checks_and_audit(tmp_path: Path) -> None:
    run_root = _run(tmp_path, "42\n")
    output = tmp_path / "report.html"

    summary = write_html_report(run_root, output, title="Suite report")

    page = output.read_text(encoding="utf-8")
    assert summary["run_count"] == 1
    assert summary["invalid_manifest_count"] == 0
    assert "<title>Suite report</title>" in page
    assert '<article class="card" data-status="passed"' in page
    assert "100.0%" in page
    assert "Strict checks" in page
    assert '<details class="audit">' in page  # execution audit is embedded per run
    assert "contract_literalism" in page  # tagged failure mode reaches the breakdown
    assert "Diagnostics" in page


def test_html_report_escapes_task_text(tmp_path: Path) -> None:
    run_root = _run(tmp_path, "42\n")
    output = tmp_path / "report.html"

    write_html_report(run_root, output)

    page = output.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Write the total &lt;b&gt;exactly&lt;/b&gt;" in page


def test_html_report_lists_unreadable_manifests(tmp_path: Path) -> None:
    run_root = _run(tmp_path, "41\n")
    (run_root / "broken-run").mkdir()
    (run_root / "broken-run" / "manifest.json").write_text("{not json", encoding="utf-8")
    output = tmp_path / "report.html"

    summary = write_html_report(run_root, output)

    page = output.read_text(encoding="utf-8")
    assert summary["run_count"] == 1
    assert summary["invalid_manifest_count"] == 1
    assert "unreadable manifest" in page
    assert 'data-status="failed"' in page


def test_report_command_writes_html_when_the_suffix_asks_for_it(tmp_path: Path) -> None:
    run_root = _run(tmp_path, "42\n")
    output = tmp_path / "reports" / "report.html"

    result = CliRunner().invoke(
        app, ["report", str(run_root), "--output", str(output), "--title", "CLI report"]
    )

    assert result.exit_code == 0, result.output
    assert "<title>CLI report</title>" in output.read_text(encoding="utf-8")


def test_html_report_handles_an_empty_run_root(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    output = tmp_path / "report.html"

    summary = write_html_report(run_root, output)

    assert summary["run_count"] == 0
    assert "No run manifests were found" in output.read_text(encoding="utf-8")


def test_html_report_marks_a_run_that_never_reached_the_evaluator(tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(TASK, encoding="utf-8")
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text("import sys; sys.stderr.write('boom\\n'); raise SystemExit(2)\n", "utf-8")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"
    manifest, _ = run_task(task, reference, command, tmp_path / "runs")
    assert manifest.status == RunStatus.AGENT_ERROR

    output = tmp_path / "report.html"
    write_html_report(tmp_path / "runs", output)

    page = output.read_text(encoding="utf-8")
    assert 'data-status="agent_error"' in page
    assert "No evaluation was recorded" in page
    assert "boom" in page
