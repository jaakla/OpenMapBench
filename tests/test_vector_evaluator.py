from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from openmapbench.evaluator import evaluate_vector
from openmapbench.models import OutputSpec


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
