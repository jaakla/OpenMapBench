"""Reference solver for gab-tmha-001, computed two independent ways and cross-checked.

Both methods start from the same planarisation: every line is split wherever it meets
another line, which gives segments whose interior vertices all have degree two.

Method A is geometric. It polygonizes the whole noded network into faces and marks a segment
as multichannel when the face boundaries cover it.

Method B is graph-theoretic and never builds a polygon. It treats the segments as edges of a
multigraph between their endpoints and finds the bridges with an iterative Tarjan search: an
edge borders an enclosed face exactly when it is not a bridge. The script refuses to write a
reference unless the two classifications agree on every segment.

Usage as the reference builder:
    python solve.py ../reference/canal_classes.csv
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
from shapely import STRtree
from shapely.geometry import MultiPoint
from shapely.ops import polygonize, split, unary_union

WORKING_CRS = "EPSG:3301"
NODE_DECIMALS = 3


def planarize(network: gpd.GeoDataFrame) -> list[tuple[str, object]]:
    """Split every line where it meets another line; keep the source id on each segment."""
    geometries = list(network.geometry)
    tree = STRtree(geometries)
    segments: list[tuple[str, object]] = []
    for index, line in enumerate(geometries):
        cuts = []
        for other in tree.query(line):
            if other == index:
                continue
            crossing = line.intersection(geometries[other])
            if crossing.is_empty:
                continue
            if crossing.geom_type == "Point":
                cuts.append(crossing)
            elif crossing.geom_type == "MultiPoint":
                cuts.extend(crossing.geoms)
            elif crossing.geom_type == "GeometryCollection":
                cuts.extend(part for part in crossing.geoms if part.geom_type == "Point")
            else:
                raise SystemExit(
                    f"line {network['orig_fid'].iloc[index]} overlaps another line along a "
                    "curve; the task assumes a network that meets only at points"
                )
        pieces = split(line, MultiPoint(cuts)).geoms if cuts else [line]
        segments.extend((str(network["orig_fid"].iloc[index]), piece) for piece in pieces)
    return segments


def method_a(network: gpd.GeoDataFrame, segments: list[tuple[str, object]]) -> list[bool]:
    faces = list(polygonize(unary_union(list(network.geometry))))
    if not faces:
        raise SystemExit("the network encloses no faces, the task would be trivial")
    boundaries = unary_union([face.boundary for face in faces])
    return [boundaries.covers(segment) for _, segment in segments]


def method_b(segments: list[tuple[str, object]]) -> list[bool]:
    """An edge borders an enclosed face exactly when it is not a bridge of the graph."""
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], int]]] = defaultdict(list)
    for edge_id, (_, segment) in enumerate(segments):
        coordinates = list(segment.coords)
        start = (round(coordinates[0][0], NODE_DECIMALS), round(coordinates[0][1], NODE_DECIMALS))
        end = (round(coordinates[-1][0], NODE_DECIMALS), round(coordinates[-1][1], NODE_DECIMALS))
        adjacency[start].append((end, edge_id))
        adjacency[end].append((start, edge_id))

    discovery: dict[tuple[float, float], int] = {}
    low: dict[tuple[float, float], int] = {}
    bridges: set[int] = set()
    clock = 0
    for root in list(adjacency):
        if root in discovery:
            continue
        discovery[root] = low[root] = clock
        clock += 1
        stack = [(root, -1, iter(adjacency[root]))]
        while stack:
            node, incoming, neighbours = stack[-1]
            descended = False
            for neighbour, edge_id in neighbours:
                if edge_id == incoming:
                    continue
                if neighbour not in discovery:
                    discovery[neighbour] = low[neighbour] = clock
                    clock += 1
                    stack.append((neighbour, edge_id, iter(adjacency[neighbour])))
                    descended = True
                    break
                low[node] = min(low[node], discovery[neighbour])
            if descended:
                continue
            stack.pop()
            if stack:
                parent = stack[-1][0]
                low[parent] = min(low[parent], low[node])
                if low[node] > discovery[parent]:
                    bridges.add(incoming)
    return [edge_id not in bridges for edge_id in range(len(segments))]


def solve(task_dir: Path, output_path: Path) -> None:
    network = gpd.read_file(task_dir / "inputs" / "network.geojson").to_crs(WORKING_CRS)
    segments = planarize(network)
    classes_a = method_a(network, segments)
    classes_b = method_b(segments)
    disagreements = [
        segments[index][0]
        for index in range(len(segments))
        if classes_a[index] != classes_b[index]
    ]
    if disagreements:
        raise SystemExit(
            f"methods disagree on {len(disagreements)} segments, first on {disagreements[0]}"
        )

    totals: dict[str, list[float]] = {
        str(fid): [0.0, 0.0] for fid in network["orig_fid"].astype(str)
    }
    for (fid, segment), multichannel in zip(segments, classes_a, strict=True):
        totals[fid][0 if multichannel else 1] += segment.length
    rows = [
        {
            "orig_fid": fid,
            "multich_len_m": f"{values[0]:.2f}",
            "single_len_m": f"{values[1]:.2f}",
            "total_len_m": f"{values[0] + values[1]:.2f}",
        }
        for fid, values in sorted(totals.items())
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["orig_fid", "multich_len_m", "single_len_m", "total_len_m"]
        )
        writer.writeheader()
        writer.writerows(rows)
    braided = sum(1 for values in totals.values() if values[0] > 0)
    print(
        f"wrote {output_path}: {len(rows)} lines, {len(segments)} planarized segments, "
        f"{sum(classes_a)} of them multichannel; {braided} lines have a braided part; "
        f"{sum(values[0] for values in totals.values()) / 1000:.2f} km multichannel of "
        f"{sum(sum(values) for values in totals.values()) / 1000:.2f} km, methods agree"
    )


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.csv (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
