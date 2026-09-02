"""Freeze the gab-tmha-002/002s elevation input from a Copernicus GLO-30 tile.

Usage:
    python build_inputs.py --tile Copernicus_DSM_COG_10_N57_00_E026_00_DEM.tif \
        --output-dir ../inputs

The tile may be a local file or a GDAL virtual path, for example

    /vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/\
Copernicus_DSM_COG_10_N57_00_E026_00_DEM/Copernicus_DSM_COG_10_N57_00_E026_00_DEM.tif

A window over the Haanja upland is copied out unchanged, in the tile's own EPSG:4326 grid
(1 arcsecond in latitude, 1.5 arcseconds in longitude) and float32 values. Nothing is
resampled here: the resampling is the step the task is about, and the window is large enough
that the task's 6.5 x 8.0 km analysis grid sits inside it with a margin of about 200 m on
every side, so no bilinear stencil ever reaches past the data.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

BOUNDS = (26.85, 57.70, 26.97, 57.78)


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
    target = args.output_dir / "dem.tif"
    target.unlink(missing_ok=True)
    with rasterio.open(target, "w", **profile) as destination:
        destination.write(data, 1)

    print(
        f"wrote {target}: {data.shape[1]}x{data.shape[0]} pixels, crs={profile['crs']}, "
        f"pixel {profile['transform'].a:.8f} x {abs(profile['transform'].e):.8f} degrees, "
        f"elevation {float(np.min(data)):.1f}..{float(np.max(data)):.1f} m, "
        f"nodata={profile['nodata']}"
    )


if __name__ == "__main__":
    main()
