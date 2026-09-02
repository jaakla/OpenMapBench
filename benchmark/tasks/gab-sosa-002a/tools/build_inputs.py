"""Freeze the gab-sosa-002a/b inputs: rivers in EPSG:4326, municipalities in EPSG:3301.

Usage:
    python build_inputs.py --rivers rivers.json --omavalitsus /path/to/omavalitsus.shp \
        --output-dir ../inputs

Raw response from the public Overpass API:

    [out:json][timeout:180];way["waterway"="river"](58.10,23.90,58.95,25.30);out geom;

Rivers are simplified at 5 m in EPSG:3301 and written back in EPSG:4326, which is the CRS
the task requires the clipped output to keep. Only rivers within 2000 m of the four
municipalities are kept, so selecting the ones that actually intersect is still part of the
task. The municipalities are generalised at 50 m, and two of them are given a deliberate
topology defect: an hourglass part with a self-touching ring, placed strictly inside the
municipality so that it is also a nested shell. Both defects are area-preserving under
repair, which the script asserts for make_valid, a zero-width buffer and a self-union, so
the intended answer does not depend on which repair an agent chooses.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.validation import make_valid

MUNICIPALITIES = ["Pärnu linn", "Tori vald", "Saarde vald", "Põhja-Pärnumaa vald"]
DEFECTIVE = {"Tori vald", "Saarde vald"}
SIMPLIFY_MUNICIPALITY_M = 50.0
SIMPLIFY_RIVER_M = 5.0
NEAR_M = 2000.0
DEFECT_HALF_SIZE_M = 400.0
DEFECT_CLEARANCE_M = 200.0


def _hourglass(x: float, y: float, half: float) -> Polygon:
    """A ring that touches itself at (x, y): two triangles joined at a single point."""
    return Polygon(
        [
            (x - half, y - half),
            (x + half, y - half),
            (x, y),
            (x + half, y + half),
            (x - half, y + half),
            (x, y),
        ]
    )


def _with_defect(geometry, rivers: gpd.GeoSeries, taken: list) -> MultiPolygon:
    """Attach an hourglass part with a self-touching ring, sitting on a river just outside.

    The part is kept disjoint from every other part, from the other municipalities and from
    the other defect, so that no ring is nested in another. That matters: a ring nested
    inside another is repaired to a hole by ``make_valid`` and to filled area by a zero-width
    buffer, and a benchmark answer must not depend on which repair the agent reaches for.
    """
    for river in rivers:
        for fraction in (0.25, 0.5, 0.75):
            anchor = river.interpolate(fraction, normalized=True)
            hourglass = _hourglass(anchor.x, anchor.y, DEFECT_HALF_SIZE_M)
            if hourglass.distance(geometry) < DEFECT_CLEARANCE_M:
                continue
            if any(hourglass.distance(other) < DEFECT_CLEARANCE_M for other in taken):
                continue
            if not hourglass.intersects(river):
                continue
            parts = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
            taken.append(hourglass)
            return MultiPolygon(parts + [hourglass])
    raise SystemExit("no river position could carry the defect")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rivers", type=Path, required=True)
    parser.add_argument("--omavalitsus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source = gpd.read_file(args.omavalitsus)
    selected = source[source["ONIMI"].isin(MUNICIPALITIES)].sort_values("OKOOD")
    if len(selected) != len(MUNICIPALITIES):
        raise SystemExit(f"expected {len(MUNICIPALITIES)} municipalities, found {len(selected)}")
    simplified = selected.geometry.simplify(SIMPLIFY_MUNICIPALITY_M, preserve_topology=True)

    elements = json.loads(args.rivers.read_text(encoding="utf-8"))["elements"]
    records = [
        {
            "source_fid": f"w{element['id']}",
            "name": element.get("tags", {}).get("name"),
            "geometry": LineString(
                [(node["lon"], node["lat"]) for node in element["geometry"]]
            ),
        }
        for element in sorted(elements, key=lambda item: item["id"])
        if len(element.get("geometry", [])) > 1
    ]
    rivers = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326").to_crs(source.crs)
    rivers["geometry"] = rivers.geometry.simplify(SIMPLIFY_RIVER_M)
    union = shapely.union_all(simplified.values)
    rivers = rivers[rivers.geometry.distance(union) < NEAR_M].reset_index(drop=True)

    geometries = []
    taken = list(simplified.values)
    for name, geometry in zip(selected["ONIMI"], simplified, strict=True):
        if name not in DEFECTIVE:
            geometries.append(geometry)
            continue
        defective = _with_defect(geometry, rivers.geometry, taken)
        if defective.is_valid:
            raise SystemExit(f"the defect on {name} did not make the geometry invalid")
        repairs = [make_valid(defective), defective.buffer(0), shapely.union_all([defective])]
        for repair in repairs[1:]:
            if repair.symmetric_difference(repairs[0]).area > 1e-6:
                raise SystemExit(f"repairs of {name} disagree, the answer would be ambiguous")
        geometries.append(defective)

    municipalities = gpd.GeoDataFrame(
        {
            "muni_id": selected["OKOOD"].astype(str).to_list(),
            "name": selected["ONIMI"].astype(str).to_list(),
        },
        geometry=geometries,
        crs=source.crs,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for frame, name, precision in (
        (rivers.to_crs("EPSG:4326"), "rivers.geojson", 7),
        (municipalities, "municipalities.geojson", 2),
    ):
        target = args.output_dir / name
        target.unlink(missing_ok=True)
        frame.to_file(target, driver="GeoJSON", COORDINATE_PRECISION=precision)

    intersecting = int(rivers.geometry.intersects(union).sum())
    crossing = int((rivers.geometry.intersects(union) & ~rivers.geometry.within(union)).sum())
    print(
        f"wrote {len(rivers)} rivers and {len(municipalities)} municipalities to "
        f"{args.output_dir}: {intersecting} rivers intersect the union, {crossing} cross its "
        f"boundary, {int((~municipalities.geometry.is_valid).sum())} municipality geometries "
        "are invalid by construction"
    )


if __name__ == "__main__":
    main()
