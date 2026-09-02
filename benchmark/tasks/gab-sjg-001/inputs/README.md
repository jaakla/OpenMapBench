# gab-sjg-001 inputs

Frozen on 2026-09-02 from OpenStreetMap through the public Overpass API. Data is
© OpenStreetMap contributors, licensed under the Open Database License 1.0 (ODbL-1.0).

| File | Features | CRS | Content |
| --- | ---: | --- | --- |
| `origins.geojson` | 443 | EPSG:4326 | `highway=bus_stop` nodes in the Tartu bounding box (S 58.33, W 26.66, N 58.42, E 26.80). Fields: `stop_id` (OSM node id), `name`, `shelter`. |
| `destinations.geojson` | 89 | EPSG:4326 | 86 `amenity=school` or `amenity=kindergarten` objects in the same box, as points (ways and relations at their Overpass `center`), plus 3 synthetic probes. Fields: `dest_id` (OSM type letter + id, or `probeN`), `name`, `amenity`, `synthetic`. |

## Overpass queries

```text
[out:json][timeout:90];node["highway"="bus_stop"](58.33,26.66,58.42,26.80);out body;
[out:json][timeout:90];nwr["amenity"~"^(school|kindergarten)$"](58.33,26.66,58.42,26.80);out center tags;
```

`tools/build_inputs.py` converts the raw responses into these files deterministically
(sorted by OSM id, coordinates rounded to 9 decimals).

## Synthetic probes

Real destinations rarely fall close to a 1-mile threshold, so three probe destinations were
placed relative to stop `2320090479` (Ropkamõisa) at exact EPSG:3301 planar distances:

| dest_id | Distance | Bearing | Inside 1609.344 m |
| --- | ---: | ---: | --- |
| probe1 | 1600.5 m | 30° | yes |
| probe2 | 1605.0 m | 150° | yes |
| probe3 | 1609.7 m | 270° | no |

They carry `synthetic: yes` and an explanatory name. They guarantee that the unit conversion
changes the answer for at least one stop even if the real data did not. The probes are part
of the input and must be counted like any other destination.

## What the data discriminates

Measured against the reference over all 443 stops (real pairs come as close as 0.12 m to the
threshold; 30 real stop–destination pairs lie within 2 m of it):

| Approach | Stops with a wrong count |
| --- | ---: |
| exact planar distance in EPSG:3301, 1609.344 m | 0 |
| threshold 1609 m | 4 |
| threshold 1610 m | 5 |
| threshold 1600 m (1.6 km) | 67 |
| haversine on a 6371 km sphere in EPSG:4326 | 37 |
| polygonal buffer, 64 segments per quarter circle | 0 |
| polygonal buffer, 16 segments (GeoPandas/Shapely default) | 13 |
| polygonal buffer, 5 segments (QGIS default) | 89 |

The prompt asks for planar distances, and "within 1 mile" is a distance predicate. A distance
join (`dwithin`, `ST_DWithin`, QGIS "join attributes by location" with a distance, nearest with
a maximum distance) is exact. A buffer polygon is an approximation whose edge lies inside the
true circle by up to r·(1 − cos(π/4n)) for n segments per quarter, and this data contains
enough near-threshold pairs to expose it. That is the boundary-ambiguity pitfall the task is
tagged with.
