# Candidate tasks from the GISAgentBench case studies

Source: Pothuri, Jiang, Xu, Yang. *GISAgentBench: A Practitioner-Sourced Benchmark for
Evaluating LLM Agents on GIS Tasks*. arXiv:2608.01645v1, 3 Aug 2026, CC BY 4.0.
<https://arxiv.org/html/2608.01645v1>

This note reinterprets the ten worked cases in the paper's Appendix I as candidate native
OpenMapBench tasks (roadmap Issue 8). Nothing from the paper's datasets or references is copied.
Each case is geographically recast onto Estonian open data in EPSG:3301, following the paper's own
argument that recasting removes pretraining leakage while keeping the analytical question intact.

## What the paper is, in one paragraph

GISAgentBench is 349 multi-step GIS tasks mined from GIS Stack Exchange threads, recast onto six
areas of interest (SF Bay, Randstad, NYC, Denver Front Range, Houston, South Florida), and solved
by a fixed harness of 128 QGIS/GDAL/GeoPandas tools. Every task ships an output contract (file,
columns, CRS, tolerances) and an executable reference trajectory. Scoring is deterministic and
artifact-based: strict pass/fail on structure plus per-value tolerance (1e-6 absolute, 0.01 %
relative), with a separate continuous closeness score. No LLM judge is used and trajectories are
never part of the score. Six frontier models were evaluated; the best reached a strict success
rate of 0.327 and the six-model mean was 0.238. That scoring philosophy is essentially
OpenMapBench's "score the result, not the tool trajectory", which is why the cases transfer well.

## What the case studies teach about failure

The appendix pairs one success and one failure per task family. The failures are the interesting
part, and they cluster into a few mechanisms that a native task set should deliberately exercise:

- **Order of operations with a CRS change.** Computing an interior point, a slope, or a
  polygonization before reprojecting yields a different, plausible-looking result. The paper's
  failure taxonomy puts "operation order violation" at 18 % of failed runs, most often resampling
  a raster after deriving slope from it.
- **Contract literalism.** "Output in the same CRS as the input layer" was honoured by one model
  in six. Every other output was analytically right and structurally wrong.
- **Silent step omission.** Polygonizing a landcover raster without warping it first passed every
  tool call and failed every model; the output simply inherited EPSG:4326.
- **Composite-tool substitution.** A single "do it all" tool that sounds right (clip point
  buffers to containing polygons) does not reproduce a Voronoi partition. "Wrong tool
  substitution" is 13 % of failures, typically a plain spatial join where an aggregating one was
  needed.
- **Geometry hygiene.** A clip fails with a topology exception until geometries are fixed; the
  agent may recover or may burn 34 calls. Tasks tagged with geometry/topology caveats have the
  lowest pooled success (0.152).

The successes are instructive too: in every success case the agent took a *shorter, structurally
different* route than the reference (an aggregating spatial join instead of overlay + frequency
+ join). A benchmark that scored trajectories would have penalised correct answers. OpenMapBench
already avoids this; the candidate contracts below are written so that any correct route passes.

## Cross-cutting design rules taken from the paper

1. **Name the trap, do not hide it in the prompt.** The paper tags every task with practitioner
   caveats: CRS misalignment, boundary ambiguity, geometry/topology errors, NoData, unit
   mismatch. Record the same tags under `metadata.failure_modes` so reports can break down
   success by caveat, as the paper does.
2. **Keep a "no-caveat control" in each family.** The landcover polygonization was such a control
   and still had 0/6 success; controls calibrate whether difficulty comes from the trap or from
   the family.
3. **Make the contract decide every ambiguity the score depends on.** Grid origin, connectivity
   (4 or 8), boundary inclusion, sampling method, slope algorithm, resampling method. If the
   contract is silent, two correct agents will produce two different references.
4. **Prefer keyed aggregates over entity-per-segment outputs.** Planarized or polygonized outputs
   have no stable key. Summarising them per source feature ID keeps the task deterministic and
   scorable with the existing table evaluator.
5. **Justify tolerances by mechanism.** Buffer arc approximation, slope kernel choice, and
   raster resampling each have a characteristic error size; state it.

## Candidate tasks

