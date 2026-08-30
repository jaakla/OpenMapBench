# OpenMapBench

**Tool-agnostic, artifact-first evaluation for autonomous GIS agents.**

OpenMapBench scores whether an agent produced the correct analytical artifact. It does not
require the agent to reproduce a canonical tool chain, library choice, or sequence of calls.

> **Score the result, not the tool trajectory.**

## MVP status

The repository now contains a runnable MVP:

- a versioned generic task contract;
- an agent-neutral subprocess runner;
- deterministic scalar/JSON, CSV/JSON-table, and vector evaluators;
- immutable run directories with logs, checksums, environment metadata, and manifests;
- aggregate JSON or Markdown reports with a headline strict success rate;
- static side-by-side visual-review folders with PNG sheets, an HTML index, an editable review
  CSV, and a machine-readable manifest;
- a GABench importer that operates against an external checkout and never copies upstream
  datasets or reference artifacts;
- automated tests and CI.

Raster and cartographic-image outputs are deliberately not included in the strict automated MVP
score. Image runs enter `needs_review` and can be inspected manually without being treated as
passes. Fuzzy and semantic image judging is the next phase.

## Install

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are recommended.

```bash
git clone https://github.com/jaakla/OpenMapBench.git
cd OpenMapBench
uv sync --no-editable --extra geo --extra dev
```

The `geo` extra installs GeoPandas, Shapely, PyProj, and raster I/O dependencies needed by the
vector evaluator. A conventional installation also works:

```bash
python -m pip install ".[geo,dev]"
```

## Run the included example

The example command follows the same contract an agent adapter uses. The solver is only a tiny
deterministic stand-in so the whole benchmark loop can be exercised locally.

```bash
openmapbench validate benchmark/examples/sum-values/task.yaml

openmapbench run benchmark/examples/sum-values/task.yaml \
  --reference benchmark/examples/sum-values/reference/result.txt \
  --agent-command ".venv/bin/python benchmark/examples/sum-values/solve.py" \
  --agent-cwd . \
  --run-root runs

openmapbench report runs --output report.json
```

A successful run writes:

```text
runs/<run-id>/
├── artifacts/<task output>
├── agent.stdout.log
├── agent.stderr.log
└── manifest.json
```

The process exits nonzero for an agent error, missing artifact, evaluator error, or strict
evaluation failure. An image artifact awaiting manual review exits successfully with status
`needs_review`. The manifest is always written, so every run remains auditable.

## Generic task contract

Every task is a YAML file. Inputs may be task-relative paths for native tasks or absolute local
references produced by an external adapter. Outputs must be safe relative paths and are created
inside the run directory.

```yaml
schema_version: "0.1"
id: vector-buffer-001
title: School proximity zones
category: vector
prompt: >
  Create and dissolve 500 metre school buffers. Write result.gpkg.

inputs:
  - path: inputs/schools.gpkg
    role: schools
    source: https://example.invalid/versioned-source
    as_of: "2026-08-01"
    checksum: sha256:<digest>
    license: CC-BY-4.0

output:
  path: result.gpkg
  kind: vector
  layer: result
  geometry_type: MultiPolygon
  crs: EPSG:3301
  required_fields: [school_count]

evaluation:
  strict:
    crs: exact
    feature_count: ignore
    geometry:
      metric: symmetric_difference_ratio
      tolerance: 0.001
      require_valid: true
    attributes:
      key: zone_id
      columns: [zone_id, school_count]
```

See [docs/task-contract.md](docs/task-contract.md) for the evaluator options and validation rules.

## Agent runner contract

`openmapbench run` launches a command directly, without an implicit shell. Commands can use these
placeholders:

- `{task_file}` and `{task_dir}`;
- `{output_dir}` and `{output_path}`;
- `{run_dir}`.

The same values are exposed as `OPENMAPBENCH_TASK_FILE`, `OPENMAPBENCH_TASK_DIR`,
`OPENMAPBENCH_OUTPUT_DIR`, `OPENMAPBENCH_OUTPUT_PATH`, and `OPENMAPBENCH_RUN_DIR`. This keeps the
core independent of model vendors: a Codex adapter, Claude Code adapter, container wrapper, MCP
Useful metadata can be recorded with `--agent-name`, `--model`, repeated `--skill`, and repeated
`--tool` flags. Use a script wrapper when an agent needs pipes, redirects, or other shell syntax.

## Deterministic evaluators

### Scalar and JSON

Numeric text and numeric values nested inside JSON use configured absolute and relative
tolerances. Strings, booleans, keys, and list order compare exactly. JSON field predicates support
threshold contracts such as `metrics.roc_auc >= 0.9` without relying on an LLM judge.

### Tables

CSV and JSON row arrays support:

- order-independent comparison;
- entity keys and duplicate-key rejection;
- required or ignored columns;
- per-column numeric tolerances;
- bounded mismatch diagnostics.

### Vectors

