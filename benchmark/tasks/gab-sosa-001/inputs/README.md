# gab-sosa-001 inputs

Frozen on 2026-09-02 from the Maa-amet "Haldus- ja asustusjaotus" county layer
(`maakond`), export date 2024-12-01. Data is © Maa-amet, published as open data with an
attribution requirement.

Download:

```text
https://geoportaal.maaamet.ee/docs/haldus_asustus/maakond_shp.zip
sha256:9f853f12c2594ead4aba8a5b6cf418561e5fcc459332de1cbf88cd9d4e202dae
```

| File | Features | CRS | Content |
| --- | ---: | --- | --- |
| `saare_maakond.geojson` | 1 | EPSG:3301 | Saare maakond as one MultiPolygon of 1065 parts, 2938.5 km². Fields: `county_id` (Maa-amet county code `0074`), `name`. |

## Processing

`tools/build_inputs.py` selects `MNIMI = "Saare maakond"`, applies a topology-preserving
Douglas-Peucker simplification at 25 m, and writes GeoJSON with 2-decimal (centimetre)
coordinates in the source CRS. Simplification takes the coastline from 277530 to 13347
vertices so the frozen input stays a reviewable text file of 360 kB; it changes the county
area by 0.007 % and keeps all 1065 parts. The reference is computed from the simplified
geometry, so the generalisation is a property of the benchmark input and not an error
budget for the agent.

The input carries no deliberately invalid geometry: the simplified polygon is valid, and
the county's own topology (1065 disjoint parts, several with lakes as holes) is the
difficulty.

## What the data discriminates

Measured as the symmetric-difference ratio against the reference over the union of the
output parts:

| Approach | Symmetric-difference ratio | Parts |
| --- | ---: | ---: |
| negative buffer, 16 segments per quarter (reference) | 0 | 4 |
| negative buffer, 8 segments (GEOS default) | 0.0011 | 4 |
| negative buffer, 5 segments (QGIS default) | 0.0032 | 4 |
| negative buffer, 4 segments | 0.0052 | 4 |
| negative buffer, 32 or 64 segments | < 0.0004 | 4 |
| inset 3050 m or 2950 m | 0.0084 | 4 |
| inset 2900 m | 0.0169 | 4 |
| bevel joins | 0.0270 | 8 |
| mitre joins | 0.3829 | 12 |
| inset by 3000/111320 degrees in EPSG:4326 | 0.1203 | 4 |
| result left as one multipart feature | 0 | 1, fails `feature_count` |

The 1 hectare minimum-part rule does not change the answer on this data: the 3 km inset
leaves four parts of 737 ha and larger and nothing in between, which is deliberate. The
rule is in the contract so that `feature_count: exact` cannot be broken by an
engine-specific sliver, not because the data needs it. The consequential steps here are
the negative buffer itself, exploding the multipart result, and not confusing the 1065
input parts with the 4 output parts.
