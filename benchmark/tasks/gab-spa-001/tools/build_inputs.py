"""Freeze the gab-spa-001 park inputs from a raw Overpass response.

Usage:
    python build_inputs.py --parks parks.json --output-dir ../inputs

Raw response from the public Overpass API over a box that covers the task's grid extent:

    [out:json][timeout:120];way["leisure"="park"](59.33,24.52,59.62,24.96);out geom;

Closed ways become polygons, everything is reprojected to EPSG:3301 and rounded to the
centimetre, and parks that do not reach the grid extent are dropped. Four synthetic parks
are appended. They are the only features whose relationship to the grid lines is
deliberate: two touch a grid line or grid node without overlapping the neighbouring cell,
and two are multipart, so that a count of parts and a count of parks differ.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon, Polygon, box

GRID = {"xmin": 531000.0, "ymin": 6579000.0, "xmax": 553000.0, "ymax": 6607000.0, "cell": 1000.0}
COORDINATE_PRECISION = 2
MIN_AREA_M2 = 100.0


def _synthetic_parks() -> list[dict]:
    """Probes placed on the grid lines; see inputs/README.md for what each one discriminates."""
    edge = box(540000.0, 6588200.0, 540400.0, 6588600.0)
    corner = box(541000.0, 6589000.0, 541300.0, 6589300.0)
    same_cell = MultiPolygon(
        [
            box(543100.0, 6590100.0, 543300.0, 6590300.0),
            box(543400.0, 6590400.0, 543600.0, 6590600.0),
            box(543700.0, 6590700.0, 543900.0, 6590900.0),
        ]
    )
    spanning = MultiPolygon(
        [
            box(545100.0, 6592100.0, 545400.0, 6592400.0),
            box(545600.0, 6592600.0, 545900.0, 6592900.0),
            box(546100.0, 6593100.0, 546400.0, 6593400.0),
            box(546600.0, 6592100.0, 546900.0, 6592400.0),
        ]
    )
    return [
        {
            "park_id": "probe_edge",
            "name": "Probe: west edge on the grid line x=540000",
            "geometry": edge,
        },
        {
            "park_id": "probe_corner",
            "name": "Probe: south-west corner on the grid node (541000, 6589000)",
            "geometry": corner,
        },
        {
            "park_id": "probe_multipart_one_cell",
            "name": "Probe: three parts inside a single cell",
            "geometry": same_cell,
        },
        {
            "park_id": "probe_multipart_spanning",
            "name": "Probe: four parts, two of them in the same cell",
            "geometry": spanning,
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parks", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    elements = json.loads(args.parks.read_text(encoding="utf-8"))["elements"]
    records = []
    for element in sorted(elements, key=lambda item: item["id"]):
        ring = [(node["lon"], node["lat"]) for node in element.get("geometry", [])]
        if len(ring) < 4 or ring[0] != ring[-1]:
            continue
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.geom_type not in {"Polygon", "MultiPolygon"}:
            continue
        records.append(
            {
                "park_id": f"w{element['id']}",
                "name": element.get("tags", {}).get("name"),
                "synthetic": "no",
                "geometry": polygon,
            }
        )

    parks = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326").to_crs("EPSG:3301")
    extent = box(GRID["xmin"], GRID["ymin"], GRID["xmax"], GRID["ymax"])
    parks = parks[parks.geometry.intersects(extent) & (parks.geometry.area >= MIN_AREA_M2)]
    parks = parks.reset_index(drop=True)

    synthetic = gpd.GeoDataFrame(
        [item | {"synthetic": "yes"} for item in _synthetic_parks()],
        geometry="geometry",
        crs="EPSG:3301",
    )
    combined = gpd.GeoDataFrame(
        pd.concat([parks, synthetic], ignore_index=True), geometry="geometry", crs="EPSG:3301"
    ).reset_index(drop=True)
    combined["geometry"] = shapely.set_precision(
        combined.geometry.values, 10.0**-COORDINATE_PRECISION
    )
    if not combined.geometry.is_valid.all():
        raise SystemExit("rounding produced an invalid park geometry")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / "parks.geojson"
    target.unlink(missing_ok=True)
    combined.to_file(target, driver="GeoJSON", COORDINATE_PRECISION=COORDINATE_PRECISION)
    multipart = int((combined.geometry.geom_type == "MultiPolygon").sum())
    print(
        f"wrote {target}: {len(combined)} parks ({len(synthetic)} synthetic), "
        f"{multipart} multipart, total area {combined.geometry.area.sum() / 1e6:.2f} km2"
    )


if __name__ == "__main__":
    main()
