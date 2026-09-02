# Roadmap

## Issue 1 — Make one end-to-end synthetic vector task pass
Create tiny projected vector fixtures and a reference output. Run the CLI in CI.

Status: evaluator-level vector fixtures and an end-to-end scalar runner fixture are complete. A
versioned native end-to-end vector benchmark task remains to be added.

## Issue 2 — Complete deterministic vector scoring
Add entity-key matching, required attributes, geometry validity, geometry-family checks, and configurable geometry metrics.

Status: complete for the MVP.

## Issue 3 — GABench field mapping
Against a real Git-LFS checkout, document `benchmark.csv` fields and convert compatible tasks into runtime OpenMapBench task objects without copying upstream content.

Status: complete for scalar/JSON, table, vector, and compatibility classification. Raster and
cartographic-image automation remain future work. Manual side-by-side image review is complete.

## Issue 4 — Visual map review
Create static side-by-side sheets for generated and bundled GABench images, plus an HTML index,
editable review CSV, and checksummed manifest.

Status: complete for manual review. Next: fuzzy structural similarity and a semantic LLM/VLM judge
for place names, legends, data labels, and map meaning.

## Issue 5 — GABench compatibility audit
Classify all upstream tasks by output type and by whether objective artifact-based scoring is possible.

## Issue 6 — Generic agent runner contract
Define a subprocess/container interface:
- input task workspace;
- output workspace;
- time limit;
- optional network policy;
- run metadata.

Do not bind the benchmark core to one model vendor.

Status: complete for local subprocess adapters, manifests, and aggregate reporting. Container and
hosted-agent wrappers can build on the same environment/path contract.

## Issue 7 — First comparative evaluation
Run the same compatible task subset with:
- vanilla agent;
- same agent + GIS skill package.

Report strict success, category breakdown, cost, and failure taxonomy.

## Issue 8 — Native OpenMapBench v0 set
Add 30–50 verified tasks emphasizing real GIS failure modes and reproducibility.

Candidate sources: ten tasks reinterpreted from the GISAgentBench case studies (arXiv:2608.01645)
are drafted with contracts, tolerance rationale, and evaluator gaps in
[docs/candidates/gisagentbench-case-studies.md](docs/candidates/gisagentbench-case-studies.md).

Status: the first native task, `benchmark/tasks/gab-sjg-001` (destinations within one mile of
each Tartu bus stop, OpenStreetMap data, cross-checked reference), is in place and guarded by
`tests/test_benchmark_tasks.py`, which validates every native task and scores its reference.