Each candidate lists the source case, the Estonian recast, the trap it keeps, a draft contract
fragment, tolerance rationale, and any evaluator gap. IDs use the `gab-` prefix so they can be
traced back to this note. Input datasets are proposals; frozen copies, checksums, licenses, and
acquisition dates still have to be produced per CONTRIBUTING.md.

Suggested Estonian sources (all open):

- Maa-amet administrative units (maakond, omavalitsus, asustusüksus), EPSG:3301, Maa-amet
  open-data licence.
- ETAK topographic database: shoreline, watercourses and ditches, green areas.
- Maa-amet 10 m DEM and 4-band (R, G, B, NIR) orthophoto tiles; Copernicus GLO-30 DEM in
  EPSG:4326 when a geographic-CRS raster is needed for the trap.
- ESA WorldCover 10 m (EPSG:4326, CC BY 4.0) for landcover.
- Estonian address data system (ADS) points; national public transport stop register
  (peatus.ee GTFS); EHIS school register; Terviseamet or OSM hospitals.

---

### gab-sjg-001 — Count destinations within one mile of each origin

**Implemented** in `benchmark/tasks/gab-sjg-001/` with frozen OpenStreetMap data for Tartu and
a cross-checked reference; the contract there supersedes the draft below.

Source: `counting_destinations_within_one_mile_of_origins` (Spatial Joining & Geocoding, success,
6/6 models).

Recast: origins are public transport stops in Tartu, destinations are schools and kindergartens.
Count destinations within **1 mile** of each stop; points exactly at the boundary count. Inputs
are delivered in EPSG:4326 to keep the CRS caveat; the prompt asks for measurement in EPSG:3301.

Trap kept: unit mismatch (mile stated, metres required) and CRS misalignment. Boundary
inclusion is stated explicitly.

```yaml
id: gab-sjg-001
category: vector
output:
  path: stops_with_counts.gpkg
  kind: vector
  geometry_type: Point
  crs: EPSG:3301
  required_fields: [stop_id, dest_cnt_1mi]
evaluation:
  strict:
    crs: exact
    feature_count: exact
    geometry: {metric: hausdorff_distance, tolerance: 0.5, comparison_crs: "EPSG:3301"}
    attributes:
      key: stop_id
      columns: [stop_id, dest_cnt_1mi]
      numeric_tolerance: {dest_cnt_1mi: {absolute: 0, relative: 0}}
metadata:
  failure_modes: [unit_mismatch, crs_misalignment, boundary_inclusion]
  difficulty: easy
```

Tolerance: counts are integers, zero tolerance. Hausdorff 0.5 m absorbs datum-transformation
noise between EPSG:4326 and EPSG:3301 while still rejecting an unprojected output. Include at
least one destination placed exactly 1609.344 m from a stop in the frozen inputs so boundary
handling is actually scored.

---

### gab-sjg-002 — Interior point per polygon with projected coordinates

**Implemented** in `benchmark/tasks/gab-sjg-002/` over all 79 Estonian municipalities; the
contract there supersedes the draft below. Building it settled the open question in this
section: on real data the "interior point before reprojection" route is *not* wrong, because a
projection is a homeomorphism and all 79 naive points stay inside their municipality. The
implemented task scores what is genuinely scorable (the output CRS and the meaning of the
coordinate columns, a 400 km error when degrees are written) and records the measurement.

Source: `creating_interior_points_with_projected_coordinates` (Spatial Joining & Geocoding,
failure: interior point computed before reprojection).

Recast: one guaranteed-interior point per Estonian municipality polygon (several are concave or
multipart because of islands and lakes), with fields `poly_fid`, `x_3301`, `y_3301` rounded to
2 decimals. Input polygons delivered in EPSG:4326.

Trap kept: order of operations around reprojection.

Why this needs care: the paper scored the reference's specific point. Two correct agents can pick
different interior points, and both satisfy the practitioner's question. Scoring against a
reference point would reproduce the paper's questionable failure rather than a real one. The
honest contract is predicate-based:

- every output point lies strictly inside the polygon with the same `poly_fid`;
- `x_3301`/`y_3301` equal the point's own geometry coordinates within 0.01 m;
- one point per input polygon, CRS EPSG:3301.

