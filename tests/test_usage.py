import json
import shlex
import sys
from pathlib import Path

import pytest

from openmapbench.pricing import estimate_cost
from openmapbench.reporting import aggregate_manifests, report_markdown
from openmapbench.runner import run_task
from openmapbench.usage import backfill_usage, parse_agent_usage


def _task_fixture(tmp_path: Path) -> tuple[Path, Path]:
    task = tmp_path / "task.yaml"
    task.write_text(
        """
id: usage-demo
title: Usage demo
category: scalar
prompt: Write 42.
output:
  path: result.txt
  kind: scalar
""".strip(),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.txt"
    reference.write_text("42\n", encoding="utf-8")
    return task, reference


def _solver(tmp_path: Path, total_tokens: int) -> str:
    solver = tmp_path / f"solver-{total_tokens}.py"
    solver.write_text(
        (
            "import os, pathlib, sys; "
            "pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text('42\\n'); "
            f"sys.stderr.write('model: gpt-5.6-luna\\nreasoning effort: low\\n"
            f"tokens used\\n{total_tokens:,}\\n')"
        ),
        encoding="utf-8",
    )
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"


def test_parse_codex_stderr_total_and_estimate_range() -> None:
    usage = parse_agent_usage(
        "",
        "model: gpt-5.6-luna\nreasoning effort: low\n...\ntokens used\n30,269\n",
    )

    assert usage is not None
    assert usage.source == "codex_stderr_summary"
    assert usage.model == "gpt-5.6-luna"
    assert usage.reasoning_effort == "low"
    assert usage.total_tokens == 30_269
    cost = estimate_cost(usage)
    assert cost is not None
    assert cost.basis == "total_tokens_range"
    assert cost.estimated_cost_usd is None
    assert cost.minimum_cost_usd == pytest.approx(0.00060538)
    assert cost.maximum_cost_usd == pytest.approx(0.0363228)


def test_parse_codex_jsonl_breakdown_and_exact_estimate() -> None:
    stdout = json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10_000,
                "input_tokens_details": {
                    "cached_tokens": 6_000,
                    "cache_write_tokens": 1_000,
                },
                "output_tokens": 2_000,
                "output_tokens_details": {"reasoning_tokens": 1_200},
                "total_tokens": 12_000,
            },
        }
    )
    usage = parse_agent_usage(stdout, "", declared_model="gpt-5.6-luna")

    assert usage is not None
    assert usage.source == "codex_jsonl"
    assert usage.input_tokens == 10_000
    assert usage.cached_input_tokens == 6_000
    assert usage.cache_write_input_tokens == 1_000
    assert usage.output_tokens == 2_000
    assert usage.reasoning_output_tokens == 1_200
    cost = estimate_cost(usage)
    assert cost is not None
    assert cost.basis == "token_breakdown"
    assert cost.estimated_cost_usd == pytest.approx(0.00337)
    assert cost.minimum_cost_usd == cost.maximum_cost_usd


def test_runner_records_usage_and_report_statistics(tmp_path: Path) -> None:
    task, reference = _task_fixture(tmp_path)
    run_root = tmp_path / "runs"
    first, first_path = run_task(task, reference, _solver(tmp_path, 100), run_root)
    second, _ = run_task(task, reference, _solver(tmp_path, 300), run_root)

    assert first.schema_version == "0.2"
    assert first.token_usage is not None
    assert first.token_usage.total_tokens == 100
    assert first.cost_estimate is not None
    assert first.cost_estimate.basis == "total_tokens_range"
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["token_usage"]["model"] == "gpt-5.6-luna"
    assert second.token_usage is not None

    report = aggregate_manifests(run_root)
    usage = report["usage"]
    assert usage["runs_with_usage"] == 2
    assert usage["total_tokens"] == 400
    assert usage["tokens_per_task"] == {"minimum": 100, "average": 200.0, "maximum": 300}
    assert usage["by_model"]["gpt-5.6-luna"]["runs"] == 2
    markdown = report_markdown(report)
    assert "100 / 200.0 / 300" in markdown
    assert "gpt-5.6-luna" in markdown


def test_backfill_usage_updates_old_manifest_from_logs(tmp_path: Path) -> None:
    task, reference = _task_fixture(tmp_path)
    run_root = tmp_path / "runs"
    _, manifest_path = run_task(task, reference, _solver(tmp_path, 1_234), run_root)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "0.1"
    payload.pop("token_usage")
    payload.pop("cost_estimate")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = backfill_usage(run_root)

    assert summary["updated"] == 1
    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated["schema_version"] == "0.2"
    assert updated["token_usage"]["total_tokens"] == 1_234
    assert updated["cost_estimate"]["basis"] == "total_tokens_range"
    assert backfill_usage(run_root)["already_complete"] == 1
