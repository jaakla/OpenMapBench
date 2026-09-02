"""Reference solver for gab-sosa-002a, computed two independent ways and cross-checked.

Method A repairs the municipality polygons, unions them, and intersects each river with the
result. Method B never calls an overlay clip: it splits each river at the boundary of the
region with a line difference, keeps the pieces whose midpoint is covered by the region, and
reassembles them. The script refuses to write a reference unless the two agree on every
river to within 1e-6 m of Hausdorff distance and 1e-6 m of length.

It also measures the two things a reader of the contract needs to trust the tolerances: how
much clipping in EPSG:4326 differs from clipping in EPSG:3301, and whether skipping the
repair of the two deliberately invalid municipality polygons changes the answer on this
toolchain.

Usage as the reference builder:
    python solve.py ../reference/rivers_clipped.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.validation import make_valid

WORKING_CRS = "EPSG:3301"
OUTPUT_CRS = "EPSG:4326"
AGREEMENT_M = 1e-6


def repaired_region(municipalities: gpd.GeoDataFrame):
    return shapely.union_all([make_valid(geometry) for geometry in municipalities.geometry])


def method_a(rivers: gpd.GeoDataFrame, region) -> list:
    return [river.intersection(region) for river in rivers.geometry]


def method_b(rivers: gpd.GeoDataFrame, region) -> list:
    """Split each river at the region boundary and keep the pieces that lie inside."""
    boundary = region.boundary
    result = []
    for river in rivers.geometry:
        split = river.difference(boundary)
        pieces = list(split.geoms) if split.geom_type.startswith("Multi") else [split]
        inside = [
            piece
            for piece in pieces
            if not piece.is_empty and region.covers(piece.interpolate(0.5, normalized=True))
        ]
        result.append(shapely.line_merge(shapely.union_all(inside)) if inside else split.difference(split))
    return result


def unrepaired_difference(rivers: gpd.GeoDataFrame, municipalities: gpd.GeoDataFrame, clipped: list) -> str:
    """What the two invalid municipality polygons cost an agent that does not repair them."""
    naive_region = shapely.union_all(municipalities.geometry.values)
    naive = [river.intersection(naive_region) for river in rivers.geometry]
    changed = sum(
        1
        for reference, candidate in zip(clipped, naive, strict=True)
        if abs(reference.length - candidate.length) > 0.01
    )
    dropped_region = shapely.union_all(
        [make_valid(geometry) for geometry in municipalities.geometry[municipalities.geometry.is_valid]]
    )
    dropped = [river.intersection(dropped_region) for river in rivers.geometry]
    dropped_changed = sum(
        1
        for reference, candidate in zip(clipped, dropped, strict=True)
        if abs(reference.length - candidate.length) > 0.01
    )
    return (
        f"rivers whose clipped length changes when the invalid polygons are used unrepaired: "
        f"{changed}; when they are dropped instead of repaired: {dropped_changed}"
    )


def crs_order_difference(rivers: gpd.GeoDataFrame, municipalities: gpd.GeoDataFrame, clipped: list) -> str:
    """Clipping in EPSG:4326 instead of EPSG:3301: the size of the tolerance this needs."""
    region_4326 = shapely.union_all(
        [make_valid(geometry) for geometry in municipalities.to_crs(OUTPUT_CRS).geometry]
    )
    other = gpd.GeoSeries(
        [river.intersection(region_4326) for river in rivers.to_crs(OUTPUT_CRS).geometry],
        crs=OUTPUT_CRS,
    ).to_crs(WORKING_CRS)
    worst = max(
        candidate.hausdorff_distance(reference)
        for candidate, reference in zip(other, clipped, strict=True)
        if not candidate.is_empty and not reference.is_empty
    )
    return f"clipping in EPSG:4326 instead of EPSG:3301 moves a river by at most {worst:.3f} m"


def solve(task_dir: Path, output_path: Path) -> None:
    rivers = gpd.read_file(task_dir / "inputs" / "rivers.geojson").to_crs(WORKING_CRS)
    municipalities = gpd.read_file(task_dir / "inputs" / "municipalities.geojson")
    region = repaired_region(municipalities)
    clipped_a = method_a(rivers, region)
    clipped_b = method_b(rivers, region)
    for fid, first, second in zip(rivers["source_fid"], clipped_a, clipped_b, strict=True):
        if abs(first.length - second.length) > AGREEMENT_M:
            raise SystemExit(f"methods disagree on {fid}: {first.length} vs {second.length}")
        if not first.is_empty and first.hausdorff_distance(second) > AGREEMENT_M:
            raise SystemExit(f"methods disagree geometrically on {fid}")

    keep = [index for index, geometry in enumerate(clipped_a) if not geometry.is_empty]
    result = gpd.GeoDataFrame(
        {
            "source_fid": rivers["source_fid"].iloc[keep].astype(str).to_list(),
            "name": rivers["name"].iloc[keep].to_list(),
        },
        geometry=[shapely.line_merge(clipped_a[index]) for index in keep],
        crs=WORKING_CRS,
    ).to_crs(OUTPUT_CRS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    result.to_file(output_path, layer="rivers_clipped", driver="GPKG")
    total_km = sum(clipped_a[index].length for index in keep) / 1000
    print(
        f"wrote {output_path}: {len(result)} of {len(rivers)} rivers keep a clipped part, "
        f"{total_km:.1f} km of {rivers.geometry.length.sum() / 1000:.1f} km, methods agree"
    )
    print(unrepaired_difference(rivers, municipalities, clipped_a))
    print(crs_order_difference(rivers, municipalities, clipped_a))


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
