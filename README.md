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
- automatic Codex token capture plus dated API-equivalent cost estimates;
- aggregate JSON or Markdown reports with strict success and per-model usage statistics;
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

After installation, the `openmapbench` CLI is available in the project environment. Use either:

```bash
source .venv/bin/activate
openmapbench validate benchmark/examples/sum-values/task.yaml
```

or, without activating the virtualenv:

```bash
uv run openmapbench validate benchmark/examples/sum-values/task.yaml
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

## Native benchmark tasks

Native tasks live under `benchmark/tasks/<id>/` with a `task.yaml`, frozen `inputs/` and their
provenance README, a `reference/` artifact, and the `tools/` scripts that built both. They are
recast from the GISAgentBench case studies onto Estonian open data.

| Task | Question | Output | Failure modes exercised |
| --- | --- | --- | --- |
| `gab-sjg-001` | Schools and kindergartens within one mile of each Tartu bus stop | vector points | unit mismatch, CRS misalignment, boundary ambiguity |
| `gab-sosa-001` | 3 km inward inset of Saare maakond, exploded to single parts | vector polygons | geometry topology, multipart handling |

Run one with its bundled reference solver to see the whole loop:

```bash
openmapbench run benchmark/tasks/gab-sjg-001/task.yaml \
  --reference benchmark/tasks/gab-sjg-001/reference/stops_with_counts.gpkg \
  --agent-command ".venv/bin/python {task_dir}/tools/solve.py" \
  --agent-cwd . \
  --run-root runs
```

Every native task is checked in CI: its inputs must match their checksums and carry source,
license, and acquisition date, and its reference must pass its own strict contract. Candidate
tasks and the rationale behind them are in
[docs/candidates/gisagentbench-case-studies.md](docs/candidates/gisagentbench-case-studies.md).

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
- `{run_dir}` and `{workspace_dir}`.

The same values are exposed as `OPENMAPBENCH_TASK_FILE`, `OPENMAPBENCH_TASK_DIR`,
`OPENMAPBENCH_OUTPUT_DIR`, `OPENMAPBENCH_OUTPUT_PATH`, `OPENMAPBENCH_RUN_DIR`, and
`OPENMAPBENCH_WORKSPACE_DIR`; the optional
agent audit stream is exposed as `OPENMAPBENCH_AUDIT_PATH`. This keeps the
core independent of model vendors: a Codex adapter, Claude Code adapter, container wrapper, MCP
client, or local script can use the same interface. Useful metadata can be recorded with
`--agent-name`, `--model`, repeated `--skill`, and repeated
`--tool` flags. Use a script wrapper when an agent needs pipes, redirects, or other shell syntax.

The agent process runs from `<run_dir>/workspace/` unless `--agent-cwd` is given. Helper scripts
and other scratch files an agent writes to its working directory therefore stay inside the
gitignored run directory and are inventoried by the audit, instead of accumulating in the project
root. Relative paths inside the agent command resolve against that working directory, so refer to
local solvers or interpreters by absolute path, through the `{task_dir}` placeholder, or pass
`--agent-cwd .` as the example above does. Use `--agent-cwd` also when the agent must see
project-level instructions or skills from the repository.
Pass `-v`/`--verbose` to show runner stages and a live, readable summary of agent commands, tool
calls, file changes, ordinary stdout, and stderr while the same lossless logs are retained.

### Execution audit and artifact lineage

Run manifests use schema `0.3` and contain a layered `audit` trail. The runner always records its
exact subprocess argument vector, working directory, runner-owned environment values, exit state,
and evaluator step. With `codex exec --json`, it also records inner commands, MCP tools and their
arguments, web searches, and file changes in JSONL source order. The manifest links back to the
lossless stdout/stderr logs rather than treating a missing inner trace as proof that no tools ran.

The runner inventories task inputs, intermediate files under the isolated run directory, the
candidate, reference, and logs. Each artifact has a final checksum and evidence-labelled lineage.
Wrappers for any agent can report additional actions and exact `derived_from` relationships by
appending vendor-neutral JSONL to the path in `OPENMAPBENCH_AUDIT_PATH`. See
[docs/run-manifest.md](docs/run-manifest.md) for the event and artifact format.

Agents usually put the logic that decides an output into a throwaway script—`render_heat.py`,
`.tmp_make_map.py`—and delete it before exiting, which leaves a path and a checksum for a file
nobody can read. The runner therefore watches the agent stream live and copies observed files into
`<run-dir>/captured-files/` as they appear: files named by a `file_change` item, files named
anywhere in a command including heredoc bodies, and files created or changed under the agent
working directory. Deleted files keep `exists_at_finish: false` and gain their preserved content,
so a run stays reviewable and reproducible. Task inputs, the reference, and files already kept in
the run directory are not duplicated. `audit.content_store` records the size, policy, and anything
deliberately skipped; `OPENMAPBENCH_AUDIT_CAPTURE=0` turns capture off.

### Token usage and cost

The runner recognizes Codex CLI usage in both JSON Lines output and the human-readable stderr
summary. Each manifest records the detected model, reasoning effort, total tokens, and—in JSON
mode—input, cached-input, cache-write, output, and reasoning-output categories when supplied.

OpenMapBench includes a dated price catalog for `gpt-5.6-luna`, `gpt-5.6-terra`, and
`gpt-5.6-sol`. Costs are **API-equivalent list-price estimates**, not actual ChatGPT subscription
charges. Detailed token categories produce a point estimate. A plain `tokens used` total cannot
distinguish cheap cached input from more expensive output, so it produces an explicit minimum to
maximum range instead.

For the most useful cost estimate, run Codex with `--json` and separately record the model with
OpenMapBench's `--model` option. Existing runs can be enriched from their retained logs:

```bash
openmapbench usage-backfill runs/gabench/<batch-id>/task-runs
openmapbench report runs/gabench/<batch-id>/task-runs \
  --output runs/gabench/<batch-id>/report.json
