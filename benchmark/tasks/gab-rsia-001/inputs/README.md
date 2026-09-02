# gab-rsia-001 inputs

Frozen on 2026-09-02. The image is derived from Copernicus Sentinel-2 data of 2025-06-04,
processed by ESA and distributed through Earth Search (Element 84) under the Copernicus
licence (CC BY-SA 3.0 IGO). The address points are © OpenStreetMap contributors, ODbL-1.0.

| File | Size | CRS | Content |
| --- | --- | --- | --- |
| `image.tif` | 300 × 300, 4 bands, int16 | EPSG:32635 | A 3 × 3 km window of scene `S2B_T35VME_20250604T093340_L2A` (1.6 % cloud) over Tartu, at 10 m. Bands: 1 red (B04), 2 green (B03), 3 blue (B02), 4 near infrared (B08). Values are surface reflectance × 10000, NoData −32768. |
| `addresses.geojson` | 463 points | EPSG:4326 | OpenStreetMap nodes carrying both `addr:street` and `addr:housenumber`. Fields: `adr_id` (`n` + OSM node id), `street`, `housenumber`. |

## Overpass query and scene

```text
[out:json][timeout:180];node["addr:housenumber"]["addr:street"](58.3607,26.6879,58.3966,26.7712);out body;

https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/sentinel-2-c1-l2a/35/V/ME/2025/6/S2B_T35VME_20250604T093340_L2A/
```

`tools/build_inputs.py` stacks B04, B03, B02 and B08 in that order, applies the
processing-baseline BOA offset of −1000 so a stored value is reflectance × 10000, and leaves
the file in the scene's own EPSG:32635. Three coordinate reference systems are therefore in
play: the points arrive in EPSG:4326, the sampling has to happen in EPSG:32635, and the
answer is written in EPSG:3301.

NDVI is invariant to the common scale, so it can be computed on the stored integers
directly; the offset is applied here rather than left to the agent because applying it to
one band and not the other, or not at all, is a data-preparation question and not the
analysis this task scores.

## The synthetic NoData block

Rows 130-149 and columns 150-169, a 200 m square over the densest part of the address layer,
are set to NoData in every band. It stands in for a cloud mask or a tile edge and it is the
only synthetic thing in these inputs. Without it, this window has no NoData at all and the
null clause in the contract would never be exercised.

## What the data discriminates

309 of the 463 points get a value, from 0.0652 to 0.7678, median 0.2507. 154 are null: 108
fall outside the image and 46 on the NoData block.

| Approach | Rows wrong |
| --- | --- |
| nearest-pixel sampling in EPSG:32635, null where undefined (reference) | 0 |
| bilinear sampling instead of nearest | 303 of the 304 comparable points, by up to 0.21 |
| bands read as a colour-infrared composite, near infrared first | all 309 values, sign flipped |
| 0 written instead of null | 154 |
| null rows omitted from the output | fails `feature_count` and the row count |
| output left in EPSG:4326 | fails `crs` |
| points sampled without reprojecting to the image CRS | every point falls outside the image |

The null column is a third of this artifact, which is the point: the attribute comparison is
null-aware, so a null and a zero are never the same answer.
