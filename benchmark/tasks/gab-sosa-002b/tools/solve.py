"""Reference solver for gab-sosa-002b, computed two independent ways and cross-checked.

The inputs and the clipping are the same as gab-sosa-002a; only the artifact differs. Method
A repairs and unions the municipality polygons and intersects each river with the result.
Method B never calls an overlay clip: it splits each river at the boundary of the region and
sums the lengths of the pieces whose midpoint is covered by the region. The script refuses to
write a reference unless the two agree on every river to within 1e-6 m.

Usage as the reference builder:
    python solve.py ../reference/river_lengths.csv
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.validation import make_valid

WORKING_CRS = "EPSG:3301"
AGREEMENT_M = 1e-6


def clipped_lengths(rivers: gpd.GeoDataFrame, region) -> list[float]:
    return [river.intersection(region).length for river in rivers.geometry]


def split_lengths(rivers: gpd.GeoDataFrame, region) -> list[float]:
    """Length inside the region, computed by splitting at the boundary and testing midpoints."""
    boundary = region.boundary
    lengths = []
    for river in rivers.geometry:
        split = river.difference(boundary)
        pieces = list(split.geoms) if split.geom_type.startswith("Multi") else [split]
        lengths.append(
            sum(
                piece.length
                for piece in pieces
                if not piece.is_empty and region.covers(piece.interpolate(0.5, normalized=True))
            )
        )
    return lengths


def solve(task_dir: Path, output_path: Path) -> None:
    rivers = gpd.read_file(task_dir / "inputs" / "rivers.geojson").to_crs(WORKING_CRS)
    municipalities = gpd.read_file(task_dir / "inputs" / "municipalities.geojson")
    region = shapely.union_all([make_valid(geometry) for geometry in municipalities.geometry])
    lengths_a = clipped_lengths(rivers, region)
    lengths_b = split_lengths(rivers, region)
    for fid, first, second in zip(rivers["source_fid"], lengths_a, lengths_b, strict=True):
        if abs(first - second) > AGREEMENT_M:
            raise SystemExit(f"methods disagree on {fid}: {first} vs {second}")

    rows = [
        {"source_fid": str(fid), "length_m": f"{length:.2f}"}
        for fid, length in zip(rivers["source_fid"], lengths_a, strict=True)
        if length > 0
    ]
    rows.sort(key=lambda row: row["source_fid"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_fid", "length_m"])
        writer.writeheader()
        writer.writerows(rows)
    total = sum(float(row["length_m"]) for row in rows)
    print(
        f"wrote {output_path}: {len(rows)} of {len(rivers)} rivers keep a clipped part, "
        f"{total / 1000:.1f} km of {rivers.geometry.length.sum() / 1000:.1f} km, methods agree"
    )


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.csv (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
