"""Freeze the gab-spa-002 inputs from a raw Overpass response and the Maa-amet boundary.

Usage:
    python build_inputs.py --hospitals hospitals.json \
        --omavalitsus /path/to/omavalitsus.shp --output-dir ../inputs

Raw response from the public Overpass API:

    [out:json][timeout:120];nwr["amenity"="hospital"](59.33,24.52,59.62,24.96);out center tags;

Ways and relations become points at their Overpass ``center``. Only hospitals inside the
Tallinn municipality polygon are kept, and both layers are written in EPSG:3301, because
this task is about the partition and not about reprojection. The boundary comes from the
same Maa-amet municipality layer as gab-sjg-002, generalised at 50 m.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Point

MUNICIPALITY = "Tallinn"
SIMPLIFY_TOLERANCE_M = 50.0
COORDINATE_PRECISION = 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hospitals", type=Path, required=True)
    parser.add_argument("--omavalitsus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source = gpd.read_file(args.omavalitsus)
    selected = source[source["ONIMI"] == MUNICIPALITY]
    if len(selected) != 1:
        raise SystemExit(f"expected exactly one {MUNICIPALITY!r} feature")
    outline = selected.geometry.iloc[0].simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True)
    boundary = gpd.GeoDataFrame(
        {"boundary_id": [str(selected["OKOOD"].iloc[0])], "name": [MUNICIPALITY]},
        geometry=[outline],
        crs=source.crs,
    )

    elements = json.loads(args.hospitals.read_text(encoding="utf-8"))["elements"]
    records = []
    for element in sorted(elements, key=lambda item: (item["type"], item["id"])):
        centre = element.get("center") or (
            {"lon": element["lon"], "lat": element["lat"]} if "lon" in element else None
        )
        if centre is None:
            continue
        records.append(
            {
                "hosp_fid": f"{element['type'][0]}{element['id']}",
                "name": element.get("tags", {}).get("name"),
                "geometry": Point(float(centre["lon"]), float(centre["lat"])),
            }
        )
    hospitals = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326").to_crs(source.crs)
    hospitals = hospitals[hospitals.geometry.within(outline)].reset_index(drop=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for frame, name in ((hospitals, "hospitals.geojson"), (boundary, "boundary.geojson")):
        target = args.output_dir / name
        target.unlink(missing_ok=True)
        frame.to_file(target, driver="GeoJSON", COORDINATE_PRECISION=COORDINATE_PRECISION)

    coordinates = np.c_[hospitals.geometry.x, hospitals.geometry.y]
    distances = np.hypot(
        coordinates[:, None, 0] - coordinates[None, :, 0],
        coordinates[:, None, 1] - coordinates[None, :, 1],
    )
    np.fill_diagonal(distances, np.inf)
    print(
        f"wrote {len(hospitals)} hospitals and the {MUNICIPALITY} boundary to {args.output_dir}: "
        f"{int((distances < 2000).sum() // 2)} hospital pairs closer than 2000 m, "
        f"{int((distances.min(axis=1) < 2000).sum())} hospitals with a neighbour inside 2000 m, "
        f"closest pair {distances.min():.0f} m"
    )


if __name__ == "__main__":
    main()
