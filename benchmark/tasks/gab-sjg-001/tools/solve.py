"""Reference solver for gab-sjg-001, computed two independent ways and cross-checked.

Method A uses GeoPandas: reproject both layers to EPSG:3301 and count destinations with an
exact ``dwithin`` spatial join. Method B uses pyproj and NumPy only: transform coordinates,
compute every pairwise planar distance, and count with ``<=``. The script refuses to write a
result when the two methods disagree on any stop.

Usage as the reference builder:
    python solve.py ../reference/stops_with_counts.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer

MILE_M = 1609.344
TARGET_CRS = "EPSG:3301"
COUNT_FIELD = "dest_cnt_1mi"


def method_a(origins: gpd.GeoDataFrame, destinations: gpd.GeoDataFrame) -> np.ndarray:
    origins_m = origins.to_crs(TARGET_CRS)
    destinations_m = destinations.to_crs(TARGET_CRS)[["geometry"]]
    joined = gpd.sjoin(origins_m, destinations_m, how="left", predicate="dwithin", distance=MILE_M)
    counts = joined.groupby(level=0)["index_right"].count()
    return counts.reindex(origins_m.index, fill_value=0).to_numpy(dtype=int)


def method_b(origins: gpd.GeoDataFrame, destinations: gpd.GeoDataFrame) -> np.ndarray:
    transformer = Transformer.from_crs(origins.crs, TARGET_CRS, always_xy=True)
    ox, oy = transformer.transform(origins.geometry.x.to_numpy(), origins.geometry.y.to_numpy())
    dx, dy = transformer.transform(
        destinations.geometry.x.to_numpy(), destinations.geometry.y.to_numpy()
    )
    distances = np.hypot(ox[:, None] - dx[None, :], oy[:, None] - dy[None, :])
    return (distances <= MILE_M).sum(axis=1).astype(int)


def solve(task_dir: Path, output_path: Path) -> None:
    origins = gpd.read_file(task_dir / "inputs" / "origins.geojson")
    destinations = gpd.read_file(task_dir / "inputs" / "destinations.geojson")
    counts_a = method_a(origins, destinations)
    counts_b = method_b(origins, destinations)
    disagreements = np.flatnonzero(counts_a != counts_b)
    if disagreements.size:
        rows = origins.iloc[disagreements][["stop_id"]].assign(a=counts_a[disagreements])
        rows = rows.assign(b=counts_b[disagreements])
        raise SystemExit(f"methods disagree on {disagreements.size} stops:\n{rows}")
    result = origins.to_crs(TARGET_CRS)
    result[COUNT_FIELD] = counts_a
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(output_path, layer="stops_with_counts", driver="GPKG")
    print(
        f"wrote {output_path}: {len(result)} stops, counts {counts_a.min()}..{counts_a.max()}, "
        f"mean {counts_a.mean():.2f}, methods agree"
    )


if __name__ == "__main__":
    task_dir = Path(os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent)
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
