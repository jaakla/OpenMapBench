# Scoring

## Primary score

The main benchmark score is binary task success:

```text
strict_success_rate = strict_successes / strictly_scored_tasks
```

A task is successful only when every required strict condition passes. Image runs awaiting manual
review are reported separately and are not included in either side of this fraction.

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

Implemented: union-based comparison by default; per-entity comparison (`geometry.match: entity`)
with entity precision, recall, and F1 when the partition itself is the result; and
reference-independent predicate checks (`vector_checks`) for properties with many correct
answers, such as one interior point per polygon. A failed entity-matched run reports entity F1 as
its diagnostic closeness.

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

Implemented checks:

- required columns;
- entity keys;
- row set;
- type-aware numeric tolerances;
- ordering only when ordering is semantically meaningful.

CSV and JSON row arrays are supported. Duplicate configured entity keys are strict failures.

## Run aggregation

Only manifests with terminal status `passed` count as strict successes. Agent errors, missing
outputs, evaluator errors, and deterministic comparison failures contribute zero to the numerator.
`needs_review` remains an attempted task but is excluded from the strict denominator. Invalid
manifests are reported separately and are not silently counted.

Reports also group strict success by each tag in the task's `metadata.failure_modes`; a run
tagged with two pitfalls counts in both groups, and untagged runs are listed as `untagged`.

## Token and cost reporting

Token use and estimated cost are efficiency diagnostics; they never affect strict task success.
Reports include total usage plus minimum, average, and maximum tokens per task, split by detected
model. Detailed token categories support a point cost estimate. Total-only logs support only a
lower-to-upper range across that model's token rates.

Pricing metadata is dated and source-linked. All reported dollar values are API-equivalent list
price estimates, not actual ChatGPT subscription charges. Runs using unknown models remain in token
statistics and are counted as unpriced rather than silently assigned another model's rates.

## Maps / cartography

Analytical correctness and visual/cartographic quality should be two separate dimensions.

The visual-review MVP composes generated and expected images side by side, preserving aspect ratio
and avoiding crop. It writes PNG sheets, an HTML index, a review CSV, and a checksummed manifest.
This is manual evidence, not an image-similarity score.

The next phase may add fuzzy pixel/structure metrics and an LLM/VLM judge for semantic content such
as place names, legends, and data labels. Those diagnostics must remain distinguishable from
deterministic validation of the underlying spatial result.
