# GABench adapter

This adapter turns an external GeoAgentBench / GABench checkout into local OpenMapBench bridge
tasks. It changes the primary evaluation question from expected tool trajectory to correct output
artifact.

## License boundary

`GeoX-Lab/GABench` currently has no repository license. OpenMapBench therefore does not vendor its
benchmark CSV, task text, data, reference maps, or generated layers. The importer writes only to a
user-selected local directory; `.openmapbench/` is ignored by this repository.

Generated task files necessarily expose the upstream prompt to the local agent, but they are a
local runtime cache, not redistributed benchmark content. Do not commit that directory unless the
upstream licensing situation changes and redistribution is allowed.

## Import

```bash
git lfs install
git clone https://github.com/GeoX-Lab/GABench.git ../GABench
git -C ../GABench lfs pull

openmapbench gabench-import \
  --source ../GABench \
  --output .openmapbench/gabench
```

The command:

1. rejects unresolved Git LFS pointers in the CSV and any referenced input or ground truth;
2. validates the real upstream CSV columns;
3. discovers external dataset paths mentioned by each data description;
4. records the upstream commit, CSV checksum, input checksums, and undeclared license status;
5. generates local task YAML files containing absolute references to the external checkout;
6. records external reference paths and deterministic-support classification in `manifest.json`;
7. copies no upstream source or reference file.

Use `--no-hash-inputs` only for a fast exploratory import. Hashing is enabled by default because a
commit alone does not prove a local Git LFS worktree is clean.

## Run all imported tests

After importing, execute every entry in `.openmapbench/gabench/manifest.json` with the same agent
contract:

```bash
uv run --no-sync python scripts/run_gabench_all.py \
  --agent-command "my-agent --task {task_file} --output {output_path}" \
  --timeout-seconds 1800
```

Each invocation creates a new `runs/gabench/<batch-id>/` folder containing immutable task runs,
aggregate JSON and Markdown reports, a batch manifest, and the visual-review gallery. The runner
checks that generated task contracts, inputs, references, and recorded reference checksums still
match the imported manifest. Missing or invalid entries are reported as skipped, the remaining
tasks continue, and the final process status is nonzero. Use `--batch-id NAME` for a stable folder
name in automation, and `--agent-cwd PATH` if the agent must run from another project directory.

The same command can be provided through `OPENMAPBENCH_AGENT_COMMAND`. Agent commands are parsed
without a shell; wrap shell pipelines in a dedicated executable script.

## Current compatibility

GABench's `Result` column predominantly names PNG maps. Those entries remain valid imported task
contracts but are classified as `file` and enter manual review rather than the deterministic MVP
score. CSV results are
treated as tables, JSON metric objects as scalar/JSON artifacts, and geospatial vector formats as
vectors. Encoded `CHECK:JSON_VALUE` results become deterministic JSON field predicates. Raster
results are identified but remain unsupported by the strict MVP evaluator.

The `Layers` column often names more objective analytical artifacts, but those reference files are
not part of the upstream checkout. If a trusted baseline run has materialized them, point the
adapter to that directory:

```bash
openmapbench gabench-import \
  --source ../GABench \
  --reference-root ../trusted-gabench-run \
  --output .openmapbench/gabench
```

Every existing named layer becomes an additional local bridge task. Scalar/JSON, table, and vector
layer references are marked `deterministic_supported: true`; missing and unsupported references
carry an explicit reason.

## Visual review of bundled maps

If generated images are collected in a separate folder, create the full manual-review bundle in
one command:

```bash
openmapbench gabench-visual-report \
  .openmapbench/gabench/manifest.json \
  --candidate-root ../generated-gabench-images \
  --output visual-reviews/gabench
```

The result contains labeled side-by-side PNGs (generated left, bundled GABench expectation right),
a scrollable `index.html`, an editable `review.csv`, and a checksummed `manifest.json`. Missing and
ambiguous candidate filenames are surfaced in the report rather than guessed.

For images produced through `openmapbench run`, the equivalent command is:

```bash
openmapbench visual-report runs/gabench --output visual-reviews/gabench-runs
```

These folders are ignored by git. They contain local derivatives of upstream expected images; do
not redistribute them until GABench's licensing permits it. Side-by-side review is deliberately
unscored. A later fuzzy/semantic judge can add automated visual diagnostics for map layout, place
names, legends, and data text without conflating those judgments with deterministic GIS outputs.

## Run an imported task

Each `manifest.json` task entry supplies both `task_path` and `reference_path`. Pass those values to
the normal runner:

```bash
openmapbench run /absolute/path/to/task.yaml \
  --reference /absolute/path/to/reference.gpkg \
  --agent-command "my-agent --task {task_file} --output {output_path}" \
  --run-root runs/gabench
```

Tool-chain fields remain metadata only. TAO, TIO, TEM, and PEA may be added later as optional
diagnostics, but they do not determine strict artifact correctness.
