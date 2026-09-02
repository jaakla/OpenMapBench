# gab-tmha-002 inputs

Frozen on 2026-09-02 from the Copernicus DEM GLO-30, tile
`Copernicus_DSM_COG_10_N57_00_E026_00_DEM` (object last modified 2022-05-09). The Copernicus
DEM is free to use for any purpose with attribution: © DLR e.V. 2010-2014 and © Airbus
Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA.
`benchmark/tasks/gab-tmha-002s/inputs/` holds a byte-identical copy; the pair shares one
analysis and scores two different artifacts.

Download:

```text
https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N57_00_E026_00_DEM/Copernicus_DSM_COG_10_N57_00_E026_00_DEM.tif
```

| File | Size | CRS | Content |
| --- | --- | --- | --- |
| `dem.tif` | 288 × 288 float32 | EPSG:4326 | The window 26.85/57.70/26.97/57.78 over the Haanja upland, at the tile's native pixel: 1 arcsecond in latitude, 1.5 arcseconds in longitude. Elevations 87.8 to 230.5 m, no NoData. |

## Processing

`tools/build_inputs.py` copies the window out unchanged. The task's 6.5 × 8.0 km analysis
grid in EPSG:3301 sits inside this window with roughly 200 m of margin on every side, so a
bilinear stencil never reaches past the data and no edge rule is needed for the resampling
itself.

## What the data discriminates

The reference has 289 steep regions covering 754.6 ha, 14.5 % of the 5200 ha grid, with a
steepest slope of 100.6 %.

| Approach | Regions | Area | Symmetric difference vs reference |
| --- | ---: | ---: | ---: |
| bilinear resample with GDAL's warper, then Horn slope (reference) | 289 | 754.6 ha | 0 |
| bilinear interpolation at the target cell centres, then Horn slope | 294 | 756.5 ha | 0.0376 |
| Horn slope on the 30 m grid, then resample the slope | 224 | 529.0 ha | 0.4033 |

The second row is not an error. It is a second correct reading of the same prompt, and the
two differ because GDAL's warper does not reduce to a textbook bilinear interpolation at the
cell centre: on this DEM the two resampled surfaces differ by up to 0.9 m, which is enough to
move the 20 % isoline across whole cells. That measurement, not a guess, is where this task's
6 % geometry tolerance and 5 % scalar tolerance come from, and it is why the region count is
not scored exactly.

The third row is the failure the source case reports. Taking the slope first smooths the
surface at 30 m and then interpolates the smoothed slope, which loses 30 % of the steep area
and a fifth of the regions — far outside any tolerance that admits the second row.
