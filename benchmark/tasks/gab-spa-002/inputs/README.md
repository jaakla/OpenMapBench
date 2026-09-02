# gab-spa-002 inputs

Frozen on 2026-09-02. Hospitals come from OpenStreetMap through the public Overpass API
(© OpenStreetMap contributors, ODbL-1.0); the boundary comes from the Maa-amet
"Haldus- ja asustusjaotus" municipality layer, export date 2024-12-01 (© Maa-amet, open data
with an attribution requirement).

| File | Features | CRS | Content |
| --- | ---: | --- | --- |
| `hospitals.geojson` | 16 | EPSG:3301 | `amenity=hospital` objects inside Tallinn, as points. Fields: `hosp_fid` (OSM type letter + id), `name`. |
| `boundary.geojson` | 1 | EPSG:3301 | The Tallinn municipality polygon, generalised at 50 m. Fields: `boundary_id` (`OKOOD`), `name`. |

## Overpass query

```text
[out:json][timeout:120];nwr["amenity"="hospital"](59.33,24.52,59.62,24.96);out center tags;
```

Ways and relations are taken at their Overpass `center`. `tools/build_inputs.py` keeps only
hospitals inside the Tallinn polygon and writes both layers in EPSG:3301 with centimetre
coordinates. The boundary download is

```text
https://geoportaal.maaamet.ee/docs/haldus_asustus/omavalitsus_shp.zip
sha256:5763714a6c7ead7146074f7741581bd78e2c311567ba1a172de98be30739d32f
```

## What the data discriminates

Tallinn's hospitals are clustered, which is what makes the partition consequential: all 16
have another hospital within 2000 m, there are 28 such pairs, and the closest pair is 113 m
apart. No service area is a full circle, and none is empty.

Scored against the reference contract (per-hospital IoU ≥ 0.97 and `area_m2` within 3 %):

| Approach | Result |
| --- | --- |
| Voronoi cell ∩ 1000 m circle ∩ boundary, 16 segments per quarter (reference) | passes |
| the same with the QGIS default of 5 segments | passes, worst IoU 0.987 |
| the same with 4 segments | passes, worst IoU 0.978 |
| plain 1000 m circles clipped to the boundary, no partition | fails, IoU 0.165 to 0.907 per hospital, area errors 10 % to 508 % |
| partition without the boundary clip | fails on the one hospital whose circle reaches the coast, 5.4 % of its area |

The last row is the honest limit of this task: the municipality clip changes only 2 of the
16 service areas, by 0.4 % and 5.4 %, so it is scored but barely. The partition itself is
what the task is really about, and per-entity geometry matching is what makes it scorable
at all — the union of the 16 service areas is nearly the same set with or without the
partition, so a union metric would give the substitution a passing mark.
