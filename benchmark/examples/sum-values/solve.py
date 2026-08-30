"""Minimal deterministic command used to demonstrate the runner contract."""

import csv
import os
from pathlib import Path

task_dir = Path(os.environ["OPENMAPBENCH_TASK_DIR"])
output_path = Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"])
with (task_dir / "inputs" / "values.csv").open(newline="", encoding="utf-8") as handle:
    total = sum(float(row["value"]) for row in csv.DictReader(handle))
output_path.write_text(f"{total:g}\n", encoding="utf-8")
