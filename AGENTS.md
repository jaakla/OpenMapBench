# AGENTS.md

Guidance for coding agents (Claude Code, Codex, Cursor, and similar) working in this repository.
Humans are welcome to read it too; CONTRIBUTING.md remains the authority on benchmark task quality.

## What this project is

OpenMapBench is a tool-agnostic benchmark for autonomous GIS analysis agents. It scores whether
an agent produced the correct analytical artifact (a number, a table, a vector file) against a
frozen reference under a declared tolerance. It never scores the tool chain, library, or call
sequence the agent used. Every design decision follows from one sentence:

> Score the result, not the tool trajectory.

If a change would make correctness depend on a specific tool, library, or step order, it is
probably wrong for this project. See `docs/design.md` for the separation of concerns.

## Repository map

```text
src/openmapbench/
  models.py        Pydantic models: TaskSpec (schema 0.1), RunManifest (schema 0.3), audit types
  taskio.py        Load and validate task YAML, checksum inputs
  evaluator.py     Deterministic scalar/JSON, table, and vector evaluators
  vector_checks.py Reference-independent vector predicates (within, count_per_input, ...)
  runner.py        run_task(): launch agent subprocess, evaluate, always write a manifest
  audit.py         Execution audit trail and artifact lineage (Codex JSONL + generic JSONL)
  capture.py       Live content capture of transient agent files into <run_dir>/captured-files/
  usage.py         Token usage parsing and backfill
  pricing.py       Dated, source-linked model price catalog; cost estimates
  reporting.py     Aggregate manifests into JSON/Markdown reports; load_manifests()
  html_report.py   Detailed self-contained HTML report over a directory of runs
  preview.py       Render vector/raster artifacts as reference|candidate|overlay sheets
  visual.py        Review sheets and HTML index for map review; AUDIT_CSS, audit_html() are
                   shared with html_report.py
  batch.py         Shared batch plumbing: batch IDs, report writing, status roll-ups
  benchmark_batch.py   Run every native benchmark task with one agent command
  gabench_batch.py Run every imported GABench task with one agent command
  adapters/gabench.py  Import GABench tasks from an external checkout (no vendoring)
  cli.py           Typer CLI: validate, evaluate, run, run-suite, report, usage-backfill,
                   gabench-*, visual-report
scripts/run_benchmark_all.py Thin entry point for benchmark_batch.main
scripts/run_gabench_all.py   Thin entry point for gabench_batch.main
adapters/gabench/README.md   How the GABench bridge works and its license boundary
benchmark/examples/          Tiny runnable example tasks (sum-values, buffer-schools)
benchmark/tasks/<id>/        Native tasks: task.yaml, inputs/ (+README provenance), reference/, tools/
docs/                        design, task-contract, scoring, run-manifest, candidates/
tests/                       pytest suite; every evaluator and runner path has a fixture test
runs/, visual-reviews/, .openmapbench/, report.*   Local outputs, gitignored, never commit
```

## Setup and commands

Python 3.11+ and uv.

```bash
uv sync --no-editable --extra geo --extra dev   # geo extra = GeoPandas/Shapely/PyProj/rasterio
uv run --no-sync ruff check .                   # lint, line length 100, target py311
uv run --no-sync pytest -q                      # full suite, runs in a few seconds
uv run openmapbench validate benchmark/examples/sum-values/task.yaml
uv run openmapbench run benchmark/examples/sum-values/task.yaml \
  --reference benchmark/examples/sum-values/reference/result.txt \
  --agent-command ".venv/bin/python benchmark/examples/sum-values/solve.py" \
  --run-root runs
uv run openmapbench report runs --output report.md
uv run openmapbench report runs --output report.html   # detailed browsable report
uv run --no-sync python scripts/run_benchmark_all.py --reference-solver   # whole-suite smoke test
```

CI runs exactly `ruff check .` and `pytest -q`. Both must pass before a task is declared done.

## Working conventions

- **Commit after every meaningful change**, one topic per commit, conventional commit prefixes
  (`feat:` `fix:` `docs:` `refactor:` `test:` `chore:`), optional scope such as `feat(runner):`.
  The body says what changed and why.
- **Keep docs in sync in the same commit.** README.md is the user manual; `docs/task-contract.md`
  documents every evaluator option; `docs/run-manifest.md` documents every manifest field;
  `docs/scoring.md` explains what counts. A code change to any of those areas without the matching
  doc change is incomplete.
- **Tests use `tmp_path` fixtures**, write small synthetic tasks and references inline, and drive
  the real `run_task` or CLI (`typer.testing.CliRunner`). Follow that style; do not add fixture
  files unless a binary format truly requires them.
- **Type everything.** Pydantic models for anything serialized; modules start with
  `from __future__ import annotations` and use 3.11 union syntax (`X | None`).
- **Schema versions are contracts.** `TaskSpec.schema_version` is `"0.1"`; `RunManifest` accepts
  `"0.1"` through `"0.4"` and writes `0.4`. Adding a manifest field means bumping the version,
  keeping older versions loadable, and noting the change in `docs/run-manifest.md`. Adding a task
  field must stay backward compatible within `0.1` or bump it. Batch manifests carry their own
  versions: `0.1` for the native suite bundle, `0.3` for the GABench bundle.
- No scratch files in the project root. Use the session scratchpad or a run's `workspace/`
  folder.

## Invariants that must not be broken

1. **Strict success is binary and artifact-based.** A task passes only if every declared strict
   check passes. Diagnostics may report closeness, never convert a near miss into a pass.
2. **Raster and image outputs are not strict passes.** The `raster` and `file` output kinds
   exist in the contract but have no strict evaluator; runs land in `evaluator_error` or
   `needs_review`. Do not make them count in `strict_success_rate` until a deterministic
   evaluator exists and is documented in `docs/scoring.md`.
