# gab-tmha-001 inputs

Frozen on 2026-09-02 from OpenStreetMap through the public Overpass API. Data is
© OpenStreetMap contributors, licensed under the Open Database License 1.0 (ODbL-1.0).

| File | Features | CRS | Content |
| --- | ---: | --- | --- |
| `network.geojson` | 791 | EPSG:4326 | Watercourse centrelines on the lower Emajõgi and the Peipsi shore: 340 ditches, 254 river segments, 181 streams, 16 drains. Fields: `orig_fid` (`w` + OSM way id), `waterway`, `name`. 529.5 km in total. |

## Overpass query

```text
[out:json][timeout:180];
way["waterway"~"^(ditch|drain|stream|canal|river)$"](58.40,26.80,58.80,27.20);out geom;
```

Nothing is simplified. The shared OSM nodes are what make this layer a graph rather than a
pile of lines, and a generalisation that moved them would change the topology the task is
about. Coordinates are written with 7 decimals, so two ways that share a node still share it
exactly after rounding. `tools/build_inputs.py` checks that no two lines overlap along a
curve (measured collinear overlap: 0.000 m), which would make "split where lines meet"
ambiguous.

## What the data discriminates

The planarized network has 1063 segments and encloses 87 islands covering 107.9 ha. 228
segments bound an island, 17.71 km of the 529.54 km total, and 114 of the 791 input lines
have a braided part.

| Approach | Multichannel length | Rows wrong |
| --- | ---: | ---: |
| planarize, polygonize, segment covered by an island boundary (reference) | 17.71 km | 0 |
| count a segment that merely touches an island | 53.41 km | 28 |
| skip the planarisation and classify whole input lines | 9.80 km | 22 |

The first wrong row is the "aggregating join versus plain join" substitution the source case
reports, in its geometric form: `intersects` is the wrong predicate here and `covers` is the
right one, and the difference is a factor of three in the answer.

Because the lines meet at shared vertices rather than at computed crossings, planarizing in
EPSG:4326 and measuring in EPSG:3301 gives exactly the same lengths as planarizing in
EPSG:3301, to the last digit measured. The reprojection is still needed for the lengths
themselves: measured in degrees they are out by five orders of magnitude.
