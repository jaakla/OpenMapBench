# gab-sjg-002 inputs

Frozen on 2026-09-02 from the Maa-amet "Haldus- ja asustusjaotus" municipality layer
(`omavalitsus`), export date 2024-12-01. Data is © Maa-amet, published as open data with an
attribution requirement.

Download:

```text
https://geoportaal.maaamet.ee/docs/haldus_asustus/omavalitsus_shp.zip
sha256:5763714a6c7ead7146074f7741581bd78e2c311567ba1a172de98be30739d32f
```

| File | Features | CRS | Content |
| --- | ---: | --- | --- |
| `municipalities.geojson` | 79 | EPSG:4326 | All Estonian municipalities. Fields: `poly_fid` (Maa-amet municipality code `OKOOD`), `name` (`ONIMI`), `county` (`MNIMI`). |

## Processing

`tools/build_inputs.py` sorts by municipality code, applies a topology-preserving
Douglas-Peucker simplification at 50 m **in EPSG:3301**, and only then reprojects to
EPSG:4326 and writes GeoJSON with 7-decimal coordinates. Simplification takes the layer from
1037618 to 43083 vertices, keeps every one of the 2528 parts, and leaves all geometries
valid. The layer is delivered in EPSG:4326 so that the task's reprojection step is real.

## What the data discriminates

| Property | Value |
| --- | ---: |
| Municipalities | 79 |
| Multipart municipalities (islands) | 30 |
| Total parts | 2528 |
| Municipalities that do **not** contain their own centroid | 6 |

Consequences for plausible approaches:

| Approach | Outcome |
| --- | --- |
| representative / point-on-surface after reprojecting to EPSG:3301 | passes |
| grid cell centre farthest from the boundary of the largest part | passes, though it differs from the reference by up to 22 km |
| centroid | fails `within` for 6 municipalities |
| one point per part | fails `count_per_input`, 2528 points for 79 polygons |
| output left in EPSG:4326 | fails `crs` |
| interior point computed in EPSG:4326 and then reprojected | passes: all 79 points stay inside, and the reference records this rather than scoring an order of operations that does not change the answer here |
| degrees written into `x_3301` and `y_3301` | fails `field_equals_geometry` by about 400 km |

The last two rows are the honest version of the source case. The published case study failed
five of six models for computing the interior point before reprojecting; on this data that
route is harmless, because a projection is a homeomorphism and the polygons are dense enough
that the straight-segment difference never moves a representative point across a boundary.
What is genuinely scorable is the output contract: the CRS of the artifact and the meaning of
the two coordinate columns.
