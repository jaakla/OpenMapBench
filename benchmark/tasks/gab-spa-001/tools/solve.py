"""Reference solver for gab-spa-001, computed two independent ways and cross-checked.

Method A works cell-first: a spatial join gives candidate park/cell pairs, each pair's
intersection area is measured, and distinct parks with a positive intersection are counted
per cell. Method B works park-first and uses no spatial join at all: for every park it walks
the cell indices its bounding box covers, tests each candidate cell directly, collects the
set of cells the park positively overlaps, and inverts that mapping into per-cell counts.
The script refuses to write a reference when the two disagree on any cell.

It also reports the smallest positive intersection area in the data, which is the number
that decides whether "positive intersection area" is a robust rule here, and how many cells
two plausible wrong routes get wrong.

Usage as the reference builder:
    python solve.py ../reference/park_grid.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box

XMIN, YMIN, XMAX, YMAX = 531000.0, 6579000.0, 553000.0, 6607000.0
CELL_M = 1000.0
NCOLS = round((XMAX - XMIN) / CELL_M)
NROWS = round((YMAX - YMIN) / CELL_M)
TARGET_CRS = "EPSG:3301"


def build_grid() -> gpd.GeoDataFrame:
    """One row per cell, grid_id = row * NCOLS + col, row 0 in the south, col 0 in the west."""
    records = []
    for row in range(NROWS):
        for col in range(NCOLS):
            records.append(
                {
                    "grid_id": row * NCOLS + col,
                    "geometry": box(
                        XMIN + col * CELL_M,
                        YMIN + row * CELL_M,
                        XMIN + (col + 1) * CELL_M,
                        YMIN + (row + 1) * CELL_M,
                    ),
                }
            )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=TARGET_CRS)


def method_a(parks: gpd.GeoDataFrame, grid: gpd.GeoDataFrame) -> tuple[np.ndarray, float]:
    pairs = gpd.sjoin(
        parks[["park_id", "geometry"]], grid[["grid_id", "geometry"]], predicate="intersects"
    )
    areas = []
    counts: dict[int, set[str]] = defaultdict(set)
    cells = dict(zip(grid["grid_id"], grid.geometry, strict=True))
    for park_id, grid_id, geometry in zip(
        pairs["park_id"], pairs["grid_id"], pairs.geometry, strict=True
    ):
        area = geometry.intersection(cells[int(grid_id)]).area
        if area > 0:
            counts[int(grid_id)].add(str(park_id))
            areas.append(area)
    values = np.array([len(counts.get(int(grid_id), ())) for grid_id in grid["grid_id"]], dtype=int)
    return values, min(areas)


def method_b(parks: gpd.GeoDataFrame, grid: gpd.GeoDataFrame) -> np.ndarray:
    counts: dict[int, set[str]] = defaultdict(set)
    for park_id, geometry in zip(parks["park_id"], parks.geometry, strict=True):
        minx, miny, maxx, maxy = geometry.bounds
        first_col = max(0, int(np.floor((minx - XMIN) / CELL_M)))
        last_col = min(NCOLS - 1, int(np.floor((maxx - XMIN) / CELL_M)))
        first_row = max(0, int(np.floor((miny - YMIN) / CELL_M)))
        last_row = min(NROWS - 1, int(np.floor((maxy - YMIN) / CELL_M)))
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                cell = box(
                    XMIN + col * CELL_M,
                    YMIN + row * CELL_M,
                    XMIN + (col + 1) * CELL_M,
                    YMIN + (row + 1) * CELL_M,
                )
                if geometry.intersection(cell).area > 0:
                    counts[row * NCOLS + col].add(str(park_id))
    return np.array(
        [len(counts.get(int(grid_id), ())) for grid_id in grid["grid_id"]], dtype=int
    )


def wrong_routes(parks: gpd.GeoDataFrame, grid: gpd.GeoDataFrame, truth: np.ndarray) -> str:
    """Two plausible substitutions: touching counts as overlap, and parts count as parks."""
    cells = dict(zip(grid["grid_id"], grid.geometry, strict=True))
    touching: dict[int, set[str]] = defaultdict(set)
    per_part: dict[int, int] = defaultdict(int)
    pairs = gpd.sjoin(
        parks[["park_id", "geometry"]], grid[["grid_id", "geometry"]], predicate="intersects"
    )
    for park_id, grid_id, geometry in zip(
        pairs["park_id"], pairs["grid_id"], pairs.geometry, strict=True
    ):
        touching[int(grid_id)].add(str(park_id))
    exploded = parks.explode(index_parts=False)[["park_id", "geometry"]]
    part_pairs = gpd.sjoin(exploded, grid[["grid_id", "geometry"]], predicate="intersects")
    for grid_id, geometry in zip(part_pairs["grid_id"], part_pairs.geometry, strict=True):
        if geometry.intersection(cells[int(grid_id)]).area > 0:
            per_part[int(grid_id)] += 1
    touching_counts = np.array(
        [len(touching.get(int(grid_id), ())) for grid_id in grid["grid_id"]], dtype=int
    )
    part_counts = np.array(
        [per_part.get(int(grid_id), 0) for grid_id in grid["grid_id"]], dtype=int
    )
    return (
        f"cells wrong when touching counts as overlap: {int((touching_counts != truth).sum())}; "
        f"cells wrong when parts are counted instead of parks: {int((part_counts != truth).sum())}"
    )


def solve(task_dir: Path, output_path: Path) -> None:
    parks = gpd.read_file(task_dir / "inputs" / "parks.geojson")
    grid = build_grid()
    counts_a, smallest_overlap = method_a(parks, grid)
    counts_b = method_b(parks, grid)
    disagreements = np.flatnonzero(counts_a != counts_b)
    if disagreements.size:
        raise SystemExit(f"methods disagree on {disagreements.size} cells: {disagreements[:20]}")
    grid["park_count"] = counts_a
    grid = grid[["grid_id", "park_count", "geometry"]]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    grid.to_file(output_path, layer="park_grid", driver="GPKG")
    print(
        f"wrote {output_path}: {len(grid)} cells, {int((counts_a > 0).sum())} with parks, "
        f"max {int(counts_a.max())} parks in a cell, methods agree; "
        f"smallest positive park-cell overlap {smallest_overlap:.2f} m2"
    )
    print(wrong_routes(parks, grid, counts_a))


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
