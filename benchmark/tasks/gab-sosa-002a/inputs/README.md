# gab-sosa-002a inputs

Frozen on 2026-09-02. Rivers come from OpenStreetMap through the public Overpass API
(© OpenStreetMap contributors, ODbL-1.0); the municipalities come from the Maa-amet
"Haldus- ja asustusjaotus" municipality layer, export date 2024-12-01 (© Maa-amet, open data
with an attribution requirement). `benchmark/tasks/gab-sosa-002b/inputs/` holds byte-identical
copies of both files; the pair shares one analysis and scores two different artifacts.

| File | Features | CRS | Content |
| --- | ---: | --- | --- |
| `rivers.geojson` | 593 | EPSG:4326 | `waterway=river` ways within 2000 m of the four municipalities, simplified at 5 m. Fields: `source_fid` (`w` + OSM way id), `name`. 892.8 km in total. |
| `municipalities.geojson` | 4 | EPSG:3301 | Pärnu linn, Tori vald, Saarde vald and Põhja-Pärnumaa vald, generalised at 50 m. Fields: `muni_id` (`OKOOD`), `name`. Two geometries are invalid by construction. |

## Overpass query and download

```text
[out:json][timeout:180];way["waterway"="river"](58.10,23.90,58.95,25.30);out geom;

https://geoportaal.maaamet.ee/docs/haldus_asustus/omavalitsus_shp.zip
sha256:5763714a6c7ead7146074f7741581bd78e2c311567ba1a172de98be30739d32f
```

`tools/build_inputs.py` simplifies the rivers in EPSG:3301 and writes them back in
EPSG:4326, which is the CRS the clipped output has to keep, and generalises the
municipalities at 50 m in their own CRS.

## The deliberate topology defect

Tori vald and Saarde vald each get one extra part: an hourglass, a ring that touches itself
at its waist, 800 m across, sitting on a river just outside the municipality and disjoint
from every other ring in the layer. Both layers therefore fail any validity check.

The defect is built that way on purpose. A ring nested **inside** another ring would also be
invalid, but `make_valid` repairs it to a hole while a zero-width buffer repairs it to filled
area, and a benchmark answer must not depend on which repair the agent reaches for. With the
parts disjoint, `make_valid`, `buffer(0)` and a self-union all return the same region;
`tools/build_inputs.py` asserts exactly that before writing the file.

## What the data discriminates

509 of the 593 rivers reach the municipalities, 63 cross their boundary, and the clip takes
892.8 km down to 641.3 km over 510 output features.

| Approach | Outcome |
| --- | --- |
| repair, union, clip in EPSG:3301, write in EPSG:4326 (reference) | passes |
| clip in EPSG:4326 instead, everything else the same | passes: a clipped end moves by at most 1.44 m, which both tolerances absorb |
| write the clipped lines in EPSG:3301, the CRS the analysis ran in | fails `crs` — this is the failure the source case reports, honoured by 1 model in 6 |
| drop the two invalid municipalities instead of repairing them | fails: 282 of the 510 rivers change length, and the feature count changes |
| use the invalid polygons unrepaired | on GEOS 3.13 this returns the same answer; the defect is an operational hazard for toolchains that validate their input, not a difference in the numbers, and the task says so rather than claiming otherwise |
| skip the clip and return the input rivers | fails on 63 rivers by hundreds of metres, and on the feature count |
