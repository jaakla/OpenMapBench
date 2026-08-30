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
for broad geographic extents.

`output.geometry_type`, `output.crs`, `output.required_fields`, and optional `output.layer` add
strict artifact constraints. Feature count is ignored by default because a semantically identical
polygon union may be partitioned differently by different GIS engines.

## Reference isolation

Reference paths are CLI or suite-manifest inputs, not required fields in the task visible to the
agent. This allows the same task contract to run with public development references or hidden
leaderboard references without exposing ground truth.
