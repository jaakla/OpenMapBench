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
