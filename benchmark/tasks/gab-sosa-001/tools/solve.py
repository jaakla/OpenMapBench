"""Reference solver for gab-sosa-001, built one way and verified a second, independent way.

Method A constructs the inset with a negative buffer (round joins), explodes the result to
single parts and drops parts smaller than 1 ha.

Method B never buffers. It lays a regular point grid over the county, evaluates the task's
own definition for every point -- inside the county and at least 3000 m from the county
boundary -- and compares that classification with membership in the polygons from method A.
Points closer than ``BAND_M`` to the 3000 m isoline are excluded, because a polygonal buffer
approximates the exact offset arc to within about 3.6 m at this radius and segmentation is
the one degree of freedom the contract tolerates. Any disagreement outside that band means
the constructed polygons are wrong, and the script then refuses to write a reference.

Usage as the reference builder:
    python solve.py ../reference/county_inset.gpkg
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

INSET_M = 3000.0
MIN_PART_AREA_M2 = 10_000.0
QUAD_SEGS = 16
GRID_STEP_M = 250.0
BAND_M = 5.0


def method_a(county: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    geometry = county.geometry.iloc[0].buffer(-INSET_M, quad_segs=QUAD_SEGS)
    parts = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    kept = [part for part in parts if not part.is_empty and part.area >= MIN_PART_AREA_M2]
    kept.sort(key=lambda part: (-part.area, part.bounds))
    return gpd.GeoDataFrame(
        {
            "orig_fid": [str(county["county_id"].iloc[0])] * len(kept),
            "inset_m": [int(INSET_M)] * len(kept),
        },
        geometry=kept,
        crs=county.crs,
    )


def method_b(county: gpd.GeoDataFrame, inset: gpd.GeoDataFrame) -> dict[str, int]:
    """Verify the inset against the task's definition on a grid, ignoring the arc band."""
    outline = county.geometry.iloc[0]
    minx, miny, maxx, maxy = outline.bounds
    xs = np.arange(minx, maxx + GRID_STEP_M, GRID_STEP_M)
    ys = np.arange(miny, maxy + GRID_STEP_M, GRID_STEP_M)
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = shapely.points(grid_x.ravel(), grid_y.ravel())
    inside_county = shapely.contains(outline, points)
    points = points[inside_county]
    distance = shapely.distance(points, outline.boundary)
    expected = distance >= INSET_M
    produced = shapely.contains(shapely.union_all(inset.geometry.values), points)
    undecided = np.abs(distance - INSET_M) < BAND_M
    disagreements = int(np.count_nonzero((expected != produced) & ~undecided))
    if disagreements:
        raise SystemExit(
            f"grid verification failed: {disagreements} of {len(points)} interior grid points "
            "are classified differently by the buffer and by the distance predicate"
        )
    return {
        "grid_points_in_county": len(points),
        "grid_points_in_inset": int(np.count_nonzero(produced)),
        "grid_points_in_band": int(np.count_nonzero(undecided)),
    }


def solve(task_dir: Path, output_path: Path) -> None:
    county = gpd.read_file(task_dir / "inputs" / "saare_maakond.geojson")
    inset = method_a(county)
    stats = method_b(county, inset)
    if not inset.geometry.is_valid.all():
        raise SystemExit("constructed inset contains an invalid geometry")
    if not inset.geometry.within(county.geometry.iloc[0]).all():
        raise SystemExit("a constructed part is not within the county")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    inset.to_file(output_path, layer="county_inset", driver="GPKG")
    print(
        f"wrote {output_path}: parts={len(inset)} "
        f"area_km2={inset.geometry.area.sum() / 1e6:.3f} "
        f"smallest_part_ha={inset.geometry.area.min() / 1e4:.1f} {stats}"
    )


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
