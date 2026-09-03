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
- aggregate JSON, Markdown, or detailed self-contained HTML reports with strict success and
  per-model usage statistics;
- a suite runner that runs every native task with one agent and writes an isolated batch bundle;
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
provenance README, a `reference/` artifact, and the `tools/` scripts that built both. All ten
worked cases from the GISAgentBench appendix are implemented here, recast onto Estonian open
data; three of them score two artifacts and are split into a pair of tasks.

Every reference is produced by two independent methods that must agree before it is written,
and every input README records what the data actually discriminates: how many rows, cells or
features each plausible wrong approach gets wrong, measured rather than asserted. Where a
tolerance is not a rounding budget — `gab-tmha-002`, where two correct resamplers disagree —
the task says so.

| Task | Question | Output | Failure modes exercised |
| --- | --- | --- | --- |
| `gab-sjg-001` | Schools and kindergartens within one mile of each Tartu bus stop | vector points | unit mismatch, CRS misalignment, boundary ambiguity |
| `gab-sosa-001` | 3 km inward inset of Saare maakond, exploded to single parts | vector polygons | geometry topology, multipart handling |
| `gab-sjg-002` | One guaranteed-interior point per Estonian municipality, with projected coordinates | vector points | CRS misalignment, contract literalism |
| `gab-spa-001` | Distinct parks per cell of a pinned 1 km grid over Tallinn | vector polygons | boundary ambiguity, multipart handling, wrong tool substitution |
| `gab-spa-002` | Nearest-hospital service areas inside 1 km, clipped to Tallinn | vector polygons | wrong tool substitution, geometry topology |
| `gab-sosa-002a` | Pärnu county rivers clipped to municipalities, written in the input CRS | vector lines | CRS preservation, geometry topology |
| `gab-sosa-002b` | Clipped river length per source feature | table | CRS misalignment, geometry topology, unit mismatch |
| `gab-tmha-001` | Braided versus single-channel length per watercourse | table | wrong tool substitution, geometry topology |
| `gab-rsia-002a` | ESA WorldCover regions over Ruhnu as 8-connected polygons | vector polygons | connectivity, missing reprojection |
| `gab-rsia-002b` | Region count and area per landcover class | table | connectivity, missing reprojection |
| `gab-tmha-002` | Steep-slope regions on a 10 m grid resampled from a 30 m DEM | vector polygons | operation order, connectivity, boundary ambiguity |
| `gab-tmha-002s` | Steep area, region count and maximum slope for the same analysis | scalar | operation order, connectivity |
| `gab-rsia-001` | NDVI at Tartu address points from a four-band image | vector points | band order, NoData, CRS misalignment, sampling method |

Run one with its bundled reference solver to see the whole loop:

```bash
openmapbench run benchmark/tasks/gab-sjg-001/task.yaml \
  --reference benchmark/tasks/gab-sjg-001/reference/stops_with_counts.gpkg \
  --agent-command ".venv/bin/python {task_dir}/tools/solve.py" \
  --agent-cwd . \
  --run-root runs
```

Every native task is checked in CI: its inputs must match their checksums and carry source,
license, and acquisition date, and its reference must pass its own strict contract. The
case-by-case rationale, including where the implemented contracts depart from the drafts and why,
is in [docs/candidates/gisagentbench-case-studies.md](docs/candidates/gisagentbench-case-studies.md).

## Run the whole suite

One command runs every native task against one agent, continues past individual failures, and
writes an isolated batch bundle with a detailed HTML report:

```bash
uv run --no-sync python scripts/run_benchmark_all.py \
  --agent-command 'codex exec --json --ephemeral --approve-for-me -m gpt-5.6-luna "Read the OpenMapBench task at {task_file}, complete it using the declared inputs, and write the required artifact exactly to {output_path}. Create the file rather than only explaining the result."' \
  --agent-name codex \
  --model gpt-5.6-luna \
  --timeout-seconds 1800
```

The same runner is available as `openmapbench run-suite benchmark/tasks --agent-command ...`. It
defaults to `benchmark/tasks` and creates a timestamped directory under `runs/benchmark/`:

```text
runs/benchmark/<batch-id>/
├── batch.json      # batch provenance, per-task outcomes, and skipped-task reasons
├── report.json     # aggregate score, token, model, and cost statistics
├── report.md       # readable score, usage summary, and per-task table
├── report.html     # detailed browsable report: evidence, logs, previews, audit per task
├── task-runs/      # immutable artifact, log, and manifest folder for every task
└── visual-review/  # comparison sheets for every vector, raster, and image artifact
```

Open `report.html` to review a batch. It is one self-contained page — no network requests, no
external assets — with headline tiles, the outcome distribution, strict success broken down by
category, output kind, and tagged failure mode, and one card per task carrying the prompt, the
declared artifact contract, every strict check with its evidence, diagnostics, the agent's own
stdout and stderr, links to the artifact and reference, and the full execution audit. Failed runs
open their diagnostics and stderr by default; the cards filter by status and search by task ID,
title, category, or failure mode. `openmapbench report <run-root> --output report.html` produces
the same page for any directory of runs.

