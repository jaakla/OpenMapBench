import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml
from PIL import Image

from openmapbench.gabench_batch import run_gabench_batch
from openmapbench.taskio import sha256_file


def _task(path: Path, task_id: str, output_path: str, output_kind: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "id": task_id,
                "title": f"Test {task_id}",
                "category": "batch-test",
                "prompt": "Create the requested artifact.",
                "output": {"path": output_path, "kind": output_kind},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_gabench_batch_runs_all_tasks_and_builds_reports(tmp_path: Path) -> None:
    imported = tmp_path / ".openmapbench" / "gabench"
    scalar_task = _task(
        imported / "tasks" / "gabench-001" / "task.yaml",
        "gabench-001",
        "answer.txt",
        "scalar",
    )
    image_task = _task(
        imported / "tasks" / "gabench-002" / "task.yaml",
        "gabench-002",
        "map.png",
        "file",
    )
    scalar_reference = tmp_path / "GABench" / "dataset" / "result" / "answer.txt"
    scalar_reference.parent.mkdir(parents=True)
    scalar_reference.write_text("42\n", encoding="utf-8")
    image_reference = scalar_reference.parent / "map.png"
    Image.new("RGB", (120, 80), "#f97316").save(image_reference)
    manifest_path = imported / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "adapter": "gabench",
                "schema_version": "0.1",
                "source_commit": "abc123",
                "upstream_license": "UNDECLARED",
                "tasks": [
                    {
                        "task_id": "gabench-001",
                        "task_path": str(scalar_task),
                        "reference_path": str(scalar_reference),
                        "reference_sha256": sha256_file(scalar_reference),
                    },
                    {
                        "task_id": "gabench-002",
                        "task_path": str(image_task),
                        "reference_path": str(image_reference),
                        "reference_sha256": sha256_file(image_reference),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    solver = tmp_path / "solver.py"
    solver.write_text(
        """
import os
from pathlib import Path
import sys
from PIL import Image

output = Path(os.environ["OPENMAPBENCH_OUTPUT_PATH"])
if output.suffix == ".png":
    Image.new("RGB", (100, 70), "#0ea5e9").save(output)
else:
    output.write_text("42\\n", encoding="utf-8")
sys.stderr.write("model: gpt-5.6-luna\\nreasoning effort: low\\ntokens used\\n1,000\\n")
""".strip(),
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"

    batch, batch_manifest = run_gabench_batch(
        manifest_path,
        command,
        tmp_path / "runs" / "gabench",
        batch_id="test-batch",
        agent={"name": "fixture-agent"},
        agent_cwd=tmp_path,
    )

    batch_dir = batch_manifest.parent
    assert batch["task_count"] == 2
    assert batch["executed_count"] == 2
    assert batch["skipped_count"] == 0
    assert batch["completed_without_failures"] is True
    assert batch["status_counts"] == {"needs_review": 1, "passed": 1}
    assert (batch_dir / "report.json").is_file()
    assert (batch_dir / "report.md").is_file()
    assert (batch_dir / "visual-review" / "index.html").is_file()
    assert batch["visual_review"]["comparison_count"] == 1
    report = json.loads((batch_dir / "report.json").read_text(encoding="utf-8"))
    assert report["strict_success_rate"] == 1.0
    assert report["needs_manual_review"] == 1
    assert report["usage"]["total_tokens"] == 2_000
    assert report["usage"]["by_model"]["gpt-5.6-luna"]["tokens_per_task"] == {
        "minimum": 1_000,
        "average": 1_000.0,
        "maximum": 1_000,
    }
    assert batch["usage"] == report["usage"]


def test_gabench_batch_reports_missing_references_and_continues(tmp_path: Path) -> None:
    imported = tmp_path / ".openmapbench" / "gabench"
    missing_task = _task(
        imported / "tasks" / "gabench-001" / "task.yaml",
        "gabench-001",
        "answer.txt",
        "scalar",
    )
    runnable_task = _task(
        imported / "tasks" / "gabench-002" / "task.yaml",
        "gabench-002",
        "answer.txt",
        "scalar",
    )
    reference = tmp_path / "answer.txt"
    reference.write_text("42\n", encoding="utf-8")
    manifest_path = imported / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "adapter": "gabench",
                "tasks": [
                    {
                        "task_id": "gabench-001",
                        "task_path": str(missing_task),
                        "reference_path": str(tmp_path / "missing.txt"),
                    },
                    {
                        "task_id": "gabench-002",
                        "task_path": str(runnable_task),
                        "reference_path": str(reference),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import os, pathlib; "
        "pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text('42\\n')",
        encoding="utf-8",
    )

    batch, _ = run_gabench_batch(
        manifest_path,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}",
        tmp_path / "runs",
        batch_id="missing-reference",
    )

    assert batch["executed_count"] == 1
    assert batch["skipped_count"] == 1
    assert batch["completed_without_failures"] is False
    assert batch["status_counts"] == {"passed": 1}
    assert "reference file missing" in batch["skipped"][0]["reason"]


def test_repository_batch_script_has_runnable_help() -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    source_path = str(repository / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, str(repository / "scripts" / "run_gabench_all.py"), "--help"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0
    assert ".openmapbench/gabench/manifest.json" in result.stdout
    assert "--agent-command" in result.stdout


def test_batch_parser_defaults_agent_cwd_to_per_run_workspace() -> None:
    from openmapbench.gabench_batch import build_parser

    args = build_parser().parse_args(["--agent-command", "true"])
    assert args.agent_cwd is None
