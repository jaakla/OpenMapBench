from __future__ import annotations

from pathlib import Path

import yaml

from .models import TaskSpec


def load_task(path: str | Path) -> TaskSpec:
    task_path = Path(path)
    payload = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    return TaskSpec.model_validate(payload)