Every vector and raster run is also drawn: expected reference, generated candidate, and the two
overlaid in one shared extent, captioned with feature counts or raster size and the declared CRS.
A missing reprojection, a dropped multipart, or a buffer measured in degrees is obvious in the
picture long before it is obvious in a table of numbers. Rasters additionally get an
absolute-difference panel when both grids align. Tables and scalars are reviewed as text.

These sheets are review aids and never scores. They are rendered after evaluation from the
artifacts alone, and only artifact kinds with no deterministic evaluator — images today — leave a
row in `visual-review/review.csv` awaiting a human decision.

### Repeats, because one pass is not a measurement

Agents are not deterministic. Running the native suite twice against the same model, same prompt
and same isolation gave 76.9% and 84.6%, with three of thirteen tasks flipping in each direction.
A single pass tells you whether an agent solved a task once, not whether it can solve it.

```bash
uv run --no-sync python scripts/run_benchmark_all.py --agent-command '...' --repeat 5
```

Attempts go round robin — every task once, then again — so a partial batch still covers the suite
and any drift in the model service is spread across tasks rather than concentrated in one. The
reports gain a pass rate per task, and any task that passes only sometimes is named as unstable:
that is exactly where a single-pass score misleads.

Useful flags: `--task <id>` and `--skip <id>` are repeatable, `--reference-solver` runs each
task's own `tools/solve.py` as the agent, `--no-isolate-task` hands the agent the original task
directory instead of a staged copy, and `--no-verify-inputs` runs tasks whose frozen inputs no
longer match their checksums. The process returns nonzero if any task failed, errored, or
could not be run. A task that cannot be scored fairly — no reference artifact, malformed contract,
or altered inputs — is reported as skipped rather than counted as a failure.

The reference solvers are also the suite's smoke test: every task must pass its own strict
contract when solved by its bundled solver.

```bash
uv run --no-sync python scripts/run_benchmark_all.py --reference-solver
```

## Keeping a run honest

A task directory holds the answer as well as the question: `reference/<artifact>`,
`tools/solve.py`, and an `inputs/README.md` that records what each wrong approach gets wrong. An
agent with a shell will find them — we watched a mini model read `tools/solve.py` and run it,
and on another task copy `reference/river_lengths.csv` straight to the output path. Three passes,
no GIS.

Two defences, and the benchmark uses both:

**Withhold.** By default each run is staged: the runner copies `task.yaml` and only the files the
contract declares into `<run_dir>/task/` and points the agent there. The reference, the solver and
the provenance notes are not on disk anywhere the agent can reach. The staged contract is reduced
to the prompt, the declared inputs, the required artifact and the tolerances — `metadata` is
dropped, because `tolerance_rationale` names every trap and its measurement and `reference_method`
names the solver, and text inside the contract is not withheld by hiding files. Evaluation still
uses the original task and reference, and reporting still groups by the original tags.

**Detect.** Staging cannot stop an agent that goes looking on the host filesystem, so after each
run `integrity.py` reads the execution audit for any contact with withheld material. A run with
findings is marked contaminated, reported with the commands that condemn it, and excluded from
both sides of the strict success rate. It is not counted as a failure — the agent may have solved
it honestly as well — it is inadmissible as evidence.

```text
[001/003] gab-sjg-001: passed CONTAMINATED (53.78s)
```

A sandbox is a useful third layer and a different control: it bounds what a misbehaving agent can
touch and pins GDAL and PROJ versions so results are reproducible. It does not replace staging —
mount the repository into a container and the answer key is still one directory listing away.

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

## Visual review and artifact previews

Every spatial artifact in a run root is drawn for review. Vector and raster artifacts become
three-panel sheets — expected reference, generated candidate, and the two overlaid in one shared
extent — and image artifacts become side-by-side sheets. Build a review folder from normal
OpenMapBench runs:

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

Image comparisons place the generated image on the left and the expected reference on the right,
in equally sized panels without cropping. Vector and raster comparisons add the overlay panel and
caption each side with its feature count or raster size and its declared CRS, so a CRS or units
mistake is legible in the sheet itself. Each HTML card also shows the full task prompt so the
reviewer can judge the artifact against the requested result. Images may be downscaled to the
configured panel limit; the manifest retains the prompt, original dimensions, and checksums.

None of this is an automated correctness metric. A strictly scored run keeps the verdict from its
checks and its sheet merely illustrates it, which is why only artifact kinds with no deterministic
evaluator — images, assigned `needs_review` — leave a row in `review.csv` awaiting a decision.
Regenerating the same review folder preserves existing `manual_result` and `notes` values.

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
├── report.html            # detailed browsable report: evidence, logs, and audit per task
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
