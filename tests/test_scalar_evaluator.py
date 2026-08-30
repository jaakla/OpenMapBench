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
