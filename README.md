# OpenMapBench

**Tool-agnostic benchmark for autonomous GIS analysis agents.**

OpenMapBench evaluates whether an AI agent can produce a **correct geospatial result**, regardless of whether it uses GeoPandas, DuckDB Spatial, PostGIS, QGIS, GDAL, custom code, MCP tools, or a GIS skill package.

The core principle is simple:

> **Score the analytical artifact, not the tool trajectory.**

This makes OpenMapBench suitable for comparisons such as:

| Configuration | Strict success |
| --- | ---: |
| Model A, vanilla | TBD |
| Model A + GIS skills | TBD |
| Model B, vanilla | TBD |
| Model B + GIS skills | TBD |

## Why this exists

Many GIS-agent benchmarks couple correctness to a predefined toolbox or expected sequence of tool calls. That is useful for measuring tool-use behavior, but it can penalize an agent that reaches the correct GIS answer through a different implementation.

OpenMapBench instead standardizes:

1. task input and instructions;
2. expected output contract;
3. frozen datasets / provenance;
4. deterministic or tolerance-aware result evaluation;
5. run metadata needed for reproducibility.

The agent implementation remains open.

## Status

**v0 scaffold.** The first milestone is to run a useful subset of GeoAgentBench / GABench tasks through a tool-agnostic OpenMapBench adapter, then compare vanilla agents against the same agents with GIS skills enabled.

OpenMapBench does **not** currently vendor GABench tasks or datasets. The upstream GABench repository does not currently declare a license, so the adapter is designed to operate against a local checkout of GABench.

## Repository layout

```text
OpenMapBench/
├── benchmark/
│   └── examples/
│       └── buffer-schools/
│           └── task.yaml
├── adapters/
│   └── gabench/
│       ├── README.md
│       └── import_tasks.py
├── src/openmapbench/
│   ├── cli.py
│   ├── evaluator.py
│   ├── models.py
│   └── taskio.py
├── tests/
├── docs/
│   ├── design.md
│   └── scoring.md
└── pyproject.toml
```

## Task contract

Each task is a directory containing `task.yaml`, inputs, and optionally private/reference ground truth.

```yaml
schema_version: "0.1"
id: vector-buffer-001
title: School proximity zones
category: vector
prompt: >
  Create a 500 metre buffer around every school and dissolve overlapping
  buffers. Save the result as result.gpkg.

inputs:
  - path: inputs/schools.gpkg
    role: schools

output:
  path: result.gpkg
  kind: vector
  geometry_type: MultiPolygon
  crs: EPSG:3301

evaluation:
  strict:
    geometry:
      metric: symmetric_difference_ratio
      tolerance: 0.001
    crs: exact
```

A benchmark runner gives the agent the task directory and a writable output directory. The runner itself is deliberately outside the benchmark semantics: Codex, Claude Code, custom agents, MCP clients, local scripts, or hosted systems should all be usable.

## Install

Python 3.11+.

```bash
git clone https://github.com/jaakla/OpenMapBench.git
cd OpenMapBench

# Recommended
uv sync --extra geo --extra dev
```

or:

```bash
pip install -e ".[geo,dev]"
```

## Validate a task

```bash
openmapbench validate benchmark/examples/buffer-schools/task.yaml
```

## Evaluate an output

The current scaffold implements scalar/table evaluation and the first vector evaluator interface. More GIS-specific evaluators are intentionally explicit rather than hidden behind an LLM judge.

```bash
openmapbench evaluate \
  benchmark/examples/buffer-schools/task.yaml \
  --candidate ./runs/vector-buffer-001/result.gpkg \
  --reference ./private/vector-buffer-001/reference.gpkg
```

## GABench adapter

Clone GABench separately with Git LFS:

```bash
git lfs install
git clone https://github.com/GeoX-Lab/GABench.git ../GABench
```

Inspect/import its benchmark metadata:

```bash
python adapters/gabench/import_tasks.py \
  --source ../GABench \
  --output .openmapbench/gabench-manifest.json
```

The adapter intentionally stores **references to the upstream checkout**, not copies of upstream tasks or data.

See [`adapters/gabench/README.md`](adapters/gabench/README.md).

## Scoring philosophy

The primary public number should remain understandable:

```text
strict_success_rate = successful_tasks / attempted_tasks
```

Then report diagnostics separately:

- vector / raster / network / tabular;
- CRS correctness;
- geometry correctness;
- attribute correctness;
- numerical closeness;
- data-quality / topology failures;
- runtime and cost;
- optional trajectory diagnostics.

See [`docs/scoring.md`](docs/scoring.md).

## Initial roadmap

### M0 — runnable benchmark core
- [x] task schema;
- [x] CLI skeleton;
- [x] scalar and tabular evaluator;
- [x] vector evaluator skeleton;
- [x] GABench adapter scaffold;
- [ ] frozen reference-output store;
- [ ] robust vector equivalence scorer;
- [ ] raster scorer;
- [ ] network-result scorer;
- [ ] run manifest format.

### M1 — GABench compatibility
- [ ] map GABench benchmark fields into OpenMapBench tasks;
- [ ] classify which GABench tasks can be scored deterministically;
- [ ] reproduce a baseline run using the original GABench agent;
- [ ] run the same tasks with arbitrary-tool agents;
- [ ] publish vanilla-vs-GIS-skill comparison.

### M2 — OpenMapBench native tasks
Target 30–50 independently verified tasks focused on failure modes underrepresented by fixed-tool benchmarks:

- data discovery and source choice;
- CRS and unit traps;
- stale / incomplete datasets;
- topology and invalid geometry;
- spatial aggregation grain;
- boundary ambiguity;
- raster NoData / resampling;
- network distance vs Euclidean distance;
- reproducibility and provenance.

## Benchmark rules

A benchmark task should:

- have a concrete, checkable result;
- avoid requiring one particular GIS library;
- freeze or version all external data used for scoring;
- record dataset source, timestamp, license, and checksum;
- define tolerance before evaluating agents;
- avoid LLM-as-judge for primary correctness where deterministic comparison is feasible;
- keep hidden/reference outputs separate from agent-visible inputs;
- preserve run metadata sufficiently to reproduce failures.

## License

OpenMapBench code and original benchmark specifications in this repository are licensed under Apache-2.0.

Third-party benchmark tasks and datasets retain their own licenses. Adapters do not imply relicensing of upstream material.
