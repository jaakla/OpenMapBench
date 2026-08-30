import pytest
from pydantic import ValidationError

from openmapbench.models import TaskSpec


def test_minimal_task_parses() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "demo-1",
            "title": "Demo",
            "category": "scalar",
            "prompt": "Return the answer.",
            "output": {"path": "result.txt", "kind": "scalar"},
        }
    )
    assert task.id == "demo-1"
    assert task.output.kind.value == "scalar"


def test_output_path_must_stay_inside_run_directory() -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "id": "unsafe",
                "title": "Unsafe",
                "category": "scalar",
                "prompt": "No-op",
                "output": {"path": "../result.txt", "kind": "scalar"},
            }
        )
