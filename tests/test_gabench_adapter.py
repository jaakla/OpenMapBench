import csv
import json
from pathlib import Path

import pytest
import yaml

from openmapbench.adapters.gabench import import_gabench


def test_gabench_import_creates_local_references_without_copying_data(tmp_path: Path) -> None:
    source = tmp_path / "GABench"
    (source / "benchmark").mkdir(parents=True)
    (source / "dataset" / "result").mkdir(parents=True)
    input_path = source / "dataset" / "values.csv"
    input_path.write_text("id,value\na,1\n", encoding="utf-8")
    reference = source / "dataset" / "result" / "answer.csv"
    reference.write_text("id,value\na,1\n", encoding="utf-8")
    metrics_reference = source / "dataset" / "result" / "model_performance.json"
    metrics_reference.write_text('{"mse": 5000}\n', encoding="utf-8")
    csv_path = source / "benchmark" / "benchmark.csv"
    fieldnames = [
        "ID",
        "Domain",
        "Task Description",
        "Data Description",
        "Drawing Style",
        "Toolchain Length",
        "Toolchain JSON",
        "Result",
        "Layers",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "ID": "1",
                "Domain": "Tabular GIS",
                "Task Description": "Summarize the supplied data.",
                "Data Description": "dataset/values.csv contains the values.",
                "Drawing Style": "",
                "Toolchain Length": "1",
                "Toolchain JSON": "[]",
                "Result": "answer.csv",
                "Layers": "",
            }
        )
        writer.writerow(
            {
                "ID": "2",
                "Domain": "GeoAI",
                "Task Description": "Train and evaluate a model.",
                "Data Description": "dataset/values.csv contains the values.",
                "Drawing Style": "",
                "Toolchain Length": "2",
                "Toolchain JSON": "[]",
                "Result": "CHECK:JSON_VALUE:model_performance.json:mse:<6000",
                "Layers": "",
            }
        )

    output = tmp_path / ".openmapbench" / "gabench"
    manifest = import_gabench(source, output)

    assert manifest["source_task_count"] == 2
    assert manifest["deterministic_supported_count"] == 2
    assert manifest["upstream_license"] == "UNDECLARED"
    task_payload = yaml.safe_load(
        (output / "tasks" / "gabench-001" / "task.yaml").read_text(encoding="utf-8")
    )
    assert task_payload["inputs"][0]["path"] == str(input_path.resolve())
    assert task_payload["inputs"][0]["checksum"].startswith("sha256:")
    assert not (output / "values.csv").exists()
    metrics_task = yaml.safe_load(
        (output / "tasks" / "gabench-002" / "task.yaml").read_text(encoding="utf-8")
    )
    assert metrics_task["output"]["path"] == "model_performance.json"
    assert metrics_task["evaluation"]["strict"]["json_checks"] == [
        {"path": "mse", "operator": "<", "value": 6000}
    ]
    disk_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert disk_manifest["tasks"][0]["reference_path"] == str(reference.resolve())


def test_gabench_import_rejects_unresolved_lfs_csv(tmp_path: Path) -> None:
    source = tmp_path / "GABench"
    (source / "benchmark").mkdir(parents=True)
    (source / "benchmark" / "benchmark.csv").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Git LFS pointer"):
        import_gabench(source, tmp_path / "output")
