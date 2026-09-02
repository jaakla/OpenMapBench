"""Reference solver for gab-spa-002, computed two independent ways and cross-checked.

Method A is the route the task describes: build the Voronoi partition of the 16 hospitals,
keep each hospital's cell, and intersect it with the hospital's 1000 m circle and with the
municipality boundary.

Method B never builds a Voronoi diagram. For each hospital it starts from the circle clipped
to the boundary and then clips it with one half-plane per competing hospital within 2000 m,
each half-plane being the side of the perpendicular bisector nearer to this hospital. That is
the definition of a Voronoi cell rather than an implementation of one, so the two routes
share no geometry code beyond the intersection operator. The script refuses to write a
reference unless every pair agrees to within a relative symmetric difference of 1e-9.

It also reports what the substitution reported in the source case costs: replacing the
partition with plain 1000 m circles clipped to the boundary.

Usage as the reference builder:
    python solve.py ../reference/hospital_cells.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import MultiPoint, Polygon, box

RADIUS_M = 1000.0
QUAD_SEGS = 16
HALF_PLANE_M = 20_000.0
TARGET_CRS = "EPSG:3301"
AGREEMENT = 1e-9


def method_a(hospitals: gpd.GeoDataFrame, outline) -> list:
    points = list(hospitals.geometry)
    envelope = box(*outline.buffer(5 * RADIUS_M).bounds)
    cells = list(shapely.voronoi_polygons(MultiPoint(points), extend_to=envelope).geoms)
    result = []
    for point in points:
        owning = [cell for cell in cells if cell.contains(point)]
        if len(owning) != 1:
            raise SystemExit("a hospital does not fall in exactly one Voronoi cell")
        circle = point.buffer(RADIUS_M, quad_segs=QUAD_SEGS)
        result.append(circle.intersection(owning[0]).intersection(outline))
    return result


def _half_plane(point, competitor) -> Polygon:
    """The side of the perpendicular bisector that is nearer to ``point``."""
    dx, dy = competitor.x - point.x, competitor.y - point.y
    length = float(np.hypot(dx, dy))
    dx, dy = dx / length, dy / length
    mid_x, mid_y = (point.x + competitor.x) / 2, (point.y + competitor.y) / 2
    normal_x, normal_y = -dy, dx
    corners = [
        (mid_x + HALF_PLANE_M * normal_x, mid_y + HALF_PLANE_M * normal_y),
        (mid_x - HALF_PLANE_M * normal_x, mid_y - HALF_PLANE_M * normal_y),
        (
            mid_x - HALF_PLANE_M * normal_x - HALF_PLANE_M * dx,
            mid_y - HALF_PLANE_M * normal_y - HALF_PLANE_M * dy,
        ),
        (
            mid_x + HALF_PLANE_M * normal_x - HALF_PLANE_M * dx,
            mid_y + HALF_PLANE_M * normal_y - HALF_PLANE_M * dy,
        ),
    ]
    return Polygon(corners)


def method_b(hospitals: gpd.GeoDataFrame, outline) -> list:
    points = list(hospitals.geometry)
    result = []
    for index, point in enumerate(points):
        geometry = point.buffer(RADIUS_M, quad_segs=QUAD_SEGS).intersection(outline)
        for other_index, competitor in enumerate(points):
            if other_index == index or point.distance(competitor) > 2 * RADIUS_M:
                continue
            geometry = geometry.intersection(_half_plane(point, competitor))
        result.append(geometry)
    return result


def plain_buffer_route(hospitals: gpd.GeoDataFrame, outline, reference: list) -> str:
    ious = []
    for point, cell in zip(hospitals.geometry, reference, strict=True):
        plain = point.buffer(RADIUS_M, quad_segs=QUAD_SEGS).intersection(outline)
        ious.append(plain.intersection(cell).area / plain.union(cell).area)
    return (
        f"plain circles clipped to the boundary instead of the partition: per-hospital IoU "
        f"from {min(ious):.3f} to {max(ious):.3f}, median {float(np.median(ious)):.3f}"
    )


def solve(task_dir: Path, output_path: Path) -> None:
    hospitals = gpd.read_file(task_dir / "inputs" / "hospitals.geojson")
    outline = gpd.read_file(task_dir / "inputs" / "boundary.geojson").geometry.iloc[0]
    cells_a = method_a(hospitals, outline)
    cells_b = method_b(hospitals, outline)
    for fid, first, second in zip(hospitals["hosp_fid"], cells_a, cells_b, strict=True):
        disagreement = first.symmetric_difference(second).area / first.area
        if disagreement > AGREEMENT:
            raise SystemExit(f"methods disagree on {fid}: relative difference {disagreement:.3g}")
    result = gpd.GeoDataFrame(
        {
            "hosp_fid": hospitals["hosp_fid"].astype(str),
            "name": hospitals["name"],
            "buffer_rad": [int(RADIUS_M)] * len(cells_a),
            "area_m2": [round(cell.area, 2) for cell in cells_a],
        },
        geometry=cells_a,
        crs=TARGET_CRS,
    )
    if not result.geometry.is_valid.all() or result.geometry.is_empty.any():
        raise SystemExit("constructed cells contain an invalid or empty geometry")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    result.to_file(output_path, layer="hospital_cells", driver="GPKG")
    print(
        f"wrote {output_path}: {len(result)} cells, areas "
        f"{result['area_m2'].min() / 1e4:.1f}..{result['area_m2'].max() / 1e4:.1f} ha, "
        "methods agree"
    )
    print(plain_buffer_route(hospitals, outline, cells_a))


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
