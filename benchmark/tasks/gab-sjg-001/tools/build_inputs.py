"""Freeze the gab-sjg-001 inputs from raw Overpass responses.

Usage:
    python build_inputs.py --stops stops.json --destinations dest.json --output-dir ../inputs

The raw responses come from two Overpass API queries over the Tartu bounding box
(south 58.33, west 26.66, north 58.42, east 26.80):

    [out:json][timeout:90];node["highway"="bus_stop"](58.33,26.66,58.42,26.80);out body;
    [out:json][timeout:90];nwr["amenity"~"^(school|kindergarten)$"](58.33,26.66,58.42,26.80);
        out center tags;

Ways and relations become points at their Overpass ``center``. Three synthetic probe
destinations are added at known EPSG:3301 distances from one anchor stop so that the
1-mile threshold is actually exercised by the data; they are flagged ``synthetic: yes``.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from pyproj import Transformer

MILE_M = 1609.344
# Distances of the synthetic probes from the anchor stop, in EPSG:3301 metres.
# 1600.5 and 1605.0 lie inside one mile; 1609.7 lies outside. The inside probes sit far
# enough from the threshold to survive a 16-segment polygonal buffer (max chord error
# about 1.9 m at this radius), while any 1600 m or 1.6 km threshold excludes them and any
# 1610 m threshold includes the outside probe.
PROBES = [(1600.5, 30.0), (1605.0, 150.0), (1609.7, 270.0)]
PRECISION = 9


def _point(element: dict) -> tuple[float, float] | None:
    if "lon" in element and "lat" in element:
        return float(element["lon"]), float(element["lat"])
    center = element.get("center")
    if center:
        return float(center["lon"]), float(center["lat"])
    return None


def _feature(properties: dict, lon: float, lat: float) -> dict:
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {
            "type": "Point",
            "coordinates": [round(lon, PRECISION), round(lat, PRECISION)],
        },
    }


def _write(path: Path, features: list[dict]) -> None:
    collection = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(collection, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stops", type=Path, required=True)
    parser.add_argument("--destinations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    stops = json.loads(args.stops.read_text(encoding="utf-8"))["elements"]
    stops = sorted((e for e in stops if e["type"] == "node"), key=lambda e: e["id"])
    origins = []
    for element in stops:
        tags = element.get("tags", {})
        origins.append(
            _feature(
                {
                    "stop_id": element["id"],
                    "name": tags.get("name"),
                    "shelter": tags.get("shelter"),
                },
                float(element["lon"]),
                float(element["lat"]),
            )
        )

    dests = json.loads(args.destinations.read_text(encoding="utf-8"))["elements"]
    destinations = []
    for element in sorted(dests, key=lambda e: (e["type"], e["id"])):
        point = _point(element)
        if point is None:
            continue
        tags = element.get("tags", {})
        destinations.append(
            _feature(
                {
                    "dest_id": f"{element['type'][0]}{element['id']}",
                    "name": tags.get("name"),
                    "amenity": tags.get("amenity"),
                    "synthetic": "no",
                },
                *point,
            )
        )

    # Anchor: the median stop by id, so the choice is deterministic and documented.
    anchor = origins[len(origins) // 2]
    anchor_lon, anchor_lat = anchor["geometry"]["coordinates"]
    forward = Transformer.from_crs("EPSG:4326", "EPSG:3301", always_xy=True)
    inverse = Transformer.from_crs("EPSG:3301", "EPSG:4326", always_xy=True)
    ax, ay = forward.transform(anchor_lon, anchor_lat)
    for index, (distance, bearing) in enumerate(PROBES, start=1):
        px = ax + distance * math.sin(math.radians(bearing))
        py = ay + distance * math.cos(math.radians(bearing))
        lon, lat = inverse.transform(px, py)
        lon, lat = round(lon, PRECISION), round(lat, PRECISION)
        rx, ry = forward.transform(lon, lat)
        realised = math.hypot(rx - ax, ry - ay)
        assert abs(realised - distance) < 0.002, (distance, realised)
        destinations.append(
            _feature(
                {
                    "dest_id": f"probe{index}",
                    "name": f"Probe {index} ({distance:.1f} m from stop {anchor['properties']['stop_id']})",
                    "amenity": "school",
                    "synthetic": "yes",
                },
                lon,
                lat,
            )
        )
        print(f"probe{index}: {distance} m requested, {realised:.4f} m realised, bearing {bearing}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write(args.output_dir / "origins.geojson", origins)
    _write(args.output_dir / "destinations.geojson", destinations)
    print(f"anchor stop_id={anchor['properties']['stop_id']} name={anchor['properties']['name']!r}")
    print(f"origins={len(origins)} destinations={len(destinations)}")


if __name__ == "__main__":
    main()
