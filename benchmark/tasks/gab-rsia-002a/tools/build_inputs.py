"""Freeze the gab-rsia-002a/b landcover raster from an ESA WorldCover tile.

Usage:
    python build_inputs.py --tile ESA_WorldCover_10m_2021_v200_N57E021_Map.tif \
        --output-dir ../inputs

The tile may be a local file or a GDAL virtual path, for example

    /vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/\
ESA_WorldCover_10m_2021_v200_N57E021_Map.tif

A window covering Ruhnu, the smallest Estonian island municipality, is copied out
unchanged: same EPSG:4326 grid, same 1/12000 degree pixel, same uint8 class codes, same
NoData value. Nothing is resampled, because the task's whole point is that the polygons
come from the native grid and only the vectors are reprojected.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

BOUNDS = (23.20, 57.775, 23.285, 57.83)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    with rasterio.open(args.tile) as source:
        window = from_bounds(*BOUNDS, source.transform).round_offsets().round_lengths()
        data = source.read(1, window=window)
        profile = source.profile | {
            "width": data.shape[1],
            "height": data.shape[0],
            "transform": source.window_transform(window),
            "driver": "GTiff",
            "compress": "deflate",
            "tiled": False,
        }
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / "landcover.tif"
    target.unlink(missing_ok=True)
    with rasterio.open(target, "w", **profile) as destination:
        destination.write(data, 1)

    values, counts = np.unique(data, return_counts=True)
    classes = ", ".join(f"{int(value)}:{int(count)}" for value, count in zip(values, counts))
    print(
        f"wrote {target}: {data.shape[1]}x{data.shape[0]} pixels, crs={profile['crs']}, "
        f"nodata={profile['nodata']}, classes {classes}"
    )


if __name__ == "__main__":
    main()
