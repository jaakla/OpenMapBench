# Roadmap

## Issue 1 — Make one end-to-end synthetic vector task pass
Create tiny projected vector fixtures and a reference output. Run the CLI in CI.

## Issue 2 — Complete deterministic vector scoring
Add entity-key matching, required attributes, geometry validity, geometry-family checks, and configurable geometry metrics.

## Issue 3 — GABench field mapping
Against a real Git-LFS checkout, document `benchmark.csv` fields and convert compatible tasks into runtime OpenMapBench task objects without copying upstream content.

## Issue 4 — GABench compatibility audit
Classify all upstream tasks by output type and by whether objective artifact-based scoring is possible.

## Issue 5 — Generic agent runner contract
Define a subprocess/container interface:
- input task workspace;
- output workspace;
- time limit;
- optional network policy;
- run metadata.

Do not bind the benchmark core to one model vendor.

## Issue 6 — First comparative evaluation
Run the same compatible task subset with:
- vanilla agent;
- same agent + GIS skill package.

Report strict success, category breakdown, cost, and failure taxonomy.

## Issue 7 — Native OpenMapBench v0 set
Add 30–50 verified tasks emphasizing real GIS failure modes and reproducibility.
