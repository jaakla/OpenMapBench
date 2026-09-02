"""Freeze the gab-sjg-002 input from the Maa-amet administrative-unit shapefile.

Usage:
    python build_inputs.py --omavalitsus /path/to/omavalitsus.shp --output-dir ../inputs

The source is the Maa-amet "Haldus- ja asustusjaotus" municipality layer, downloaded as
https://geoportaal.maaamet.ee/docs/haldus_asustus/omavalitsus_shp.zip in EPSG:3301. All 79
municipalities are kept, generalised with a topology-preserving Douglas-Peucker
simplification at 50 m (in EPSG:3301, before reprojection) and then written in EPSG:4326,
which is what makes the task's reprojection step consequential. Thirty of the 79 are
multipart because of islands, and six have a centroid that falls outside their own polygon.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

SIMPLIFY_TOLERANCE_M = 50.0
COORDINATE_PRECISION = 7


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--omavalitsus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source = gpd.read_file(args.omavalitsus).sort_values("OKOOD").reset_index(drop=True)
    simplified = source.geometry.simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True)
    if not simplified.is_valid.all():
        raise SystemExit("simplified municipality geometry is invalid")

    projected = gpd.GeoSeries(simplified, crs=source.crs)
    multipart = int((projected.geom_type == "MultiPolygon").sum())
    outside = int((~projected.contains(projected.centroid)).sum())

    municipalities = gpd.GeoDataFrame(
        {
            "poly_fid": source["OKOOD"].astype(str),
            "name": source["ONIMI"].astype(str),
            "county": source["MNIMI"].astype(str),
        },
        geometry=simplified,
        crs=source.crs,
    ).to_crs("EPSG:4326")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / "municipalities.geojson"
    target.unlink(missing_ok=True)
    municipalities.to_file(target, driver="GeoJSON", COORDINATE_PRECISION=COORDINATE_PRECISION)
    print(
        f"wrote {target}: {len(municipalities)} municipalities, {multipart} multipart, "
        f"{outside} with a centroid outside their own polygon, crs={municipalities.crs}"
    )


if __name__ == "__main__":
    main()
