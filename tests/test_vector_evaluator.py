from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from openmapbench.evaluator import evaluate, evaluate_vector
from openmapbench.models import OutputSpec
from openmapbench.taskio import load_task


def test_vector_partitioning_does_not_change_polygon_result(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.geojson"
    reference = tmp_path / "reference.geojson"
    gpd.GeoDataFrame(
        {"name": ["left", "right"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:3857",
    ).to_file(candidate)
    gpd.GeoDataFrame(
        {"name": ["whole"]},
        geometry=[box(0, 0, 2, 1)],
        crs="EPSG:3857",
    ).to_file(reference)

    result = evaluate_vector(
        candidate,
        reference,
        {
            "crs": "exact",
            "feature_count": "ignore",
            "geometry": {"metric": "symmetric_difference_ratio", "tolerance": 0},
        },
        OutputSpec(path="result.geojson", kind="vector", geometry_type="MultiPolygon"),
    )
    assert result.success
    assert result.diagnostics["geometry_value"] == 0


def _write(path: Path, data: dict, geometries: list, crs: str = "EPSG:3857") -> Path:
    gpd.GeoDataFrame(data, geometry=geometries, crs=crs).to_file(path)
    return path


def test_entity_matching_detects_wrong_partition_that_union_metric_cannot(tmp_path: Path) -> None:
    reference = _write(
        tmp_path / "reference.geojson",
        {"hosp": ["a", "b"]},
        [box(0, 0, 1, 1), box(1, 0, 2, 1)],
    )
    wrong_partition = _write(
        tmp_path / "wrong.geojson",
        {"hosp": ["a", "b"]},
        [box(0, 0, 1.5, 1), box(1.5, 0, 2, 1)],
    )
    config = {
        "geometry": {"metric": "symmetric_difference_ratio", "tolerance": 0.001, "match": "entity"},
        "attributes": {"key": "hosp", "columns": ["hosp"]},
    }
    union_config = {**config, "geometry": {**config["geometry"], "match": "union"}}

    assert evaluate_vector(wrong_partition, reference, union_config).success
    result = evaluate_vector(wrong_partition, reference, config)
    assert not result.success
    entities = result.diagnostics["entities"]
    assert [item["key"] for item in entities["failed"]] == ["a", "b"]
    assert entities["entity_f1"] == 0.0
    assert result.score == 0.0


def test_entity_matching_unions_parts_of_one_entity_and_flags_missing_and_extra(
    tmp_path: Path,
) -> None:
    reference = _write(
        tmp_path / "reference.geojson",
        {"hosp": ["a", "b"]},
        [box(0, 0, 1, 1), box(1, 0, 2, 1)],
    )
    split_entity = _write(
        tmp_path / "split.geojson",
        {"hosp": ["a", "a", "b"]},
        [box(0, 0, 0.5, 1), box(0.5, 0, 1, 1), box(1, 0, 2, 1)],
    )
    config = {"geometry": {"metric": "iou", "tolerance": 0.99, "match": "entity", "key": "hosp"}}
    result = evaluate_vector(split_entity, reference, config)
    assert result.success
    assert result.diagnostics["entities"]["passed_entities"] == 2

    renamed = _write(
        tmp_path / "renamed.geojson",
        {"hosp": ["a", "c"]},
        [box(0, 0, 1, 1), box(1, 0, 2, 1)],
    )
    result = evaluate_vector(renamed, reference, config)
    assert not result.success
    assert result.diagnostics["entities"]["missing"] == ["b"]
    assert result.diagnostics["entities"]["extra"] == ["c"]
    assert result.diagnostics["entities"]["entity_f1"] == 0.5


def test_vector_checks_score_interior_points_without_a_fixed_reference(tmp_path: Path) -> None:
    polygons = _write(
        tmp_path / "polygons.geojson",
        {"fid": [1, 2]},
        [box(0, 0, 2, 2), box(5, 5, 7, 7)],
    )
    reference = _write(
        tmp_path / "reference.geojson",
        {"poly_fid": [1, 2], "x": [0.5, 5.5], "y": [0.5, 5.5]},
        [Point(0.5, 0.5), Point(5.5, 5.5)],
    )
    candidate = _write(
        tmp_path / "candidate.geojson",
        {"poly_fid": [1, 2], "x": [1.0, 6.0], "y": [1.0, 6.0]},
        [Point(1, 1), Point(6, 6)],
    )
    config = {
        "geometry": {"metric": "ignore"},
        "vector_checks": [
            {"type": "within", "input_role": "polygons", "key": "poly_fid", "input_key": "fid"},
            {"type": "count_per_input", "input_role": "polygons", "key": "poly_fid", "input_key": "fid"},
            {"type": "field_equals_geometry", "x": "x", "y": "y", "tolerance": 0.01},
            {"type": "unique", "field": "poly_fid"},
            {"type": "field_range", "field": "x", "min": 0, "max": 10},
        ],
    }
    result = evaluate_vector(candidate, reference, config, inputs={"polygons": polygons})
    assert result.success, [check for check in result.checks if not check.passed]
    assert {check.id for check in result.checks} >= {
        "vector_checks.0.within",
        "vector_checks.1.count_per_input",
        "vector_checks.2.field_equals_geometry",
        "vector_checks.3.unique",
        "vector_checks.4.field_range",
    }

    outside = _write(
        tmp_path / "outside.geojson",
        {"poly_fid": [1, 1], "x": [1.0, 6.0], "y": [1.0, 6.0]},
        [Point(3, 3), Point(6, 6)],
    )
    result = evaluate_vector(outside, reference, config, inputs={"polygons": polygons})
    failed = {check.id: check.details for check in result.checks if not check.passed}
    assert set(failed) == {
        "vector_checks.0.within",
        "vector_checks.1.count_per_input",
        "vector_checks.2.field_equals_geometry",
        "vector_checks.3.unique",
    }
    assert failed["vector_checks.0.within"]["failures"][0]["reason"] == "not within"
    assert failed["vector_checks.1.count_per_input"]["wrong_count"] == [
        {"key": "1", "count": 2, "expected": 1},
        {"key": "2", "count": 0, "expected": 1},
    ]


def test_vector_checks_resolve_input_roles_through_the_task(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    _write(inputs / "zones.geojson", {"zone": ["z1"]}, [box(0, 0, 10, 10)])
    (tmp_path / "task.yaml").write_text(
        """
id: vector-checks-demo
title: Vector checks demo
category: vector
prompt: Produce one polygon per zone with its area.
inputs:
  - path: inputs/zones.geojson
    role: zones
output:
  path: result.geojson
  kind: vector
evaluation:
  strict:
    crs: ignore
    geometry: {metric: ignore}
    vector_checks:
      - {type: covered_by, input_role: zones, key: zone}
      - {type: field_equals_measure, field: area_m2, measure: area, relative: 0.001}
""".strip(),
        encoding="utf-8",
    )
    reference = _write(tmp_path / "reference.geojson", {"zone": ["z1"]}, [box(0, 0, 10, 10)])
    candidate = _write(
        tmp_path / "candidate.geojson",
        {"zone": ["z1"], "area_m2": [25.0]},
        [box(0, 0, 5, 5)],
    )
    task = load_task(tmp_path / "task.yaml")

    result = evaluate(task, candidate, reference, task_file=tmp_path / "task.yaml")
    assert result.success

    wrong_area = _write(
        tmp_path / "wrong.geojson",
        {"zone": ["z1"], "area_m2": [26.0]},
        [box(0, 0, 5, 5)],
    )
    result = evaluate(task, wrong_area, reference, task_file=tmp_path / "task.yaml")
    assert not result.success
    with pytest.raises(ValueError, match="input role"):
        evaluate(task, candidate, reference)


def test_vector_attribute_nulls_equal_nulls_but_not_zero(tmp_path: Path) -> None:
    reference = _write(
        tmp_path / "reference.geojson",
        {"id": [1, 2], "ndvi": [0.5, None]},
        [Point(0, 0), Point(1, 1)],
    )
    same_nulls = _write(
        tmp_path / "same.geojson",
        {"id": [1, 2], "ndvi": [0.5, None]},
        [Point(0, 0), Point(1, 1)],
    )
    zero_for_null = _write(
        tmp_path / "zero.geojson",
        {"id": [1, 2], "ndvi": [0.5, 0.0]},
        [Point(0, 0), Point(1, 1)],
    )
    config = {
        "geometry": {"metric": "hausdorff_distance", "tolerance": 0.0},
        "attributes": {"key": "id", "columns": ["id", "ndvi"]},
    }
    assert evaluate_vector(same_nulls, reference, config).success
    result = evaluate_vector(zero_for_null, reference, config)
    assert not result.success
    assert result.diagnostics["attributes"]["mismatches"][0]["reference"] is None
