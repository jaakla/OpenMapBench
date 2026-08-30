# GABench adapter

The goal of this adapter is to use GeoAgentBench / GABench as an initial source of realistic GIS tasks while changing the evaluation question from:

> Did the agent follow the expected GIS tool trajectory?

to:

> Did the agent produce the correct geospatial result?

## Why no vendored GABench content?

At the time this scaffold was created, the upstream GABench GitHub repository did not declare a repository license. OpenMapBench therefore does not copy its benchmark CSV, task text, datasets, or ground-truth files.

Clone GABench separately:

```bash
git lfs install
git clone https://github.com/GeoX-Lab/GABench.git ../GABench
```

Then build a local manifest:

```bash
python adapters/gabench/import_tasks.py \
  --source ../GABench \
  --output .openmapbench/gabench-manifest.json
```

The generated manifest is ignored by git.

## Adapter strategy

The importer is deliberately conservative. It:

1. verifies that `benchmark/benchmark.csv` exists and is not an unresolved Git LFS pointer;
2. reads the actual upstream column names;
3. emits a local manifest containing each row and source-relative references;
4. does **not** copy upstream data.

The next implementation step is to add an explicit field mapping once the downloaded GABench CSV is inspected locally.

That mapping should classify each task into one of:

- deterministic scalar/table;
- deterministic vector;
- deterministic raster;
- map/cartographic output requiring a separate visual-quality metric;
- unsupported / ambiguous.

Tool-chain metrics such as TAO/TIO/TEM/PEA should remain optional diagnostics, not primary correctness metrics.
