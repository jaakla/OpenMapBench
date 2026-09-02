# gab-rsia-002b inputs

Frozen on 2026-09-02 from ESA WorldCover 10 m 2021 v200, tile `N57E021`. Data is
© ESA WorldCover project 2021, licensed CC BY 4.0; it contains modified Copernicus Sentinel
data (2021) processed by the ESA WorldCover consortium.
This file is a byte-identical copy of
`benchmark/tasks/gab-rsia-002a/inputs/landcover.tif`, which is where `tools/build_inputs.py`
lives; the pair shares one analysis and scores two different artifacts.

Download:

```text
https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N57E021_Map.tif
```

| File | Size | CRS | Content |
| --- | --- | --- | --- |
| `landcover.tif` | 1020 × 660 uint8 | EPSG:4326 | The WorldCover window 23.20/57.775/23.285/57.83 over Ruhnu, the smallest Estonian island municipality, at the native 1/12000 degree pixel. NoData 0. |

Pixel counts by class: 10 tree cover 164518, 30 grassland 66174, 40 cropland 232, 50 built-up
318, 60 bare 1436, 80 water 408130, 90 wetland 32392. No pixel is NoData.

## Processing

`gab-rsia-002a/tools/build_inputs.py` copies the window out of the tile unchanged: same grid, same pixel
size, same class codes, same NoData value, deflate-compressed. Nothing is resampled, because
the task turns on vectorizing the native grid rather than a warped copy of it.

## What the data discriminates

The correct answer has 360 8-connected regions over 7 classes, 30.9611 km² in EPSG:3301, and
41 of the regions touch themselves diagonally so they are only valid as multipolygons.

| Approach | Regions | Effect |
| --- | ---: | --- |
| polygonize the native grid 8-connected, then reproject (reference) | 360 | passes |
| 4-connected instead | 427 | per-class counts 45/153/24/52/51/16/86 against 42/127/22/48/37/11/73; total area identical to 2e-15 relative |
| warp the raster to EPSG:3301 at 10 m first, then polygonize | 298 | invents 2 NoData regions and moves class 40 by 3.5 % and class 50 by 2.3 % |
| leave the polygons in EPSG:4326 | 360 | fails `crs` |
| one feature per class instead of per region | 7 | fails `feature_count` |

The 4-connected row is why the connectivity has to be stated in the prompt and scored by the
feature count: both answers cover exactly the same ground, and only the number of regions
tells them apart. The warp-first row is the failure the source case reports, where every
model skipped the reprojection; here the prompt names the order outright, which makes this
pair the family's no-caveat control.
