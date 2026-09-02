# Task contract

OpenMapBench task schema `0.1` fixes the problem, visible inputs, expected artifact, and strict
evaluation policy while leaving the implementation open.

## Required fields

- `id`: stable, whitespace-free task identifier.
- `title`, `category`, `prompt`: human-facing task definition and reporting group.
- `output.path`: safe path relative to the runner's artifact directory.
- `output.kind`: `scalar`, `table`, `vector`, `raster`, or `file`.

`raster` and `file` are valid contract values but do not have strict MVP evaluators yet. The runner
records an explicit `evaluator_error`; it never treats them as successful.

## Inputs and provenance

Each input supports `path`, `role`, `source`, `checksum`, `as_of`, and `license`. Native tasks should
use task-relative paths. Local interoperability adapters may use absolute paths to keep third-party
content external. `openmapbench validate` reports missing files and checksum mismatches as failures,
and undeclared checksums as warnings.

## Scalar/JSON strict configuration

```yaml
strict:
  absolute_tolerance: 0.01
  relative_tolerance: 0.001
```

JSON object key order is ignored. JSON list order is meaningful. Numeric tolerances apply
recursively; other values compare exactly.

Reference-independent JSON field predicates are also deterministic:

```yaml
strict:
  json_checks:
    - {path: metrics.roc_auc, operator: ">=", value: 0.9}
```

Paths traverse object keys with dots and may use a numeric component for a list index. Supported
operators are `<`, `<=`, `==`, `!=`, `>=`, and `>`.

## Table strict configuration

```yaml
strict:
  key: feature_id
  ignore_order: true
  required_columns: [feature_id, value]
  ignore_columns: [generated_at]
  numeric_tolerance:
    default: {absolute: 0, relative: 0}
    value: {absolute: 0.01, relative: 0.001}
```

CSV and JSON row arrays are supported. A JSON object may wrap the array under `rows`. When `key` is
set, duplicate candidate keys are a strict failure.

Null semantics are explicit: `null`, an empty CSV cell, the strings `null`, `none`, and `nan`
(case-insensitive), and NaN all mean *no value*. A null equals a null and never equals a number or
a string, so a NoData rule such as "set NDVI to null where a band is NoData" is scored; writing
`0` for a missing value is a mismatch. The same rule applies to vector attribute comparison.

## Vector strict configuration

```yaml
strict:
  crs: exact                 # or ignore
  feature_count: ignore      # or exact
  geometry:
    metric: auto             # symmetric_difference_ratio, iou, hausdorff_distance
    tolerance: 0.001
    comparison_crs: EPSG:3301
    require_valid: true
  attributes:
    key: feature_id
    columns: [feature_id, value]
    numeric_tolerance:
      value: {absolute: 0.01, relative: 0}
```

`auto` selects symmetric-difference ratio for polygonal references and Hausdorff distance for
linear or point references. IoU interprets `tolerance` as the minimum accepted IoU; the other
metrics interpret it as the maximum accepted error. If `comparison_crs` is omitted, OpenMapBench
uses the reference CRS when projected, a local UTM CRS for compact geographic data, or EPSG:6933
for broad geographic extents. `metric: ignore` skips geometry comparison entirely, for tasks whose
geometry is scored only by `vector_checks` below.

### Entity-level geometry matching

```yaml
strict:
  geometry:
    metric: iou
    tolerance: 0.5
    match: entity          # default: union
    key: hosp_fid          # defaults to attributes.key
```

`match: union` (the default) compares the union of all candidate features with the union of all
reference features, so partitioning never matters but a wrong partition is invisible. `match:
entity` groups both artifacts by `key`, unions the parts of each entity, and applies the metric
per entity. The check passes only when every reference key is present, no extra keys exist, and
every entity meets the tolerance. Diagnostics carry entity precision, recall, and F1 (the failed
run's closeness score), plus the missing, extra, and failed keys. Use it whenever the task is
about a partition, an assignment, or a per-feature geometry such as Voronoi cells or per-source
clipped buffers.

### Reference-independent vector checks

```yaml
strict:
  geometry: {metric: ignore}
  vector_checks:
    - {type: within, input_role: polygons, key: poly_fid, input_key: fid}
    - {type: count_per_input, input_role: polygons, key: poly_fid, input_key: fid, count: 1}
    - {type: field_equals_geometry, x: x_3301, y: y_3301, tolerance: 0.01, crs: EPSG:3301}
    - {type: field_equals_measure, field: area_m2, measure: area, crs: EPSG:3301, relative: 0.001}
    - {type: field_range, field: ndvi, min: -1, max: 1, allow_null: true}
    - {type: unique, field: grid_id}
```

Every listed check is strict. They relate the candidate to the task's own declared inputs
(by `role`) or to itself, so a task can require a property that has many correct answers, such as
"one interior point per polygon", without a reference that fixes one of them.

| type | meaning |
| --- | --- |
| `within`, `covered_by`, `intersects`, `contains`, `covers`, `disjoint` (or `type: relation` with `predicate`) | each candidate feature stands in that relation to the input feature sharing its key; input parts with the same key are unioned first; the input is reprojected to the candidate CRS |
| `count_per_input` | every input key has exactly `count` candidate features and no candidate key is unknown |
| `field_equals_geometry` | for point features, the `x`/`y` fields equal the geometry coordinates within `tolerance`, optionally after reprojecting to `crs` |
| `field_equals_measure` | the field equals the geometry `area` or `length`, optionally in `crs`, within `absolute`/`relative` tolerance |
| `field_range` | numeric field lies within `min`/`max`; nulls fail unless `allow_null` |
| `unique` | no duplicate values in the field |

Input roles are resolved from the task file, so `openmapbench evaluate` and the runner pass the
task path through; calling `evaluate_vector` directly requires an explicit `inputs` mapping.

`output.geometry_type`, `output.crs`, `output.required_fields`, and optional `output.layer` add
strict artifact constraints. Feature count is ignored by default because a semantically identical
polygon union may be partitioned differently by different GIS engines.

## Failure-mode tags

`metadata` is free-form, but `metadata.failure_modes` is read by reporting: a list of tags naming
the practitioner pitfalls the task exercises (for example `crs_misalignment`, `unit_mismatch`,
`boundary_ambiguity`, `geometry_topology`, `nodata`, `operation_order`,
`wrong_tool_substitution`). Reports break strict success down by tag, and every run manifest
retains the task metadata it was scored under.

## Reference isolation

Reference paths are CLI or suite-manifest inputs, not required fields in the task visible to the
agent. This allows the same task contract to run with public development references or hidden
leaderboard references without exposing ground truth.