```yaml
output:
  path: municipality_points.gpkg
  kind: vector
  geometry_type: Point
  crs: EPSG:3301
  required_fields: [poly_fid, x_3301, y_3301]
evaluation:
  strict:
    crs: exact
    geometry: {metric: ignore}
    vector_checks:
      - {type: within, input_role: municipalities, key: poly_fid, input_key: fid}
      - {type: count_per_input, input_role: municipalities, key: poly_fid, input_key: fid}
      - {type: field_equals_geometry, x: x_3301, y: y_3301, tolerance: 0.01, crs: EPSG:3301}
metadata:
  failure_modes: [crs_misalignment, operation_order]
  difficulty: easy
```

The reference artifact is still passed to the runner but only its structure is used; any
interior point passes, and a point computed before reprojection fails `within` whenever the
unprojected representative point drifts outside a concave polygon.

---

### gab-sosa-001 — Inward inset of a county boundary

**Implemented** in `benchmark/tasks/gab-sosa-001/` with the Maa-amet county layer for Saare
maakond and a grid-verified reference; the contract there supersedes the draft below, and its
geometry tolerance is 0.006 rather than 0.002 because the measured spread across plausible
buffer segmentations is larger than the draft assumed.

Source: `creating_inland_county_boundary_inset` (Spatial Overlay & Suitability, success, 6/6).

Recast: Saare maakond (Saaremaa and surrounding islands). Inset the county polygon 3000 m
inward, drop empty or invalid parts, explode to single parts, drop parts under 1 ha, attach
`orig_fid` and `inset_m = 3000`. The islands make the multipart-to-singlepart step and the
dropping of small islands consequential.

```yaml
output:
  path: county_inset.gpkg
  kind: vector
  geometry_type: Polygon
  crs: EPSG:3301
  required_fields: [orig_fid, inset_m]
evaluation:
  strict:
    crs: exact
    feature_count: exact
    geometry: {metric: symmetric_difference_ratio, tolerance: 0.002, require_valid: true}
    attributes:
      key: null
      columns: [inset_m]
metadata:
  failure_modes: [geometry_topology, multipart_handling]
  difficulty: easy
```

Tolerance: a negative buffer of 3 km on a long coastline is approximated with circular arcs;
segment count differences between engines produce area deltas on the order of 0.1 % of the
result, so 0.002 leaves headroom without accepting a wrong distance (a 2500 m inset differs by
several percent). The 1 ha minimum-part rule exists solely to make `feature_count: exact`
robust to engine-specific slivers. Attribute comparison needs a unique key; single parts have
none, so only the constant column is checked. If the frozen inputs assign a stable part
identifier (for example the island name), switch to `match: entity` on that key.

---

### gab-sosa-002a / 002b — Clip shoreline to municipalities, preserve input CRS

**Implemented** in `benchmark/tasks/gab-sosa-002a/` and `.../gab-sosa-002b/`, with two
departures from the draft below, both forced by measurement. The line layer is the river
network of four Pärnu county municipalities rather than the shoreline, because an OSM coastline
runs *along* the municipality boundary and clipping it would score dataset registration noise
instead of the operation. And the topology defect is a self-touching ring in a part disjoint
from every other ring, not a nested one: a nested ring is repaired to a hole by `make_valid`
and to filled area by `buffer(0)`, which would make the intended answer depend on the repair
tool. Tolerances are 3 m Hausdorff and 1.5 m / 0.4 % on length, both set by the measured cost
of clipping in EPSG:4326 rather than EPSG:3301.

Source: `clipping_shoreline_to_boroughs` (Spatial Overlay & Suitability, failure: only 1/6
preserved the input CRS; also a TopologyException until geometries were fixed).

Recast: a shoreline line layer delivered in **EPSG:4326** (for example OSM coastline for the
Pärnu Bay area) and municipality polygons in EPSG:3301, with one or two deliberately invalid
polygons (self-touching rings) so the clip fails until fixed. Clip the shoreline to the polygons,
measure clipped length per original shoreline feature in EPSG:3301, and write the clipped lines
**in the same CRS as the input shoreline layer**. The prompt should say exactly that and not name
EPSG:4326.

