"""Reference solver for gab-rsia-002a, computed two independent ways and cross-checked.

Method A polygonizes the raster on its native EPSG:4326 grid with 8-connectivity, repairs
the rings that touch themselves diagonally, and reprojects the polygons to EPSG:3301.

Method B never vectorises anything. It labels 8-connected components with a union-find over
the pixel array, which gives the number of regions per class, and it computes the area of
every pixel as the shoelace area of its four reprojected corners, which gives the area per
class. Region counts must match exactly and class areas to within 1e-6 relative, otherwise
no reference is written. The two disagree only by the sagitta of the reprojected pixel
edges, which is a fraction of a millimetre at this latitude.

Usage as the reference builder:
    python solve.py ../reference/landcover_polygons.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import shapes
from shapely.geometry import shape
from shapely.validation import make_valid

TARGET_CRS = "EPSG:3301"
CONNECTIVITY = 8
AREA_AGREEMENT = 1e-6


def method_a(data: np.ndarray, transform, crs) -> gpd.GeoDataFrame:
    records = [
        {"raster_val": int(value), "geometry": make_valid(shape(geometry))}
        for geometry, value in shapes(data, connectivity=CONNECTIVITY, transform=transform)
    ]
    frame = gpd.GeoDataFrame(records, geometry="geometry", crs=crs).to_crs(TARGET_CRS)
    bounds = frame.geometry.bounds
    order = np.lexsort((bounds["miny"], bounds["minx"], frame["raster_val"]))
    return frame.iloc[order].reset_index(drop=True)


def _find(parent: np.ndarray, index: int) -> int:
    root = index
    while parent[root] != root:
        root = parent[root]
    while parent[index] != root:
        parent[index], index = root, parent[index]
    return root


def label_components(data: np.ndarray) -> Counter:
    """Count 8-connected components of equal value with a union-find over the pixel grid."""
    height, width = data.shape
    parent = np.arange(height * width, dtype=np.int64)
    index = np.arange(height * width, dtype=np.int64).reshape(height, width)
    neighbours = [
        (data[:, :-1] == data[:, 1:], index[:, :-1], index[:, 1:]),
        (data[:-1, :] == data[1:, :], index[:-1, :], index[1:, :]),
        (data[:-1, :-1] == data[1:, 1:], index[:-1, :-1], index[1:, 1:]),
        (data[:-1, 1:] == data[1:, :-1], index[:-1, 1:], index[1:, :-1]),
    ]
    for mask, left, right in neighbours:
        for a, b in zip(left[mask].tolist(), right[mask].tolist(), strict=True):
            root_a, root_b = _find(parent, a), _find(parent, b)
            if root_a != root_b:
                parent[max(root_a, root_b)] = min(root_a, root_b)
    flat = data.ravel()
    counts: Counter = Counter()
    seen: set[int] = set()
    for pixel in range(parent.size):
        root = _find(parent, pixel)
        if root not in seen:
            seen.add(root)
            counts[int(flat[root])] += 1
    return counts


def pixel_areas(data: np.ndarray, transform, crs) -> dict[int, float]:
    """Area per class from the reprojected corners of every pixel, with no vectorisation."""
    height, width = data.shape
    columns, rows = np.meshgrid(np.arange(width + 1), np.arange(height + 1))
    longitudes, latitudes = rasterio.transform.xy(transform, rows, columns, offset="ul")
    shape_2d = (height + 1, width + 1)
    transformer = Transformer.from_crs(crs, TARGET_CRS, always_xy=True)
    x, y = transformer.transform(
        np.asarray(longitudes).reshape(shape_2d), np.asarray(latitudes).reshape(shape_2d)
    )
    x0, y0 = x[:-1, :-1], y[:-1, :-1]
    x1, y1 = x[:-1, 1:], y[:-1, 1:]
    x2, y2 = x[1:, 1:], y[1:, 1:]
    x3, y3 = x[1:, :-1], y[1:, :-1]
    area = 0.5 * np.abs(
        (x0 * y1 - x1 * y0) + (x1 * y2 - x2 * y1) + (x2 * y3 - x3 * y2) + (x3 * y0 - x0 * y3)
    )
    totals: dict[int, float] = defaultdict(float)
    for value in np.unique(data):
        totals[int(value)] = float(area[data == value].sum())
    return totals


def solve(task_dir: Path, output_path: Path) -> None:
    with rasterio.open(task_dir / "inputs" / "landcover.tif") as source:
        data = source.read(1)
        transform, crs = source.transform, source.crs

    polygons = method_a(data, transform, crs)
    counts = Counter(polygons["raster_val"].astype(int))
    reference_counts = label_components(data)
    if counts != reference_counts:
        raise SystemExit(f"region counts disagree: polygonize {counts}, labelling {reference_counts}")

    areas = polygons.geometry.area.groupby(polygons["raster_val"].astype(int)).sum()
    expected = pixel_areas(data, transform, crs)
    for value, area in areas.items():
        error = abs(area - expected[int(value)]) / expected[int(value)]
        if error > AREA_AGREEMENT:
            raise SystemExit(f"class {value} area disagrees by {error:.3g} relative")

    if not polygons.geometry.is_valid.all():
        raise SystemExit("a polygon is invalid after repair")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    polygons.to_file(output_path, layer="landcover_polygons", driver="GPKG")
    multipart = int((polygons.geometry.geom_type == "MultiPolygon").sum())
    print(
        f"wrote {output_path}: {len(polygons)} regions in {len(counts)} classes, "
        f"{multipart} of them multipart because the region touches itself diagonally, "
        f"{polygons.geometry.area.sum() / 1e6:.4f} km2, both methods agree"
    )


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
