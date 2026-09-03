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


def test_value_check_names_the_fields_that_differ(tmp_path: Path) -> None:
    """A multi-field scalar artifact is one check, so the check itself carries the evidence."""
    candidate = tmp_path / "candidate.json"
    reference = tmp_path / "reference.json"
    candidate.write_text('{"steep_area_m2": 7546500.0, "region_count": 402}')
    reference.write_text('{"steep_area_m2": 7546500.0, "region_count": 289}')

    result = evaluate_scalar(
        candidate,
        reference,
        {"absolute_tolerance": 0, "relative_tolerance": 0.05},
    )

    assert not result.success
    check = result.checks[0]
    assert check.id == "value"
    assert check.passed is False
    assert check.details["mismatch_count"] == 1
    assert check.details["relative_tolerance"] == 0.05
    assert check.details["mismatches"] == [
        {"path": "$.region_count", "candidate": 402, "reference": 289}
    ]


def test_value_check_records_the_tolerances_it_applied_on_a_pass(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    reference = tmp_path / "reference.json"
    candidate.write_text('{"region_count": 289}')
    reference.write_text('{"region_count": 289}')

    result = evaluate_scalar(
        candidate,
        reference,
        {"absolute_tolerance": 0, "relative_tolerance": 0.05},
    )

    assert result.success
    assert result.checks[0].details == {
        "mismatch_count": 0,
        "absolute_tolerance": 0.0,
        "relative_tolerance": 0.05,
    }


def test_value_check_details_stay_bounded(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    reference = tmp_path / "reference.json"
    candidate.write_text(str({f"f{index}": index for index in range(30)}).replace("'", '"'))
    reference.write_text(str({f"f{index}": index + 1 for index in range(30)}).replace("'", '"'))

    result = evaluate_scalar(candidate, reference, {})

    details = result.checks[0].details
    assert details["mismatch_count"] == 30
    assert len(details["mismatches"]) == 10
    assert details["mismatches_truncated"] is True
    assert len(result.diagnostics["mismatches"]) == 30