OpenMapBench tasks have one output, so split:

- **002a** vector: clipped lines, `crs: exact` with `output.crs: EPSG:4326`, Hausdorff tolerance
  1 m in comparison CRS EPSG:3301. This task is the CRS-preservation check.
- **002b** table: `source_fid, length_m` CSV, keyed, `length_m` tolerance absolute 0.05 m
  relative 0.0001. This task is the analytical check.

```yaml
metadata:
  failure_modes: [crs_preservation, geometry_topology, unrecovered_tool_failure]
  difficulty: medium
```

Tolerance: length rounding to 2 dp gives 0.005 m; 0.05 m absorbs projection-round-trip noise on
lines a few kilometres long. The relative term rejects lengths computed in degrees or in the wrong
CRS by orders of magnitude.

---

### gab-tmha-001 — Multichannel classification of a drainage network

**Implemented** in `benchmark/tasks/gab-tmha-001/` on the lower Emajõgi and Peipsi shore
network (791 lines, 87 enclosed islands) as the keyed aggregate this section proposes; the
contract there supersedes the draft below. "Touches" turned out to be the wrong predicate to
write into the contract: counting a segment that merely touches an island triples the answer,
so the implemented prompt requires the island boundaries to *cover* the segment.

Source: `classifying_multichannel_canal_segments` (Terrain Modeling & Hydrology, success, 6/6).

Recast: ETAK ditch and watercourse lines in a polder or delta landscape (Kasari delta or a
drained bog with parallel ditches). Planarize at all intersections, polygonize the linework to
find island polygons, and mark every segment that touches an island as multichannel.

Reinterpretation: planarized segments have no stable key, so the primary artifact becomes a
keyed aggregate per source line: `orig_fid, multich_len_m, single_len_m, total_len_m`. This is
exactly the practitioner's question (how much of each canal is braided) and it is scorable with
the table evaluator today. A companion vector output can be kept as a diagnostic.

```yaml
output: {path: canal_classes.csv, kind: table}
evaluation:
  strict:
    key: orig_fid
    required_columns: [orig_fid, multich_len_m, single_len_m, total_len_m]
    numeric_tolerance:
      default: {absolute: 0.05, relative: 0.0001}
metadata:
  failure_modes: [wrong_tool_substitution, geometry_topology]
  difficulty: medium
```

The contract must define "island polygon" (positive-area face enclosed by the planarized
linework) and "touches" (shares any point, boundary included) so that a plain spatial join and an
aggregating join give the same lengths.

---

### gab-tmha-002 — Polygons of steep slope from a DEM

**Implemented** in `benchmark/tasks/gab-tmha-002/` with the companion scalar task
`benchmark/tasks/gab-tmha-002s/`, on a Copernicus GLO-30 window over the Haanja upland; the
contracts there supersede the draft below. The draft's guess of 0.02 was too tight, and the
reason is worth recording: GDAL's warper and a direct bilinear interpolation at the target
cell centres are both correct readings of "resample bilinearly, then take Horn's slope", and
they differ by 0.0376 in symmetric difference (289 regions over 754.6 ha against 294 over
756.5 ha). The implemented tolerance is 0.06, measured rather than guessed, and the wrong
order still fails at 0.4033.

Source: `polygonizing_steep_slope_areas` (Terrain Modeling & Hydrology, failure, 0/6; an
inserted clump step changed the result and no model matched the reference).

Recast: Haanja upland. Provide Copernicus GLO-30 in EPSG:4326 and require slope in percent on a
10 m EPSG:3301 grid, so the resample-before-slope order matters. Threshold at 20 %, group
contiguous cells, polygonize, attach `slope_pct_max` and `area_m2`.

The paper's 0/6 outcome is partly a reference artefact: the contract did not say whether
contiguity is 4- or 8-connected, which slope kernel to use, or how to resample. Fix all three in
the prompt: bilinear resampling to 10 m, Horn's method, 8-connected regions, drop regions under
0.5 ha.

