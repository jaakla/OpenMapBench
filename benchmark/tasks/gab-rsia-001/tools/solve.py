"""Reference solver for gab-rsia-001, computed two independent ways and cross-checked.

Method A reprojects the points with GeoPandas and reads the pixel under each one with
rasterio's sampler. Method B transforms the same points with a bare pyproj transformer,
turns coordinates into row and column indices with the inverse of the affine transform, and
indexes the array itself. They share no sampling code, and they must agree on every point,
including which points have no value at all.

NDVI is null for a point that falls outside the image, for a point whose pixel is NoData in
either band, and for a pixel where NIR + red is zero. On this data the first two cases occur
and the third does not; it is in the contract because the quotient is otherwise undefined.

Usage as the reference builder:
    python solve.py ../reference/addresses_ndvi.gpkg
Usage as an agent stand-in under ``openmapbench run`` (reads OPENMAPBENCH_* variables).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer

TARGET_CRS = "EPSG:3301"
RED_BAND, NIR_BAND = 1, 4
DECIMALS = 4


def _ndvi(red: np.ndarray, nir: np.ndarray, valid: np.ndarray) -> np.ndarray:
    total = nir + red
    values = np.full(red.shape, np.nan)
    usable = valid & (total != 0)
    values[usable] = (nir[usable] - red[usable]) / total[usable]
    return np.round(values, DECIMALS)


def method_a(addresses: gpd.GeoDataFrame, image_path: Path) -> np.ndarray:
    with rasterio.open(image_path) as image:
        points = addresses.to_crs(image.crs)
        coordinates = list(zip(points.geometry.x, points.geometry.y, strict=True))
        samples = np.array(
            list(image.sample(coordinates, indexes=[RED_BAND, NIR_BAND], masked=True)),
            dtype="float64",
        )
        inside = np.array(
            [
                image.bounds.left <= x < image.bounds.right
                and image.bounds.bottom < y <= image.bounds.top
                for x, y in coordinates
            ]
        )
        nodata = image.nodata
    red, nir = samples[:, 0], samples[:, 1]
    valid = inside & (red != nodata) & (nir != nodata)
    return _ndvi(red, nir, valid)


def method_b(addresses: gpd.GeoDataFrame, image_path: Path) -> np.ndarray:
    with rasterio.open(image_path) as image:
        red_band = image.read(RED_BAND).astype("float64")
        nir_band = image.read(NIR_BAND).astype("float64")
        transform, crs, nodata = image.transform, image.crs, image.nodata
    transformer = Transformer.from_crs(addresses.crs, crs, always_xy=True)
    x, y = transformer.transform(
        addresses.geometry.x.to_numpy(), addresses.geometry.y.to_numpy()
    )
    column = np.floor((x - transform.c) / transform.a).astype(int)
    row = np.floor((y - transform.f) / transform.e).astype(int)
    height, width = red_band.shape
    inside = (column >= 0) & (column < width) & (row >= 0) & (row < height)
    safe_row = np.clip(row, 0, height - 1)
    safe_column = np.clip(column, 0, width - 1)
    red = red_band[safe_row, safe_column]
    nir = nir_band[safe_row, safe_column]
    valid = inside & (red != nodata) & (nir != nodata)
    return _ndvi(red, nir, valid)


def solve(task_dir: Path, output_path: Path) -> None:
    image_path = task_dir / "inputs" / "image.tif"
    addresses = gpd.read_file(task_dir / "inputs" / "addresses.geojson")
    ndvi_a = method_a(addresses, image_path)
    ndvi_b = method_b(addresses, image_path)
    same_null = np.isnan(ndvi_a) == np.isnan(ndvi_b)
    both = ~np.isnan(ndvi_a) & ~np.isnan(ndvi_b)
    if not same_null.all() or not np.allclose(ndvi_a[both], ndvi_b[both], atol=0, rtol=0):
        raise SystemExit("the two sampling routes disagree")

    result = addresses.to_crs(TARGET_CRS)
    result["ndvi"] = ndvi_a
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    result.to_file(output_path, layer="addresses_ndvi", driver="GPKG")
    values = ndvi_a[~np.isnan(ndvi_a)]
    print(
        f"wrote {output_path}: {len(result)} points, {len(values)} with an NDVI from "
        f"{values.min():.4f} to {values.max():.4f} (median {np.median(values):.4f}), "
        f"{int(np.isnan(ndvi_a).sum())} null, methods agree"
    )


if __name__ == "__main__":
    task_dir = Path(
        os.environ.get("OPENMAPBENCH_TASK_DIR") or Path(__file__).resolve().parent.parent
    )
    output = os.environ.get("OPENMAPBENCH_OUTPUT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not output:
        raise SystemExit("usage: solve.py OUTPUT.gpkg (or set OPENMAPBENCH_OUTPUT_PATH)")
    solve(task_dir, Path(output))