The vector evaluator checks CRS, required fields, geometry family, validity, optional feature
count, and semantic geometry equivalence. Polygon partitions and feature order do not affect a
union-based score. Available metrics are symmetric-difference ratio, IoU, and Hausdorff distance.
Geographic data is reprojected to a deterministic projected comparison CRS before area or distance
metrics are computed.

## Manual visual review

Image outputs are validated as decodable images and assigned `needs_review`; they are not given an
automated success score. Build a review folder from normal OpenMapBench runs:

```bash
openmapbench visual-report runs/gabench \
  --output visual-reviews/gabench-runs
```

The output is intentionally static and easy to inspect or archive:

```text
visual-reviews/gabench-runs/
├── index.html             # scrollable visual review
├── review.csv             # fill in pass/fail/uncertain and notes
├── manifest.json          # paths, checksums, dimensions, provenance
└── comparisons/
    └── 001-gabench-001-<run>.png
```

Each comparison places the generated image on the left and the expected reference on the right,
in equally sized panels without cropping. Images may be downscaled to the configured panel limit;
the manifest retains original dimensions and checksums. This is a presentation artifact, not an
image-similarity metric. Regenerating the same review folder preserves existing `manual_result`
and `notes` values from `review.csv`.

## Strict success score

The headline score is intentionally simple:

```text
strict_success_rate = passed_runs / strictly_scored_runs
```

Runs awaiting manual visual review are excluded from the strict denominator and reported
separately. Near-miss diagnostics never turn a failed task into a success. Reports also break
results down by category, output kind, and terminal status.

```bash
openmapbench report runs
openmapbench report runs --output report.md
```

See [docs/run-manifest.md](docs/run-manifest.md) and [docs/scoring.md](docs/scoring.md).

## GABench interoperability without vendoring

The upstream `GeoX-Lab/GABench` repository currently has no declared repository license.
OpenMapBench therefore does not include its CSV, prompts, datasets, maps, or derived layers.

Clone it separately and resolve Git LFS files:

```bash
git lfs install
git clone https://github.com/GeoX-Lab/GABench.git ../GABench
git -C ../GABench lfs pull
```

Build a local bridge under the ignored `.openmapbench/` directory:

```bash
openmapbench gabench-import \
  --source ../GABench \
  --output .openmapbench/gabench
```

The importer reads the actual upstream columns, discovers input files named in each task's data
description, records the upstream commit and checksums, generates local task contracts, and points
the manifest at upstream reference files. Nothing from GABench is copied into this repository.

Run every imported task in one isolated batch:

```bash
uv run --no-sync python scripts/run_gabench_all.py \
  --agent-command "my-agent --task {task_file} --output {output_path}" \
  --agent-name my-agent \
  --model my-model \
  --timeout-seconds 1800
```

The script defaults to `.openmapbench/gabench/manifest.json`, continues after individual failures,
and creates a timestamped directory under `runs/gabench/`:

```text
runs/gabench/<batch-id>/
├── batch.json             # batch provenance, outcomes, and skipped-task reasons
├── report.json            # aggregate machine-readable score
├── report.md              # readable score and per-task status table
├── task-runs/             # immutable artifact, log, and manifest folder for every task
└── visual-review/         # HTML, CSV, manifest, and comparison PNGs for image tasks
```

The process returns nonzero after completing the batch if any task failed, errored, or could not be
run. Image tasks in `needs_review` do not cause a runner failure, but remain excluded from the
strict score. Set `OPENMAPBENCH_AGENT_COMMAND` instead of `--agent-command` if preferred. Commands
are executed directly without shell expansion; use an agent wrapper script if pipes or redirects
are required.

Most upstream final artifacts are PNG maps. They are now usable through manual visual review while
remaining outside the strict automated score. If generated images are already collected in one
directory, compare them directly with the bundled GABench expectations:

```bash
openmapbench gabench-visual-report \
  .openmapbench/gabench/manifest.json \
  --candidate-root ../generated-gabench-images \
  --output visual-reviews/gabench
```

Open `visual-reviews/gabench/index.html` and record decisions in `review.csv`. The candidate lookup
accepts images directly under `--candidate-root`, under a `<task-id>/` subdirectory, or at a unique
recursive match. Ambiguous matches are listed as skipped instead of being guessed.

The upstream CSV also names analytical layers that are not shipped as reference files. If you have
a trusted reference run, expose those layers without copying them:

```bash
openmapbench gabench-import \
  --source ../GABench \
  --reference-root ../trusted-gabench-run \
  --output .openmapbench/gabench
```

Existing scalar/JSON, table, and vector references are marked `deterministic_supported` in
`.openmapbench/gabench/manifest.json`; raster scoring remains future work. The next image phase is a
fuzzy plus semantic judge that can inspect layout, place names, legends, and data labels while
keeping deterministic artifact checks separate. See
[adapters/gabench/README.md](adapters/gabench/README.md) for details.

## Development

```bash
uv sync --no-editable --extra geo --extra dev
uv run --no-sync ruff check .
uv run --no-sync pytest -q
```

OpenMapBench code and original task specifications are Apache-2.0. Third-party tasks and datasets
retain their own terms; an adapter never implies relicensing.