```yaml
output:
  path: steep_slopes.gpkg
  kind: vector
  geometry_type: MultiPolygon
  crs: EPSG:3301
  required_fields: [slope_pct_max, area_m2]
evaluation:
  strict:
    crs: exact
    feature_count: ignore
    geometry: {metric: symmetric_difference_ratio, tolerance: 0.02, require_valid: true}
metadata:
  failure_modes: [operation_order, crs_misalignment, nodata, boundary_ambiguity]
  difficulty: hard
```

Tolerance: even with the kernel fixed, resampling-kernel and edge-handling differences move
cells that sit near the 20 % threshold. On a 10 m grid over a few km² of upland the affected
band is one cell wide around every region, which empirically is a few percent of steep area;
0.02 is the tightest value that does not fail a correct GDAL-versus-QGIS pairing. Computing slope
on the 30 m source and resampling afterward is smoother by construction and should fall well
outside 0.02; verify this while producing the reference and record the observed value. Per-polygon
attributes cannot be keyed, so `slope_pct_max` and `area_m2` are checked only through the
optional companion scalar output below.

Companion `gab-tmha-002s` (scalar/JSON): `{steep_area_m2, region_count, max_slope_pct}` with
relative tolerance 0.02 on area and 0.01 on slope; exact `region_count` is deliberately not
scored.

---

### gab-spa-001 — Count distinct parks intersecting each 1 km grid cell

**Implemented** in `benchmark/tasks/gab-spa-001/` with OpenStreetMap parks for Tallinn on a
22 x 28 km grid and four synthetic probes on the grid lines; the contract there supersedes the
draft below.

Source: `counting_park_overlaps_in_grid_cells` (Spatial Pattern Analysis, success, 4/6).

Recast: Tallinn green areas (ETAK) over a 1 km grid. The grid must be pinned: origin at the
lower-left corner of a stated EPSG:3301 extent (round-thousand coordinates), `grid_id = row *
ncols + col`, rows counted from the south. Count a park once per cell regardless of how many
polygons it has; a park that only touches a cell edge does not count (interior intersection
area must be positive). Cells with no parks have `park_count = 0`.

```yaml
output:
  path: park_grid.gpkg
  kind: vector
  geometry_type: Polygon
  crs: EPSG:3301
  required_fields: [grid_id, park_count]
evaluation:
  strict:
    crs: exact
    feature_count: exact
    geometry: {metric: symmetric_difference_ratio, tolerance: 0.0001}
    attributes:
      key: grid_id
      columns: [grid_id, park_count]
      numeric_tolerance: {park_count: {absolute: 0, relative: 0}}
metadata:
  failure_modes: [boundary_ambiguity, multipart_handling, wrong_tool_substitution]
  difficulty: medium
```

Tolerance: the grid is fully specified, so geometry should match to floating-point noise; counts
are exact. Freeze inputs with at least one multipart park spanning several cells and one park
whose edge coincides with a grid line. A plain (non-distinct) join over-counts multipart parks,
which is the substitution error the paper reports.

---

### gab-spa-002 — Voronoi-clipped 1 km hospital service areas

**Implemented** in `benchmark/tasks/gab-spa-002/` with 16 clustered OpenStreetMap hospitals in
Tallinn, scored per hospital with `geometry: {metric: iou, match: entity}` at 0.97 as the draft
anticipated; the contract there supersedes the draft below.

Source: `sf_bay_voronoi_clipped_hospital_buffers_1_km` (Spatial Pattern Analysis, failure, 1/6;
a composite clip-buffers-to-containing-polygons tool replaced the Voronoi step).

Recast: hospitals in Tallinn and Harju maakond. For each hospital, output the intersection of its
1000 m circle and its Voronoi cell bounded by the dissolved municipality extent. Fields
`hosp_fid`, `buffer_rad = 1000`.

Why the current evaluator is not enough: the union of the outputs equals the union of the plain
buffers clipped to the extent, so `symmetric_difference_ratio` over the union cannot see whether
the Voronoi partition happened at all. Add a per-hospital area check so partition errors are
scored:

