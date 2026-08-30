# Scoring

## Primary score

The main benchmark score is binary task success:

```text
strict_success_rate = strict_successes / attempted_tasks
```

A task is successful only when every required strict condition passes.

This number should be the headline score because it is interpretable and resistant to arbitrary weighting.

## Diagnostic closeness

A failed task may still receive a continuous diagnostic score in `[0, 1]`.

Diagnostic scores are useful for development but must not silently turn near misses into benchmark successes.

## Vector outputs

Candidate and reference files should be compared semantically, not byte-for-byte.

Potential checks:

- required layer(s);
- CRS;
- geometry family;
- feature/entity set;
- required attributes;
- geometry validity;
- topology constraints;
- geometry similarity.

Useful geometry metrics include:

- symmetric-difference area / reference area;
- IoU;
- Hausdorff distance for linear features;
- tolerance-aware coordinate distance.

Vertex order, ring orientation, serialization order, and harmless coordinate precision differences should not cause failure.

## Raster outputs

Planned checks:

- CRS;
- bounds;
- dimensions;
- resolution;
- nodata mask;
- dtype where semantically relevant;
- per-pixel tolerance;
- aggregate error metrics.

Resampling tolerance must be specified by the task, not invented after seeing model outputs.

## Tables

Planned checks:

- required columns;
- entity keys;
- row set;
- type-aware numeric tolerances;
- ordering only when ordering is semantically meaningful.

## Maps / cartography

Analytical correctness and visual/cartographic quality should be two separate dimensions.

An LLM/VLM judge may be useful for subjective map-quality diagnostics, but should not replace deterministic validation of the underlying spatial result.
