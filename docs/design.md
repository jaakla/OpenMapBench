# Design

## Capability being measured

OpenMapBench aims to measure **autonomous GIS analysis capability**, not competence with one predefined GIS API.

The benchmark fixes the problem, data, output contract, and evaluator. It does not fix the implementation.

```text
task + frozen inputs
        |
        v
 agent under evaluation
        |
        +--> any allowed GIS/runtime tools
        |
        v
 result artifact
        |
        v
 deterministic/tolerance-aware evaluator
```

## Separation of concerns

### Benchmark
Defines what must be solved and how correctness is judged.

### Agent adapter
Knows how to invoke a particular agent and provide its workspace.

### Tool environment
May expose Python, shell, QGIS, DuckDB, PostGIS, MCP servers, web/data APIs, or skills.

### Evaluator
Reads only the candidate artifact, reference artifact, and task evaluation contract.

A benchmark result should remain valid if the agent implementation changes but produces the same artifact.

## Reproducibility

Each run should eventually record:

- benchmark commit SHA;
- task ID and task schema version;
- dataset hashes;
- model/provider/version;
- agent implementation/version;
- enabled skills/instructions;
- available tools;
- wall-clock runtime;
- token usage and cost where available;
- candidate artifact hashes;
- evaluator version;
- strict result and diagnostic scores.

## Public vs hidden ground truth

Development tasks may have public reference outputs.

Leaderboard/test tasks should support hidden reference outputs to reduce benchmark gaming and contamination. The same evaluator code can execute in a private grading environment.

## Scope evolution

### Phase 1: supplied-data analysis
Question + frozen datasets -> result.

### Phase 2: analyst workflow
Question -> dataset discovery -> provenance/freshness assessment -> transformation -> analysis -> validated result.

The second phase is intentionally harder and should not be mixed into the first benchmark score until its data-access environment is reproducible.