```yaml
output:
  path: hospital_cells.gpkg
  kind: vector
  geometry_type: Polygon
  crs: EPSG:3301
  required_fields: [hosp_fid, buffer_rad, area_m2]
evaluation:
  strict:
    crs: exact
    feature_count: exact
    geometry: {metric: symmetric_difference_ratio, tolerance: 0.002}
    attributes:
      key: hosp_fid
      columns: [hosp_fid, buffer_rad, area_m2]
      numeric_tolerance: {area_m2: {absolute: 0, relative: 0.005}}
metadata:
  failure_modes: [wrong_tool_substitution, geometry_topology]
  difficulty: hard
```

Tolerance: 0.002 on the union and 0.5 % per-feature area both come from circular-buffer
segmentation (a 1000 m circle with 16 versus 64 segments per quarter differs by roughly 0.2 % in
area). Overlapping hospitals closer than 2 km must exist in the frozen inputs, otherwise the
Voronoi step is a no-op. With `geometry: {metric: iou, tolerance: 0.98, match: entity}` the partition is scored
directly per `hosp_fid`, and the `area_m2` column becomes a secondary check.

---

### gab-rsia-001 — NDVI at address points from a 4-band orthophoto

**Implemented** in `benchmark/tasks/gab-rsia-001/` with a Sentinel-2 window over Tartu rather
than a Maa-amet orthophoto, because the Sentinel-2 archive gives a citable scene, a small
frozen file and a real near-infrared band; the contract there supersedes the draft below. The
draft's ADS points are OpenStreetMap address nodes, and the NoData clause needed a deliberate
NoData block, since a cloud-free 3 km window contains none.

Source: `extract_ndvi_values_at_randstad_address_points_from_cir_orthophoto` (Remote Sensing &
Image Analysis, success, 3/6).

Recast: Maa-amet 4-band orthophoto tile (band order R, G, B, NIR must be stated in the prompt and
verified on the frozen file) and ADS address points delivered in EPSG:4326. Sample the pixel
under each point using nearest-pixel sampling, compute NDVI = (NIR − R) / (NIR + R), set NDVI to
null when either band is NoData or when NIR + R = 0, round to 4 decimals, keep all original
attributes.

```yaml
output:
  path: addresses_ndvi.gpkg
  kind: vector
  geometry_type: Point
  crs: EPSG:3301
  required_fields: [adr_id, ndvi]
evaluation:
  strict:
    crs: exact
    feature_count: exact
    geometry: {metric: hausdorff_distance, tolerance: 0.5, comparison_crs: "EPSG:3301"}
    attributes:
      key: adr_id
      columns: [adr_id, ndvi]
      numeric_tolerance: {ndvi: {absolute: 0.0002, relative: 0}}
metadata:
  failure_modes: [crs_misalignment, nodata, band_order, operation_order]
  difficulty: medium
```

Tolerance: 4-dp rounding contributes 0.00005; the rest of the 0.0002 budget covers computing
in float32 versus float64. The paper's success case computed NDVI after sampling rather than
before, which is fine here because nearest-pixel sampling makes both orders identical; if
bilinear sampling were allowed they would differ, so the prompt fixes nearest. Include address
points on the tile edge (NoData) and on a saturated water pixel where NIR + R can be 0.

Attribute comparison is null-aware: null equals null and never equals 0, so the NoData clause is
scored.

---

### gab-rsia-002a / 002b — Vectorize landcover classes (8-connected)

**Implemented** in `benchmark/tasks/gab-rsia-002a/` and `.../gab-rsia-002b/` on an ESA
WorldCover window over Ruhnu; the contracts there supersede the draft below. Two refinements
came out of building it: 002a scores geometry per class with `match: entity` rather than over
the union, since the union really is just the raster extent; and the contract has to say that
diagonally self-touching regions must be written as valid geometry, because 41 of the 360
regions are invalid as single rings.

Source: `vectorize_randstad_landcover_raster_classes_into_polygons` (Remote Sensing & Image
Analysis, failure, 0/6; every model skipped the reprojection).

Recast: ESA WorldCover 10 m clipped to one rural municipality, delivered in EPSG:4326. Produce
one polygon per 8-connected region of equal value, field `raster_val`, output EPSG:3301.

Order ambiguity must be settled by the contract, because warping the raster first resamples it
and changes region boundaries. Prefer "polygonize on the native grid, then reproject the
polygons": it involves no resampling, so the reference is unique, and the CRS trap is retained
because the output must still end up in EPSG:3301.

