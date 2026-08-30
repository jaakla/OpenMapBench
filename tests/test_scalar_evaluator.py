from pathlib import Path

from openmapbench.evaluator import evaluate_scalar


def test_scalar_with_tolerance(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.txt"
    reference = tmp_path / "reference.txt"
    candidate.write_text("10.01")
    reference.write_text("10.00")

    result = evaluate_scalar(
        candidate,
        reference,
        {"absolute_tolerance": 0.02, "relative_tolerance": 0.0},
    )
    assert result.success


def test_json_metrics_use_numeric_tolerance(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    reference = tmp_path / "reference.json"
    candidate.write_text('{"auc": 0.901, "count": 10}')
    reference.write_text('{"count": 10, "auc": 0.9}')

    result = evaluate_scalar(
        candidate,
        reference,
        {"absolute_tolerance": 0.01, "relative_tolerance": 0.0},
    )
    assert result.success


def test_json_metric_predicate(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    reference = tmp_path / "reference.json"
    candidate.write_text('{"metrics": {"roc_auc": 0.91}}')
    reference.write_text('{"metrics": {"roc_auc": 0.95}}')

    result = evaluate_scalar(
        candidate,
        reference,
        {"json_checks": [{"path": "metrics.roc_auc", "operator": ">=", "value": 0.9}]},
    )
    assert result.success
