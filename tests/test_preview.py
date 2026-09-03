"""Vector and raster previews illustrate a verdict; they must never influence one."""

import shlex
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from shapely.geometry import Point, box

from openmapbench.html_report import write_html_report
from openmapbench.preview import PreviewUnavailable, is_previewable, render_comparison
from openmapbench.runner import run_task
from openmapbench.visual import visual_report_from_runs


def _grid(path: Path, *, cells: int, offset: float = 0.0, crs: str = "EPSG:3301") -> Path:
    gpd.GeoDataFrame(
        {"cell_id": list(range(cells))},
        geometry=[
            box(index * 10 + offset, 0, index * 10 + 10 + offset, 10) for index in range(cells)
        ],
        crs=crs,
    ).to_file(path)
    return path


def _raster(path: Path, *, value: float, crs: str = "EPSG:3301", size: int = 16) -> Path:
    data = np.full((size, size), value, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(0, size, 1, 1),
        nodata=-9999.0,
    ) as sink:
        sink.write(data, 1)
    return path


def test_vector_sheet_has_three_panels_and_reports_both_layers(tmp_path: Path) -> None:
    candidate = _grid(tmp_path / "candidate.gpkg", cells=4, offset=2.0)
    reference = _grid(tmp_path / "reference.gpkg", cells=6)
    sheet = tmp_path / "sheet.png"

    metadata = render_comparison(
        candidate, reference, sheet, kind="vector", title="grid comparison"
    )

    assert metadata["artifact_kind"] == "vector"
    assert metadata["candidate"] == {"features": 4, "crs": "EPSG:3301"}
    assert metadata["reference"] == {"features": 6, "crs": "EPSG:3301"}
    with Image.open(sheet) as image:
        # three panels side by side, so the sheet is much wider than it is tall
        assert image.width > image.height * 2
        assert (image.width, image.height) == (
            metadata["composition"]["width"],
            metadata["composition"]["height"],
        )


def test_vector_sheet_draws_both_layers_in_one_extent(tmp_path: Path) -> None:
    """A candidate in the wrong CRS must not be silently framed as if it matched."""
    candidate = tmp_path / "candidate.geojson"
    gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(26.7, 58.3)], crs="EPSG:4326"
    ).to_file(candidate)
    reference = tmp_path / "reference.geojson"
    gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(658000, 6473000)], crs="EPSG:3301"
    ).to_file(reference)

    metadata = render_comparison(
        candidate, reference, tmp_path / "sheet.png", kind="vector", title="crs mismatch"
    )

    assert metadata["candidate"]["crs"] == "EPSG:4326"
    assert metadata["reference"]["crs"] == "EPSG:3301"
    assert len(metadata["extent"]) == 4


def test_unreadable_artifact_is_reported_not_raised(tmp_path: Path) -> None:
    broken = tmp_path / "broken.gpkg"
    broken.write_text("not a geopackage", encoding="utf-8")
    reference = _grid(tmp_path / "reference.gpkg", cells=2)

    with pytest.raises(PreviewUnavailable):
        render_comparison(
            broken, reference, tmp_path / "sheet.png", kind="vector", title="broken"
        )


def test_raster_sheet_diffs_matching_grids_and_declines_mismatched_ones(tmp_path: Path) -> None:
    candidate = _raster(tmp_path / "candidate.tif", value=3.0)
    reference = _raster(tmp_path / "reference.tif", value=1.0)

    metadata = render_comparison(
        candidate, reference, tmp_path / "same.png", kind="raster", title="same grid"
    )
    assert metadata["pixel_difference_computed"] is True

    other = _raster(tmp_path / "other.tif", value=1.0, crs="EPSG:4326", size=8)
    metadata = render_comparison(
        candidate, other, tmp_path / "different.png", kind="raster", title="other grid"
    )
    assert metadata["pixel_difference_computed"] is False


def test_only_spatial_artifacts_are_previewable() -> None:
    assert is_previewable(Path("a.gpkg"), "vector")
    assert is_previewable(Path("a.tif"), "raster")
    assert not is_previewable(Path("a.csv"), "table")
    assert not is_previewable(Path("a.json"), "scalar")
    assert not is_previewable(Path("a.csv"), "vector")


def _vector_run(tmp_path: Path, *, cells: int) -> Path:
    task = tmp_path / "task.yaml"
    task.write_text(
        "id: preview-demo\n"
        "title: Write the grid\n"
        "category: vector\n"
        "prompt: Write the grid cells to grid.gpkg.\n"
        "output:\n"
        "  path: grid.gpkg\n"
        "  kind: vector\n"
        "  crs: EPSG:3301\n"
        "evaluation:\n"
        "  strict:\n"
        "    crs: exact\n"
        "    feature_count: exact\n",
        encoding="utf-8",
    )
    reference = _grid(tmp_path / "reference.gpkg", cells=6)
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import os\n"
        "import geopandas as gpd\n"
        "from shapely.geometry import box\n"
        f"cells = {cells}\n"
        "gpd.GeoDataFrame({'cell_id': list(range(cells))},\n"
        "    geometry=[box(i * 10, 0, i * 10 + 10, 10) for i in range(cells)],\n"
        "    crs='EPSG:3301').to_file(os.environ['OPENMAPBENCH_OUTPUT_PATH'])\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"
    run_task(task, reference, command, tmp_path / "runs")
    return tmp_path / "runs"


def test_scored_vector_run_gets_a_sheet_but_owes_no_manual_decision(tmp_path: Path) -> None:
    run_root = _vector_run(tmp_path, cells=6)
    review_dir = tmp_path / "visual-review"

    report = visual_report_from_runs(run_root, review_dir)

    assert report["comparison_count"] == 1
    comparison = report["comparisons"][0]
    assert comparison["artifact_kind"] == "vector"
    assert comparison["manual_review_required"] is False
    assert comparison["manual_review_result"].startswith("not required")
    assert (review_dir / comparison["comparison_image"]).is_file()
    # review.csv is the list of decisions a human still owes, so a scored run stays out of it
    rows = (review_dir / "review.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1


def test_report_card_labels_a_preview_as_unscored(tmp_path: Path) -> None:
    run_root = _vector_run(tmp_path, cells=4)
    review_dir = tmp_path / "visual-review"
    visual_report_from_runs(run_root, review_dir)
    output = tmp_path / "report.html"

    summary = write_html_report(run_root, output, visual_review_dir=review_dir)

    page = output.read_text(encoding="utf-8")
    assert summary["visual_comparison_count"] == 1
    assert 'data-status="failed"' in page  # 4 cells against a 6 cell reference
    assert "Artifact preview — rendered for review, not scored" in page
    assert "comes from the strict checks, not from this image" in page


def test_table_and_scalar_runs_produce_no_sheet(tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        "id: table-demo\n"
        "title: Write the totals\n"
        "category: table\n"
        "prompt: Write totals.csv.\n"
        "output:\n"
        "  path: totals.csv\n"
        "  kind: table\n",
        encoding="utf-8",
    )
    reference = tmp_path / "reference.csv"
    reference.write_text("id,total\n1,6\n", encoding="utf-8")
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import os, pathlib\n"
        "pathlib.Path(os.environ['OPENMAPBENCH_OUTPUT_PATH']).write_text('id,total\\n1,6\\n')\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(solver))}"
    run_task(task, reference, command, tmp_path / "runs")

    report = visual_report_from_runs(tmp_path / "runs", tmp_path / "visual-review")

    assert report["comparison_count"] == 0
    assert report["skipped"] == []