The union of all class polygons is just the municipality, so the vector union metric scores
nothing useful. Split:

- **002a** vector: `crs: exact`, `geometry_type: Polygon`, `required_fields: [raster_val]`,
  `feature_count: exact` (8-connected versus 4-connected region counts differ substantially and
  this is the point of the task), geometry tolerance 0.001 on the union as a sanity check.
- **002b** table keyed by `raster_val`: `region_count, area_m2` with `area_m2` relative tolerance
  0.001 and `region_count` exact. This is the discriminating artifact; it is cheap to produce
  from 002a and it isolates the connectivity decision from the CRS decision.

```yaml
metadata:
  failure_modes: [crs_misalignment, missing_operation, connectivity]
  difficulty: easy
  control: true
```

This pair is the family's "no-caveat control" in the paper's sense: the only trap is the one the
prompt states outright.

---

## Summary table

| ID | Family | Source outcome | Trap kept | Output kind | Scorable today |
|---|---|---|---|---|---|
| gab-sjg-001 | Join | pass 6/6 | unit + CRS + boundary | vector | yes |
| gab-sjg-002 | Join | fail | order around reprojection | vector | yes, via vector_checks |
| gab-sosa-001 | Overlay | pass 6/6 | topology, multipart | vector | yes |
| gab-sosa-002a/b | Overlay | fail 1/6 | preserve input CRS, invalid geometry | vector + table | yes |
| gab-tmha-001 | Terrain | pass 6/6 | aggregating join vs plain join | table | yes |
| gab-tmha-002(+s) | Terrain | fail 0/6 | resample before slope, connectivity | vector + scalar | yes, loose tolerance |
| gab-spa-001 | Pattern | pass 4/6 | boundary inclusion, multipart | vector | yes |
| gab-spa-002 | Pattern | fail 1/6 | composite tool substitution | vector | yes, entity matching |
| gab-rsia-001 | Remote sensing | pass 3/6 | band order, NoData, CRS | vector | yes |
| gab-rsia-002a/b | Remote sensing | fail 0/6 | skipped reprojection, connectivity | vector + table | yes |

None of the ten requires a raster output artifact, so all are within reach of the current
scalar, table, and vector evaluators. This holds for the paper's raster families too: their
tasks end in points, polygons, or tables.

## Evaluator gaps surfaced by this exercise

These were gaps when this note was first written; all but the last are now implemented and
documented in `docs/task-contract.md`.

1. **Per-entity geometry matching.** Done: `geometry.match: entity` groups by key, unions the
   parts of each entity, applies the metric per entity, and reports entity precision, recall, and
   F1, mirroring the paper's entity-level scoring. gab-sosa-001 and gab-spa-002 use it.
2. **Reference-independent vector predicates.** Done: `vector_checks` with `within` and other
   relations, `count_per_input`, `field_equals_geometry`, `field_equals_measure`, `field_range`,
   and `unique`, plus `geometry.metric: ignore`. gab-sjg-002 is now scorable.
3. **Null semantics in attribute comparison.** Done: null equals null and never a value, in
   tables and vector attributes.
4. **Multi-artifact tasks.** Open. Splitting into paired tasks works but doubles run cost; an
   `outputs:` list with per-artifact strict blocks would be cleaner and is a task-contract change
   rather than an evaluator change.
5. **Caveat and failure-mode reporting.** Done: manifests keep `task_metadata`, and reports
   group strict success by `metadata.failure_modes`. The audit trail remains the basis for
   labelling the paper's mechanism taxonomy for roadmap Issue 7 without scoring trajectories.

## Next steps

1. Pick the five that are scorable today with no evaluator change (gab-sjg-001, gab-sosa-001,
   gab-sosa-002a/b, gab-tmha-001, gab-spa-001, gab-rsia-002a/b) and freeze inputs, licences, and
   checksums.
2. Produce references with two independent toolchains (for example GeoPandas/Shapely and QGIS
   processing) and record the observed disagreement; that number, not a guess, sets the final
   tolerance.
3. Implement null handling and per-entity geometry matching, then add gab-spa-002 and
   gab-rsia-001.
4. Add vector predicates, then gab-sjg-002.