openmapbench report runs/gabench/<batch-id>/task-runs \
  --output runs/gabench/<batch-id>/report.md
```

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
- explicit null semantics (null equals null, never a number or string);
- bounded mismatch diagnostics.

### Vectors

The vector evaluator checks CRS, required fields, geometry family, validity, optional feature
count, and semantic geometry equivalence. Polygon partitions and feature order do not affect a
union-based score. Available metrics are symmetric-difference ratio, IoU, and Hausdorff distance.
Geographic data is reprojected to a deterministic projected comparison CRS before area or distance
metrics are computed.

Two further modes cover tasks the union score cannot: `geometry.match: entity` scores each keyed
entity separately (a Voronoi partition, per-source clipped buffers) and reports entity precision,
recall, and F1; `vector_checks` are reference-independent predicates against the task's own
inputs, such as "every point lies within the polygon with the same key", "exactly one feature per
input feature", "the x/y fields equal the geometry", or "the area field equals the measured
area". Attribute comparison is null-aware. See [docs/task-contract.md](docs/task-contract.md).

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
in equally sized panels without cropping. Each HTML card also shows the full task prompt so the
reviewer can judge the images against the requested result. Images may be downscaled to the
configured panel limit; the manifest retains the prompt, original dimensions, and checksums. This
is a presentation artifact, not an image-similarity metric. Regenerating the same review folder
preserves existing `manual_result` and `notes` values from `review.csv`.

## Strict success score

The headline score is intentionally simple:

```text
strict_success_rate = passed_runs / strictly_scored_runs
```

Runs awaiting manual visual review are excluded from the strict denominator and reported
separately. Near-miss diagnostics never turn a failed task into a success. Reports also break
results down by category, output kind, terminal status, and the `metadata.failure_modes` tags a
task declares, so success under CRS, unit, NoData, or topology pitfalls can be read directly.

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

Run a single imported task:

```bash
uv run --no-sync openmapbench run \
  .openmapbench/gabench/tasks/gabench-008/task.yaml \
  --reference ../GABench/dataset/result/Fire_Service_Analysis.png \
  --agent-command 'codex exec --json --ephemeral --approve-for-me -m gpt-5.6-luna -c model_reasoning_effort=low "Read the OpenMapBench task at {task_file}, complete it using the declared inputs, and write the required artifact exactly to {output_path}. Create the file rather than only explaining the result."' \
  --agent-name codex \
  --model gpt-5.6-luna \
  --timeout-seconds 1800 \
  --run-root runs/gabench
```

Run every imported task in one isolated batch, skip one problematic one as an example case:

```bash
uv run --no-sync python scripts/run_gabench_all.py \
  --agent-command 'codex exec --json --ephemeral --approve-for-me -m gpt-5.6-luna -c model_reasoning_effort=low "Read the OpenMapBench task at {task_file}, complete it using the declared inputs, and write the required artifact exactly to {output_path}. Create the file rather than only explaining the result."' \
  --agent-name codex \
  --model gpt-5.6-luna \
  --timeout-seconds 1800  \
  --skip gabench-009
```

The script defaults to `.openmapbench/gabench/manifest.json`, continues after individual failures,
and creates a timestamped directory under `runs/gabench/`:

```text
runs/gabench/<batch-id>/
├── batch.json             # batch provenance, outcomes, and skipped-task reasons
├── report.json            # aggregate score, token, model, and cost statistics
├── report.md              # readable score, usage summary, and per-task table
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
  --candidate-root runs/gabench \
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

Coding agents and contributors should read [AGENTS.md](AGENTS.md) for the repository map,
commands, conventions, and the invariants that must not be broken.

```bash
uv sync --no-editable --extra geo --extra dev
uv run --no-sync ruff check .
uv run --no-sync pytest -q
```

OpenMapBench code and original task specifications are Apache-2.0. Third-party tasks and datasets
retain their own terms; an adapter never implies relicensing.
