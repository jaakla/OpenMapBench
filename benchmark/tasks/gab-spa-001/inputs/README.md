# gab-spa-001 inputs

Frozen on 2026-09-02 from OpenStreetMap through the public Overpass API. Data is
© OpenStreetMap contributors, licensed under the Open Database License 1.0 (ODbL-1.0).

| File | Features | CRS | Content |
| --- | ---: | --- | --- |
| `parks.geojson` | 177 | EPSG:3301 | 173 `leisure=park` closed ways reaching the task's grid extent, plus 4 synthetic probes. Fields: `park_id` (`w` + OSM way id, or `probe_*`), `name`, `synthetic`. Total area 5.18 km². |

## Overpass query

```text
[out:json][timeout:120];way["leisure"="park"](59.33,24.52,59.62,24.96);out geom;
```

`tools/build_inputs.py` keeps closed ways only, repairs any self-intersection with a zero
buffer, reprojects to EPSG:3301, snaps coordinates to the centimetre, drops parks under
100 m² and parks that do not reach the grid extent, and appends the probes. Relations are
not read, so a handful of multipolygon parks are absent; the layer is the task's universe,
not a claim about Tallinn's parks.

## Synthetic probes

Real parks almost never align with an arbitrary grid, so four probes make the two rules in
the prompt consequential. All are in EPSG:3301 metres and carry `synthetic: yes`.

| park_id | Geometry | What it discriminates |
| --- | --- | --- |
| `probe_edge` | square 540000–540400 × 6588200–6588600 | its west edge lies exactly on the grid line x = 540000, so it touches the cell to the west with zero overlap area |
| `probe_corner` | square 541000–541300 × 6589000–6589300 | its south-west corner is exactly the grid node (541000, 6589000), which it shares with three cells it does not overlap |
| `probe_multipart_one_cell` | 3 squares inside one cell | counting parts instead of parks reports 3 |
| `probe_multipart_spanning` | 4 squares over 3 cells, two of them in the same cell | a park spanning cells still counts once per cell, and the shared cell exposes part counting |

## What the data discriminates

Measured against the reference over all 616 cells:

| Approach | Cells with a wrong count |
| --- | ---: |
| distinct parks with a positive intersection area (reference) | 0 |
| any touching park counts (`intersects` join) | 4 |
| parts counted instead of parks (explode before the join) | 2 |
| a grid offset by half a cell | 616 (also fails the geometry check) |

The smallest positive park-cell overlap in the data is 0.32 m², so "positive area" is a
robust rule here rather than a floating-point coin flip: real overlaps are square metres or
larger, and the deliberate edge and corner contacts are exactly zero.

101 of the 616 cells contain at least one park and the busiest cell contains 16, so a run
that silently returns only the non-empty cells fails `feature_count` rather than quietly
scoring well.
