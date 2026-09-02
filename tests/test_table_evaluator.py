from pathlib import Path

from openmapbench.evaluator import evaluate_table


def test_table_is_keyed_order_independent_and_tolerance_aware(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.csv"
    reference = tmp_path / "reference.csv"
    candidate.write_text("id,value\nb,2.01\na,1.0\n", encoding="utf-8")
    reference.write_text("id,value\na,1\nb,2\n", encoding="utf-8")

    result = evaluate_table(
        candidate,
        reference,
        {
            "key": "id",
            "numeric_tolerance": {"value": {"absolute": 0.02, "relative": 0}},
        },
    )
    assert result.success


def test_table_rejects_duplicate_entity_keys(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.csv"
    reference = tmp_path / "reference.csv"
    candidate.write_text("id,value\na,1\na,1\n", encoding="utf-8")
    reference.write_text("id,value\na,1\nb,1\n", encoding="utf-8")

    result = evaluate_table(candidate, reference, {"key": "id"})
    assert not result.success
    assert result.diagnostics["duplicate_keys"] == ["a"]


def test_table_nulls_equal_nulls_but_never_values(tmp_path: Path) -> None:
    reference = tmp_path / "reference.csv"
    reference.write_text("id,value\na,\nb,\nc,1\n", encoding="utf-8")
    config = {"key": "id"}

    same = tmp_path / "same.csv"
    same.write_text("id,value\na,\nb,NULL\nc,1\n", encoding="utf-8")
    assert evaluate_table(same, reference, config).success

    zero = tmp_path / "zero.csv"
    zero.write_text("id,value\na,0\nb,\nc,1\n", encoding="utf-8")
    result = evaluate_table(zero, reference, config)
    assert not result.success
    assert result.diagnostics["mismatches"] == [
        {"row": 0, "key": "a", "column": "value", "candidate": "0", "reference": None}
    ]

    empty_for_value = tmp_path / "empty.csv"
    empty_for_value.write_text("id,value\na,\nb,\nc,\n", encoding="utf-8")
    assert not evaluate_table(empty_for_value, reference, config).success
