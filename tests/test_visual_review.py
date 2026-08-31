import csv
import json
import shlex
import sys
from pathlib import Path

import yaml
from PIL import Image

from openmapbench.models import RunStatus
from openmapbench.reporting import aggregate_manifests
from openmapbench.runner import run_task
from openmapbench.visual import visual_report_from_gabench, visual_report_from_runs


def _image(path: Path, color: str, size: tuple[int, int] = (160, 100)) -> None:
    Image.new("RGB", size, color).save(path)


def test_image_run_becomes_manual_side_by_side_review(tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: map-demo
title: Map demo
category: cartography
prompt: Create a map image with <all> values & labels.
output:
  path: map.png
  kind: file
""".strip(),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.png"
    _image(reference, "#f59e0b", (180, 120))
    solver = tmp_path / "solver.py"
    solver.write_text(
        """
import os
import json
from pathlib import Path
from PIL import Image

Image.new("RGB", (160, 100), "#2563eb").save(Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"]))
print(json.dumps({
    "type": "item.completed",
    "item": {
        "id": "render-1",
        "type": "mcp_tool_call",
        "server": "qgis",
        "tool": "render_map",
        "arguments": {"style": "blue", "width": 160},
        "status": "completed",
    },
}))
""".strip(),
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"

    manifest, _ = run_task(task, reference, command, tmp_path / "runs")

    assert manifest.status == RunStatus.NEEDS_REVIEW
    assert manifest.evaluation["success"] is None
    aggregate = aggregate_manifests(tmp_path / "runs")
    assert aggregate["needs_manual_review"] == 1
    assert aggregate["strictly_scored_tasks"] == 0
    assert aggregate["strict_success_rate"] is None

    report = visual_report_from_runs(tmp_path / "runs", tmp_path / "visual-review")
    assert report["comparison_count"] == 1
    comparison = tmp_path / "visual-review" / report["comparisons"][0]["comparison_image"]
    assert comparison.is_file()
    with Image.open(comparison) as composed:
        assert composed.width > 320
        assert composed.height > 120
    assert (tmp_path / "visual-review" / "index.html").is_file()
    html = (tmp_path / "visual-review" / "index.html").read_text(encoding="utf-8")
    assert "Task prompt" in html
    assert "Create a map image with &lt;all&gt; values &amp; labels." in html
    assert "Execution audit" in html
    assert "Layered timeline" in html
    assert "Artifact lineage" in html
    assert "Tool: render_map" in html
    assert "&quot;style&quot;: &quot;blue&quot;" in html
    assert "agent.stdout.log" in html
    assert report["comparisons"][0]["audit"]["inner_trace_status"] == "captured"
    assert report["comparisons"][0]["prompt"] == (
        "Create a map image with <all> values & labels."
    )
    assert "manual_result" in (tmp_path / "visual-review" / "review.csv").read_text()


def test_gabench_visual_report_matches_images_by_imported_output_name(tmp_path: Path) -> None:
    task_dir = tmp_path / "import" / "tasks" / "gabench-001"
    task_dir.mkdir(parents=True)
    task_path = task_dir / "task.yaml"
    task_path.write_text(
        yaml.safe_dump(
            {
                "id": "gabench-001",
                "title": "GABench 1: visual map",
                "category": "visualization",
                "prompt": "Create the map.\nLabel <Town> & river.",
                "output": {"path": "expected-map.png", "kind": "file"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    reference = tmp_path / "GABench" / "dataset" / "result" / "expected-map.png"
    reference.parent.mkdir(parents=True)
    _image(reference, "#f97316")
    candidate_root = tmp_path / "generated"
    candidate_root.mkdir()
    _image(candidate_root / "expected-map.png", "#0ea5e9")
    manifest_path = tmp_path / "import" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "adapter": "gabench",
                "tasks": [
                    {
                        "task_id": "gabench-001",
                        "task_path": str(task_path),
                        "reference_path": str(reference),
                        "output_kind": "file",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = visual_report_from_gabench(
        manifest_path,
        candidate_root,
        tmp_path / "gabench-review",
    )

    assert report["comparison_count"] == 1
    assert report["comparisons"][0]["task_id"] == "gabench-001"
    assert report["comparisons"][0]["manual_review_result"] == "pending"
    html = (tmp_path / "gabench-review" / "index.html").read_text(encoding="utf-8")
    assert "Generated images are on the left" in html
    assert "do not commit or redistribute" in html
    assert "Create the map.\nLabel &lt;Town&gt; &amp; river." in html
    assert report["comparisons"][0]["prompt"] == "Create the map.\nLabel <Town> & river."

    review_path = tmp_path / "gabench-review" / "review.csv"
    with review_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    rows[0]["manual_result"] = "pass"
    rows[0]["notes"] = "Checked map layout and legend."
    with review_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    rerun = visual_report_from_gabench(
        manifest_path,
        candidate_root,
        tmp_path / "gabench-review",
    )
    assert rerun["comparisons"][0]["manual_review_result"] == "pass"
    assert rerun["comparisons"][0]["notes"] == "Checked map layout and legend."