3. **Tolerances are declared by the task, never tuned after seeing agent output.** When adding a
   tolerance option, document the mechanism it compensates for in `docs/task-contract.md`.
4. **References are isolated.** Reference paths come from the CLI or a batch manifest, never
   from the task YAML the agent sees.
5. **No vendoring of GABench.** GeoX-Lab/GABench has no license. The adapter reads an external
   checkout and writes only to a user-chosen local directory. Never copy its CSV, prompts, data,
   or reference images into this repository, and never commit `.openmapbench/` or
   `visual-reviews/`.
6. **Agent commands run without a shell.** `_render_command` uses `shlex.split` and placeholder
   substitution; pipes and redirects belong in a wrapper script. Placeholders and matching
   `OPENMAPBENCH_*` variables are `task_file`, `task_dir`, `output_dir`, `output_path`,
   `run_dir`, `workspace_dir`, plus `OPENMAPBENCH_AUDIT_PATH`.
7. **The agent runs from `<run_dir>/workspace/` by default.** That keeps agent scratch files
   inside the gitignored, audited run directory. Only pass `--agent-cwd` when the agent must see
   project-level files.
8. **Transient agent files are captured, not just named.** `capture.py` watches the agent
   stream and working directory and copies changed files into `<run_dir>/captured-files/` so a
   script the agent deletes before exiting is still reviewable. The workspace is capturable; the
   rest of the run directory, task inputs, and the reference are exempt. `OPENMAPBENCH_AUDIT_CAPTURE=0`
   disables it. Do not weaken this without updating `docs/run-manifest.md` and `tests/test_audit.py`.
9. **The manifest is always written**, even on agent error, timeout, missing output, or
   evaluator crash. Any new runner code path must preserve this.
10. **Costs are estimates.** Pricing entries carry a date and a source URL. Unknown models stay
   unpriced rather than borrowing another model's rates.
11. **The evaluator reads only candidate, reference, and the task's evaluation block.** It must
    not inspect logs, trajectories, or the agent's environment.
12. **Reports present, never decide.** `report.html` is a view over run manifests and
    `report.json`; every number on it must already exist there. It may show logs, prompts,
    audit trails, and rendered artifact previews as evidence, but it must never compute a
    verdict of its own or turn a diagnostic into a pass. The page stays self-contained:
    inline CSS and JS, no network requests, no external assets.
    Previews follow the same rule from the other side: `preview.py` runs after evaluation,
    reads only the candidate and reference files, and is best effort — an artifact it cannot
    draw is recorded as skipped, never raised. `review.csv` lists only decisions a human still
    owes, so a strictly scored run never appears in it.
13. **A task that cannot be scored fairly is skipped, not failed.** The suite runner skips a task
    with no reference artifact, a malformed contract, or inputs that no longer match their
    checksums, records the reason in `batch.json` and the reports, and keeps it out of both sides
    of the strict rate.

## How to extend the evaluators

1. Add the option to the relevant `strict` config handling in `evaluator.py` and, if it affects
   the artifact contract, to `OutputSpec` in `models.py`.
2. Add a `tests/test_<kind>_evaluator.py` case that exercises both the pass and the fail side.
3. Document the option, its default, and its rationale in `docs/task-contract.md`.
4. If it changes what counts as a pass, update `docs/scoring.md`.

The vector evaluator supports union and per-entity geometry matching (`geometry.match`),
`metric: ignore`, and reference-independent `vector_checks`; table and attribute comparison is
null-aware; reports break success down by `metadata.failure_modes`. The remaining known gap is
multi-artifact tasks (one task, several scored outputs); pair tasks instead. Motivating cases are
in `docs/candidates/gisagentbench-case-studies.md`.

## How to add a native benchmark task

Follow CONTRIBUTING.md in full. In practice a task directory contains `task.yaml`, an `inputs/`
folder with frozen data and a README naming source, license, acquisition date and checksum, and a
`reference/` artifact produced by an independently reviewed method. `openmapbench validate` must
pass, and `openmapbench run` with a trivial solver must exercise the strict contract end to end.
Tag the GIS failure mode the task targets under `metadata` (CRS, units, topology, NoData,
boundary handling). Prefer keyed aggregates over per-segment outputs when the natural output has
no stable entity key. The suite runner pairs each task with `<task-dir>/reference/<artifact>`, so
the reference file must be named exactly like the declared `output.path`, and
`python scripts/run_benchmark_all.py --reference-solver --task <id>` must end in `passed`.

## When you are the agent being evaluated

If you are invoked through `openmapbench run`, `openmapbench run-suite`, or either batch
runner:

- Read the task YAML at `OPENMAPBENCH_TASK_FILE`; its `inputs` are the only data you may rely on.
- Write the artifact exactly to `OPENMAPBENCH_OUTPUT_PATH`, in the declared kind, geometry type,
  CRS, and required fields. Producing an explanation without the file is a failure.
- Your working directory is the run's `workspace/`; put helper scripts and intermediates there.
  They are inventoried and their content is captured even if you delete them, so there is no need
  to clean up after yourself.
- Do not modify input files or anything outside the run directory.
- Optionally append vendor-neutral JSONL events to `OPENMAPBENCH_AUDIT_PATH` describing commands,
  tools, and derived artifacts; the format is in `docs/run-manifest.md`.

## Where things are heading

`ROADMAP.md` tracks the issues. Open work: a native v0 task set emphasizing real GIS failure modes
(Issue 8), the first vanilla-versus-skill comparative evaluation (Issue 7), raster evaluation, and
fuzzy or semantic map-image judging that stays clearly separated from deterministic scoring.
