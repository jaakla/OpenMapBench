"""Reference solver for gab-tmha-002, computed two independent ways and cross-checked.

Method A resamples the DEM onto the task's 10 m EPSG:3301 grid with GDAL's warper. Method B
resamples nothing through GDAL: it transforms every target cell centre to EPSG:4326 and
interpolates the four surrounding DEM cell centres directly with NumPy. Both then take
Horn's slope in percent, threshold, group 8-connected cells and drop regions under 0.5 ha.

The two do not agree exactly, and the point of running both is to measure by how much: the
warper does not reduce to a textbook bilinear interpolation at the cell centre, and on this
DEM it moves elevations by up to 0.9 m, which moves the 20 % isoline. The measured
disagreement is what the task's tolerance is set from, and the script refuses to write a
reference if it grows past ``MAX_DISAGREEMENT``.

Usage as the reference builder:
    python solve.py ../reference/steep_slopes.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from pyproj import Transformer
from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape
from shapely.validation import make_valid

TARGET_CRS = "EPSG:3301"
ORIGIN_X, ORIGIN_Y = 670000.0, 6407500.0
NCOLS, NROWS, CELL_M = 650, 800, 10.0
SLOPE_THRESHOLD_PCT = 20.0
MIN_REGION_M2 = 5000.0
CONNECTIVITY = 8
MAX_DISAGREEMENT = 0.05


def target_transform():
    return from_origin(ORIGIN_X, ORIGIN_Y, CELL_M, CELL_M)


def warp_elevation(dem: np.ndarray, transform, crs) -> np.ndarray:
    """Method A: GDAL's bilinear warper onto the task grid."""
    destination = np.zeros((NROWS, NCOLS), dtype="float64")
    reproject(
        dem,
        destination,
        src_transform=transform,
        src_crs=crs,
        dst_transform=target_transform(),
        dst_crs=TARGET_CRS,
        resampling=Resampling.bilinear,
    )
    return destination


def interpolate_elevation(dem: np.ndarray, transform, crs) -> np.ndarray:
    """Method B: bilinear interpolation at each target cell centre, computed directly."""
    to_source = Transformer.from_crs(TARGET_CRS, crs, always_xy=True)
    x, y = np.meshgrid(
        ORIGIN_X + (np.arange(NCOLS) + 0.5) * CELL_M,
        ORIGIN_Y - (np.arange(NROWS) + 0.5) * CELL_M,
    )
    longitude, latitude = to_source.transform(x, y)
    column = (longitude - transform.c) / transform.a - 0.5
    row = (latitude - transform.f) / transform.e - 0.5
    column_floor = np.floor(column).astype(int)
    row_floor = np.floor(row).astype(int)
    weight_x = column - column_floor
    weight_y = row - row_floor
    if (
        row_floor.min() < 0
        or column_floor.min() < 0
        or row_floor.max() + 1 >= dem.shape[0]
        or column_floor.max() + 1 >= dem.shape[1]
    ):
        raise SystemExit("the analysis grid reaches outside the DEM window")
    return (
        dem[row_floor, column_floor] * (1 - weight_x) * (1 - weight_y)
        + dem[row_floor, column_floor + 1] * weight_x * (1 - weight_y)
        + dem[row_floor + 1, column_floor] * (1 - weight_x) * weight_y
        + dem[row_floor + 1, column_floor + 1] * weight_x * weight_y
    )


def horn_slope_pct(elevation: np.ndarray) -> np.ndarray:
    """Horn's 3x3 slope in percent; the outermost row and column stay at zero."""
    a, b, c = elevation[:-2, :-2], elevation[:-2, 1:-1], elevation[:-2, 2:]
    d, f = elevation[1:-1, :-2], elevation[1:-1, 2:]
    g, h, i = elevation[2:, :-2], elevation[2:, 1:-1], elevation[2:, 2:]
    dz_dx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * CELL_M)
    dz_dy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * CELL_M)
    slope = np.zeros_like(elevation)
    slope[1:-1, 1:-1] = 100 * np.hypot(dz_dx, dz_dy)
    return slope


def steep_regions(slope: np.ndarray) -> gpd.GeoDataFrame:
    mask = slope >= SLOPE_THRESHOLD_PCT
    records = []
    for geometry, _ in shapes(
        mask.astype("uint8"),
        mask=mask,
        connectivity=CONNECTIVITY,
        transform=target_transform(),
    ):
        polygon = shape(geometry)
        if polygon.area >= MIN_REGION_M2:
            # 8-connected regions can touch themselves diagonally, which is only valid
            # geometry as a multipolygon; the repair preserves the area exactly.
            records.append(make_valid(polygon))
    frame = gpd.GeoDataFrame(geometry=records, crs=TARGET_CRS)
    frame["area_m2"] = frame.geometry.area.round(2)
    frame["slope_pct_max"] = [
        round(float(slope[_cells(polygon)].max()), 4) for polygon in frame.geometry
    ]
    bounds = frame.geometry.bounds
    order = np.lexsort((bounds["miny"], bounds["minx"]))
    return frame.iloc[order].reset_index(drop=True)[["slope_pct_max", "area_m2", "geometry"]]


def _cells(polygon) -> tuple[np.ndarray, np.ndarray]:
    """Row and column indices of the grid cells whose centre lies inside a region."""
    minx, miny, maxx, maxy = polygon.bounds
    first_col = max(0, int((minx - ORIGIN_X) // CELL_M))
    last_col = min(NCOLS - 1, int((maxx - ORIGIN_X) // CELL_M))
    first_row = max(0, int((ORIGIN_Y - maxy) // CELL_M))
    last_row = min(NROWS - 1, int((ORIGIN_Y - miny) // CELL_M))
    columns, rows = np.meshgrid(
        np.arange(first_col, last_col + 1), np.arange(first_row, last_row + 1)
    )
    x = ORIGIN_X + (columns + 0.5) * CELL_M
    y = ORIGIN_Y - (rows + 0.5) * CELL_M
    inside = shapely.contains_xy(polygon, x, y)
    return rows[inside], columns[inside]


def solve(task_dir: Path, output_path: Path) -> None:
    with rasterio.open(task_dir / "inputs" / "dem.tif") as source:
        dem = source.read(1).astype("float64")
        transform, crs = source.transform, source.crs

    regions_a = steep_regions(horn_slope_pct(warp_elevation(dem, transform, crs)))
    regions_b = steep_regions(horn_slope_pct(interpolate_elevation(dem, transform, crs)))
    union_a = shapely.union_all(regions_a.geometry.values)
    union_b = shapely.union_all(regions_b.geometry.values)
    disagreement = union_a.symmetric_difference(union_b).area / union_a.area
    if disagreement > MAX_DISAGREEMENT:
        raise SystemExit(
            f"the two resampling routes disagree by {disagreement:.4f}, more than the "
            f"{MAX_DISAGREEMENT} this task's tolerance was set for"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    regions_a.to_file(output_path, layer="steep_slopes", driver="GPKG")
    print(
        f"wrote {output_path}: {len(regions_a)} regions, {union_a.area / 1e4:.1f} ha, "
        f"steepest {regions_a['slope_pct_max'].max():.1f} %; the direct bilinear route gives "
        f"{len(regions_b)} regions and {union_b.area / 1e4:.1f} ha, a symmetric difference of "
        f"{disagreement:.4f}"
    )


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
