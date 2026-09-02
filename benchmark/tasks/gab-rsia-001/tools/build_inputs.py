"""Freeze the gab-rsia-001 inputs: a 4-band image over Tartu and address points.

Usage:
    python build_inputs.py --scene https://.../S2B_T35VME_20250604T093340_L2A/ \
        --addresses addresses.json --output-dir ../inputs

The image is a 3 x 3 km window of the Sentinel-2 L2A scene S2B_T35VME_20250604T093340
(1.6 % cloud), stacked in the band order the task declares: 1 red (B04), 2 green (B03),
3 blue (B02), 4 near infrared (B08). The processing-baseline offset of -1000 is applied, so
a stored value is surface reflectance times 10000 and NDVI can be computed on the stored
numbers directly. The file stays in the scene's own EPSG:32635, which is neither the CRS the
points arrive in nor the CRS the answer is written in.

One 20 x 20 pixel block, rows 130-149 and columns 150-169, is set to the NoData value in
every band. It stands in for a cloud mask or a tile edge and it is the only synthetic thing
in these inputs; it was placed on the densest part of the address layer so that the NoData
clause is actually scored, and it covers 46 of the 463 address points.

The address points are OpenStreetMap nodes with a street and a house number, taken over a
box wider than the image so that 108 of them fall outside it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from shapely.geometry import Point

ORIGIN_X, ORIGIN_Y = 482120.0, 6472560.0
SIZE = 300
CELL_M = 10.0
BANDS = [("B04", "red"), ("B03", "green"), ("B02", "blue"), ("B08", "nir")]
BOA_OFFSET = -1000
NODATA = -32768
NODATA_BLOCK = (130, 150, 20)
COORDINATE_PRECISION = 7


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True, help="scene asset directory URL or path")
    parser.add_argument("--addresses", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    bounds = (
        ORIGIN_X,
        ORIGIN_Y - SIZE * CELL_M,
        ORIGIN_X + SIZE * CELL_M,
        ORIGIN_Y,
    )
    stack = np.zeros((len(BANDS), SIZE, SIZE), dtype="int16")
    for index, (band, _) in enumerate(BANDS):
        source_path = args.scene.rstrip("/") + f"/{band}.tif"
        if source_path.startswith("http"):
            source_path = "/vsicurl/" + source_path
        with rasterio.open(source_path) as source:
            window = from_bounds(*bounds, source.transform).round_offsets().round_lengths()
            data = source.read(1, window=window).astype("int32") + BOA_OFFSET
        if data.shape != (SIZE, SIZE):
            raise SystemExit(f"{band} window is {data.shape}, expected {(SIZE, SIZE)}")
        stack[index] = data.astype("int16")

    row, column, extent = NODATA_BLOCK
    stack[:, row : row + extent, column : column + extent] = NODATA

    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / "image.tif"
    target.unlink(missing_ok=True)
    with rasterio.open(
        target,
        "w",
        driver="GTiff",
        width=SIZE,
        height=SIZE,
        count=len(BANDS),
        dtype="int16",
        crs="EPSG:32635",
        transform=from_origin(ORIGIN_X, ORIGIN_Y, CELL_M, CELL_M),
        nodata=NODATA,
        compress="deflate",
    ) as destination:
        destination.write(stack)
        destination.descriptions = tuple(name for _, name in BANDS)

    elements = json.loads(args.addresses.read_text(encoding="utf-8"))["elements"]
    records = [
        {
            "adr_id": f"n{element['id']}",
            "street": element.get("tags", {}).get("addr:street"),
            "housenumber": element.get("tags", {}).get("addr:housenumber"),
            "geometry": Point(float(element["lon"]), float(element["lat"])),
        }
        for element in sorted(elements, key=lambda item: item["id"])
    ]
    addresses = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    points = args.output_dir / "addresses.geojson"
    points.unlink(missing_ok=True)
    addresses.to_file(points, driver="GeoJSON", COORDINATE_PRECISION=COORDINATE_PRECISION)

    utm = addresses.to_crs("EPSG:32635")
    columns = ((utm.geometry.x - ORIGIN_X) / CELL_M).astype(int)
    rows = ((ORIGIN_Y - utm.geometry.y) / CELL_M).astype(int)
    inside = (columns >= 0) & (columns < SIZE) & (rows >= 0) & (rows < SIZE)
    masked = (
        inside
        & (rows >= row)
        & (rows < row + extent)
        & (columns >= column)
        & (columns < column + extent)
    )
    print(
        f"wrote {target} ({SIZE}x{SIZE}, 4 bands int16, EPSG:32635, nodata {NODATA}) and "
        f"{points} ({len(addresses)} address points): {int(inside.sum())} fall on the image, "
        f"{int((~inside).sum())} outside it, {int(masked.sum())} on the NoData block"
    )


if __name__ == "__main__":
    main()
