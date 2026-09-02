"""Freeze the gab-tmha-001 drainage network from a raw Overpass response.

Usage:
    python build_inputs.py --network network.json --output-dir ../inputs

Raw response from the public Overpass API over the lower Emajõgi and the Peipsi shore
polders, a landscape of drained fields, braided channels and river islands:

    [out:json][timeout:180];
    way["waterway"~"^(ditch|drain|stream|canal|river)$"](58.40,26.80,58.80,27.20);out geom;

Nothing is simplified: the vertices carry the shared OSM nodes that make the network a
graph, and moving them would change the topology the task is about. Coordinates are written
in EPSG:4326 with 7 decimals, so two ways that share a node still share it exactly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

COORDINATE_PRECISION = 7


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    elements = json.loads(args.network.read_text(encoding="utf-8"))["elements"]
    records = [
        {
            "orig_fid": f"w{element['id']}",
            "waterway": element.get("tags", {}).get("waterway"),
            "name": element.get("tags", {}).get("name"),
            "geometry": LineString(
                [(node["lon"], node["lat"]) for node in element["geometry"]]
            ),
        }
        for element in sorted(elements, key=lambda item: item["id"])
        if len(element.get("geometry", [])) > 1
    ]
    network = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    if network["orig_fid"].duplicated().any():
        raise SystemExit("duplicate way ids in the response")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / "network.geojson"
    target.unlink(missing_ok=True)
    network.to_file(target, driver="GeoJSON", COORDINATE_PRECISION=COORDINATE_PRECISION)

    projected = gpd.read_file(target).to_crs("EPSG:3301")
    noded = unary_union(projected.geometry.values)
    faces = list(polygonize(noded))
    overlap = projected.geometry.length.sum() - noded.length
    print(
        f"wrote {target}: {len(network)} lines, "
        f"{projected.geometry.length.sum() / 1000:.1f} km, {len(faces)} enclosed faces "
        f"covering {sum(face.area for face in faces) / 1e4:.1f} ha, "
        f"collinear overlap {overlap:.3f} m"
    )


if __name__ == "__main__":
    main()
