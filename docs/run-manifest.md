# Run manifests and reports

Every `openmapbench run` invocation creates a unique run directory and writes `manifest.json`, even
when the agent fails, times out, omits the artifact, or triggers an evaluator error.

The manifest records:

- run ID, timestamps, duration, and terminal status;
- task identity, category, output kind, task checksum, and benchmark commit;
- resolved input paths, sizes, and checksums;
- the exact agent argument vector and optional agent/model/skill/tool metadata;
- a sequenced execution audit with the runner invocation, observed inner commands and tool calls,
  their parameters and results, and the evaluator step;
- task, input, intermediate, working, candidate, reference, and log artifacts with hashes and
  evidence-labelled lineage links;
- Python and platform metadata without copying arbitrary environment values;
- candidate and reference paths, sizes, and checksums;
- detected token usage, model, reasoning effort, and a cost estimate when supported;
- subprocess exit code, deterministic evaluation checks, diagnostics, or explicit error.

Run-manifest schema `0.3` adds the `audit` block. Schema `0.2` added `token_usage` and
`cost_estimate`; schemas `0.1` and `0.2` remain readable.
Codex CLI `--json` output can provide input, cached-input, cache-write, output, and reasoning-output
token categories. Without JSON output, OpenMapBench recovers the final `tokens used` total from
`agent.stderr.log`. Total-only usage receives a cost range because the mix of differently priced
token categories is unknown. Detailed category usage receives a point estimate.

Pricing is model-specific, dated, and includes the official source URL in every cost record. It is
an API-equivalent list-price estimate for comparisons—not a statement of actual ChatGPT billing.
Unknown models retain token statistics without a cost estimate.

## Execution audit

Every new run contains `audit.events` in display order. The first event is always the exact agent
subprocess invocation, including its argument vector, working directory, timeout, and the values of
the runner-owned `OPENMAPBENCH_*` variables. The last event records evaluation or explains why it
was skipped. Inner events use `parent_event_id: runner-agent-process`, making the subprocess and its
actions a layered timeline rather than an unrelated list.

When an agent emits Codex JSONL, OpenMapBench merges the `item.started` and `item.completed` records
for each action and records command executions, MCP tool calls, web searches, and file changes in
their original source-line order. Command output is not copied again into the manifest; its exact
source line and the lossless `agent.stdout.log` are recorded. See the official
[Codex non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

`audit.inner_trace_status` is `captured`, `partial`, or `unavailable`. `unavailable` explicitly
means that the runner could not observe inner actions—not that the agent used no tools.

### Vendor-neutral agent audit JSONL

The runner exposes `OPENMAPBENCH_AUDIT_PATH`, normally `<run-dir>/agent.audit.jsonl`. Any agent or
wrapper can append one JSON object per action. A command or tool event may declare artifacts it
produced:

```json
{"type":"command","event_id":"clip","name":"Clip roads","command":["ogr2ogr","clipped.gpkg","roads.gpkg","-clipsrc","bounds.geojson"],"status":"completed","artifacts":[{"artifact_id":"clipped-roads","path":"clipped.gpkg","role":"intermediate","derived_from":["roads.gpkg","bounds.geojson"]}]}
{"type":"tool","event_id":"style","status":"completed","tool":{"server":"qgis","name":"set_layer_style","parameters":{"layer":"roads","style":"primary"}}}
```

Supported event fields are `event_id`, `parent_event_id`, `type`/`kind`, `name`, `status`,
`command`, `tool`, `parameters`, `result`, `details`, `started_at`, `finished_at`, and `artifacts`.
An artifact supports `artifact_id`, `path`, `role`, `produced_by`, `derived_from`, and `metadata`.
Paths are resolved relative to the agent working directory.

### Artifact lineage

`audit.artifacts` stores the final existence state, SHA-256, size, media type, and lineage for each
observed file. Each lineage link includes its evidence so reviewers can distinguish:

- `runner_observation`: a file was observed inside the isolated run directory after the agent;
- `task_contract`: an input was declared by the benchmark task;
- `codex_jsonl`: a Codex file-change item named the file;
- `agent_reported`: the agent or wrapper explicitly declared the relation.

OpenMapBench does not invent dependency edges between intermediate files. Exact `derived_from`
relationships require agent-reported audit records; otherwise the manifest records only the
producer or declared-input relationship that it can support.

Recover these fields for manifests created before schema `0.2` with:

```bash
openmapbench usage-backfill runs/gabench/<batch-id>/task-runs
```

The command reads the already retained stdout/stderr logs, validates the enriched manifest, and
leaves runs with no recognizable usage unchanged. It is safe to rerun.

Terminal statuses are `passed`, `failed`, `needs_review`, `agent_error`, `missing_output`, and
`evaluator_error`. A decodable image with no deterministic evaluator becomes `needs_review`; this
is neither an implicit pass nor an evaluator failure. Only `passed` contributes to strict
successes, and `needs_review` is excluded from the strictly scored denominator.

`openmapbench report <run-root>` validates every discovered run manifest and emits:

- attempted task count;
- strictly scored task count and pending manual-review count;
- strict successes and strict success rate;
- status counts;
- category and output-kind breakdowns;
- compact per-run outcomes;
- total token use and min/average/max tokens per task, overall and per model;
- per-task and aggregate API-equivalent cost estimates or ranges;
- invalid-manifest diagnostics.

JSON is the default. A `.md` or `.markdown` output suffix produces a human-readable summary while
preserving the same strict score semantics.
