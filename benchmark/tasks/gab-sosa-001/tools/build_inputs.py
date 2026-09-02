"""Freeze the gab-sosa-001 input from the Maa-amet administrative-unit shapefile.

Usage:
    python build_inputs.py --maakond /path/to/maakond.shp --output-dir ../inputs

The source is the Maa-amet "Haldus- ja asustusjaotus" county layer, downloaded as
https://geoportaal.maaamet.ee/docs/haldus_asustus/maakond_shp.zip and already in EPSG:3301.
Only Saare maakond is kept. The 277530-vertex coastline is generalised with a
topology-preserving Douglas-Peucker simplification at 25 m so the frozen input stays a
reviewable text file; that is a property of the benchmark input, not of the analysis, and
the reference is computed from the simplified geometry. The 1065 islands survive the
simplification, which is what makes the multipart handling in the task consequential.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

COUNTY = "Saare maakond"
SIMPLIFY_TOLERANCE_M = 25.0
COORDINATE_PRECISION = 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maakond", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    counties = gpd.read_file(args.maakond)
    selected = counties[counties["MNIMI"] == COUNTY]
    if len(selected) != 1:
        raise SystemExit(f"expected exactly one {COUNTY!r} feature, found {len(selected)}")

    geometry = selected.geometry.iloc[0].simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True)
    if not geometry.is_valid:
        raise SystemExit("simplified county geometry is invalid")
    county = gpd.GeoDataFrame(
        {
            "county_id": [str(selected["MKOOD"].iloc[0])],
            "name": [COUNTY],
        },
        geometry=[geometry],
        crs=counties.crs,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / "saare_maakond.geojson"
    target.unlink(missing_ok=True)
    county.to_file(target, driver="GeoJSON", COORDINATE_PRECISION=COORDINATE_PRECISION)
    print(
        f"wrote {target}: parts={len(geometry.geoms)} "
        f"area_km2={geometry.area / 1e6:.3f} crs={county.crs}"
    )


if __name__ == "__main__":
    main()
