"""Every native benchmark task must validate, carry provenance, and score its own reference."""

from pathlib import Path

import pytest

from openmapbench.evaluator import evaluate
from openmapbench.taskio import load_task, validate_task_files

TASK_ROOT = Path(__file__).resolve().parent.parent / "benchmark" / "tasks"
TASK_FILES = sorted(TASK_ROOT.glob("*/task.yaml"))


@pytest.mark.parametrize("task_file", TASK_FILES, ids=[path.parent.name for path in TASK_FILES])
def test_native_task_is_complete_and_self_consistent(task_file: Path) -> None:
    task = load_task(task_file)
    assert task.id == task_file.parent.name

    findings = validate_task_files(task, task_file)
    assert findings, "task declares no inputs"
    assert all(finding["status"] == "passed" for finding in findings), findings
    for item in task.inputs:
        assert item.source and item.license and item.as_of and item.checksum, item

    assert task.metadata.get("failure_modes"), "task must tag the failure modes it exercises"
    assert task.metadata.get("tolerance_rationale"), "task must justify its tolerances"

    references = sorted((task_file.parent / "reference").glob(Path(task.output.path).name))
    assert len(references) == 1, "task must ship exactly one reference artifact"
    result = evaluate(task, references[0], references[0], task_file=task_file)
    assert result.success, [check for check in result.checks if not check.passed]
