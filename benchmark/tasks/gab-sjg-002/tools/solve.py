"""Reference solver for gab-sjg-002, with a second construction used only as a control.

The task has no unique answer: any point strictly inside its municipality satisfies the
contract. So the two methods here are not cross-checked against each other, they are both
checked against the contract's own predicates.

Method A reprojects to EPSG:3301 and takes a guaranteed-interior representative point; it
becomes the reference artifact. Method B searches the largest part of each municipality on a
grid for the cell centre farthest from the boundary, a pole-of-inaccessibility style answer
that is structurally different from method A and usually a long way from it. Both must pass
every predicate in the contract, which is how this script demonstrates that the contract
scores the property asked for rather than one particular correct answer.

The script also reports how many municipalities a naive "interior point first, reproject
afterwards" route would still place inside the polygon, and how far its coordinates are from
the projected ones, which is the failure the task is built around.

Usage as the reference builder:
    python solve.py ../reference/municipality_points.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import MultiPolygon

TARGET_CRS = "EPSG:3301"
COORDINATE_DECIMALS = 2
GRID_CELLS = 64


def _frame(municipalities: gpd.GeoDataFrame, points: list) -> gpd.GeoDataFrame:
    xs = [round(point.x, COORDINATE_DECIMALS) for point in points]
    ys = [round(point.y, COORDINATE_DECIMALS) for point in points]
    return gpd.GeoDataFrame(
        {
            "poly_fid": municipalities["poly_fid"].astype(str).to_list(),
            "name": municipalities["name"].astype(str).to_list(),
            "x_3301": xs,
            "y_3301": ys,
        },
        geometry=shapely.points(xs, ys),
        crs=TARGET_CRS,
    )


def method_a(municipalities: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject first, then take a representative interior point."""
    projected = municipalities.to_crs(TARGET_CRS)
    return _frame(projected, list(projected.geometry.representative_point()))


def method_b(municipalities: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """A structurally different answer: the grid cell centre farthest from the boundary."""
    projected = municipalities.to_crs(TARGET_CRS)
    points = []
    for geometry in projected.geometry:
        parts = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
        part = max(parts, key=lambda candidate: candidate.area)
        minx, miny, maxx, maxy = part.bounds
        xs = np.linspace(minx, maxx, GRID_CELLS + 2)[1:-1]
        ys = np.linspace(miny, maxy, GRID_CELLS + 2)[1:-1]
        grid_x, grid_y = np.meshgrid(xs, ys)
        candidates = shapely.points(grid_x.ravel(), grid_y.ravel())
        inside = candidates[shapely.contains(part, candidates)]
        if len(inside) == 0:
            points.append(part.representative_point())
            continue
        distances = shapely.distance(inside, part.boundary)
        points.append(inside[int(np.argmax(distances))])
    return _frame(projected, points)


def check_contract(result: gpd.GeoDataFrame, municipalities: gpd.GeoDataFrame, label: str) -> None:
    projected = municipalities.to_crs(TARGET_CRS)
    targets = dict(zip(projected["poly_fid"].astype(str), projected.geometry, strict=True))
    if len(result) != len(projected) or set(result["poly_fid"]) != set(targets):
        raise SystemExit(f"{label}: one point per municipality is required")
    for fid, point, x_value, y_value in zip(
        result["poly_fid"], result.geometry, result["x_3301"], result["y_3301"], strict=True
    ):
        if not point.within(targets[fid]):
            raise SystemExit(f"{label}: point for {fid} is not strictly inside its municipality")
        if abs(point.x - x_value) > 0.01 or abs(point.y - y_value) > 0.01:
            raise SystemExit(f"{label}: coordinate fields for {fid} disagree with the geometry")


def report_naive_route(municipalities: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> str:
    """Interior point taken in EPSG:4326 first, reprojected afterwards, degrees written out."""
    naive = municipalities.geometry.representative_point()
    projected = gpd.GeoSeries(naive, crs=municipalities.crs).to_crs(TARGET_CRS)
    outside = int((~projected.within(municipalities.to_crs(TARGET_CRS).geometry)).sum())
    separation = projected.distance(reference.geometry, align=False)
    degrees_written = float(np.abs(naive.x - reference["x_3301"]).min())
    return (
        f"naive route: {outside}/{len(naive)} points fall outside their municipality once "
        f"reprojected, median distance from the reference point {separation.median():.0f} m, "
        f"smallest gap between a degree coordinate and the required x_3301 "
        f"{degrees_written:.0f} m"
    )


def solve(task_dir: Path, output_path: Path) -> None:
    municipalities = gpd.read_file(task_dir / "inputs" / "municipalities.geojson")
    reference = method_a(municipalities)
    control = method_b(municipalities)
    check_contract(reference, municipalities, "method A")
    check_contract(control, municipalities, "method B")
    separation = control.geometry.distance(reference.geometry, align=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    reference.to_file(output_path, layer="municipality_points", driver="GPKG")
    print(
        f"wrote {output_path}: {len(reference)} points, both constructions satisfy the "
        f"contract, they differ by {separation.median():.0f} m on the median municipality "
        f"and up to {separation.max():.0f} m"
    )
    print(report_naive_route(municipalities, reference))


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
