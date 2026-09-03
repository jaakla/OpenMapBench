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
- preserved content of the transient files the run created, used, or deleted;
- Python and platform metadata without copying arbitrary environment values;
- candidate and reference paths, sizes, and checksums;
- detected token usage, model, reasoning effort, and a cost estimate when supported;
- subprocess exit code, deterministic evaluation checks, diagnostics, or explicit error.

Run-manifest schema `0.4` adds `task_metadata`, the task's free-form metadata (including
`failure_modes` tags) frozen at run time so reports can group by it. Schema `0.3` added the
`audit` block. Schema `0.2` added `token_usage` and `cost_estimate`; schemas `0.1` to `0.3`
remain readable. Inside the block, `audit.schema_version`
`0.2` adds `audit.content_store` and per-artifact `content_captures`.
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

Use `openmapbench run -v` (or `--verbose`) to show the same useful action progress in the terminal
as the run proceeds. JSONL command and tool events are summarized rather than dumped as raw JSON;
the exact streams are still written to `agent.stdout.log` and `agent.stderr.log`.

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
Paths are resolved relative to the agent working directory, which defaults to the run's own
`workspace/` folder. Files left there are inventoried as intermediate artifacts.

### Preserved content of transient files

Agents routinely put the logic that decides an output into a throwaway helper script—
`render_heat.py`, `.tmp_make_map.py`—run it, and delete it before exiting. A path and a checksum of
a file that no longer exists cannot be reviewed or reproduced, so the runner reads the agent stream
while the agent is still running and copies observed files into `<run-dir>/captured-files/`.

Content is preserved when the runner sees a file:

- named by a Codex `file_change` item, including the version that a later item deletes;
- named anywhere in a `command_execution` command, heredoc bodies included;
- created or modified under the agent working directory during the run, found by a bounded sweep
  after each observed action and once more when the agent exits.

A file is never copied twice: the store is addressed by content, and repeated observations of the
same bytes only append to that version's `observations`. Every version records when it was first and
last seen, how it was observed, and which event was running at the time.

Nothing is duplicated needlessly. The task file, declared inputs, the reference, and everything
already kept inside the run directory are exempt, except the agent's `workspace/` folder, whose
files are transient agent work rather than runner output. Only files whose modification time falls
inside the run are considered—reading an unchanged project file copies nothing.

`audit.content_store` indexes the store and lists what was deliberately left out, so a reviewer can
tell an empty store from a suppressed one:

```json
{"path":"captured-files","file_count":2,"version_count":3,"total_bytes":8134,
 "policy":{"max_file_bytes":4194304,"max_total_bytes":268435456,"sweep_depth":4},
 "skipped":[{"path":"/tmp/tiles.gpkg","reason":"exceeds_max_file_bytes","size_bytes":91234567}]}
```

Each artifact carries the matching versions in `content_captures`, with `stored_path` relative to
the run directory, the SHA-256, size, line count, and whether the bytes decode as UTF-8. An artifact
whose `exists_at_finish` is `false` but whose `content_captures` is non-empty is exactly the case
this exists for: the file is gone, its content is not. The visual review report inlines preserved
text so the deciding logic is readable beside the image it produced.

Four environment variables bound the store; set any of them before `openmapbench run`:

| Variable | Default | Effect |
| --- | --- | --- |
| `OPENMAPBENCH_AUDIT_CAPTURE` | `1` | `0`, `false`, `off`, or `no` disables capture entirely |
| `OPENMAPBENCH_AUDIT_CAPTURE_MAX_FILE_BYTES` | `4194304` | Largest single file preserved |
| `OPENMAPBENCH_AUDIT_CAPTURE_MAX_TOTAL_BYTES` | `268435456` | Total store budget for one run |
| `OPENMAPBENCH_AUDIT_CAPTURE_SWEEP_DEPTH` | `4` | Sweep depth below the agent working directory |

The sweep never descends into `.git`, `.venv`, `node_modules`, `__pycache__`, `site-packages`, or
the other build and cache directories listed in `audit.content_store.policy`. When capture is
disabled, `audit.content_store` is absent rather than empty.

The store holds verbatim bytes, so it is exactly as sensitive as whatever the agent wrote next to
its working directory. Treat `<run-dir>/captured-files/` like the `agent.stdout.log` beside it, and
lower the limits or disable capture for runs that touch credentials.

### Artifact lineage

`audit.artifacts` stores the final existence state, SHA-256, size, media type, preserved content,
and lineage for each observed file. Each lineage link includes its evidence so reviewers can distinguish:

- `runner_observation`: a file was observed inside the isolated run directory after the agent;
- `task_contract`: an input was declared by the benchmark task;
- `codex_jsonl`: a Codex file-change item named the file;
- `agent_reported`: the agent or wrapper explicitly declared the relation;
- `runner_content_capture`: the runner preserved the file's content while that event was running.

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

JSON is the default. A `.md` or `.markdown` output suffix produces a human-readable summary, and
an `.html` or `.htm` suffix produces the detailed browsable report described below. All three
carry the same strict score semantics.

## The HTML report

`openmapbench report <run-root> --output report.html` and both batch runners write a single
self-contained page: inline CSS, a few lines of filter JavaScript, no network requests, no
external assets. It is a view over the run manifests, never a second source of truth — every
number on it comes from `report.json`, and every per-run detail comes from that run's manifest
and the task contract the manifest points at.

The page carries, in order:

- headline tiles: strict success rate, attempted tasks, pending manual reviews, total tokens,
  estimated cost, and batch wall time;
- the outcome distribution as one bar, with a legend per terminal status;
- run context: batch ID, task source, agent name, model, the exact agent command, and timings;
- strict success broken down by category, output kind, and tagged failure mode, plus token use
  and cost per model;
- tasks that were never scored, with the reason each was skipped and any unreadable manifest;
- one card per run: status, task metadata chips, duration, exit code, diagnostic score, tokens
  and cost, the task prompt, the declared artifact contract, every strict check with its
  evidence, diagnostics, the agent's own stdout and stderr tails, links to the run directory,
  manifest, candidate and reference, and the full execution audit from
  [Execution audit](#execution-audit) above.

Failure evidence is expanded by default and success evidence is collapsed: a failed run opens its
diagnostics, and any run that did not pass opens its stderr tail. The per-task cards can be
filtered by status or searched by task ID, title, category, or failure mode.

A `needs_review` run — a decodable image with no deterministic evaluator — carries an explicit
notice saying it was neither passed nor failed and is excluded from both sides of the strict rate;
its only check, `image_decodable`, asserts nothing more than that the file decodes. When the batch
also built a visual review, each such card embeds that run's side-by-side sheet, generated output
on the left and expected reference on the right, with the decision recorded in `review.csv` and a
link to the review page. The report shows the comparison; it never scores it.

## Batch bundles

Both batch runners — `scripts/run_benchmark_all.py` for the native suite and
`scripts/run_gabench_all.py` for the GABench bridge — write the same bundle:

```text
<output-root>/<batch-id>/
├── batch.json     # batch provenance, per-task outcomes, and skipped-task reasons
├── report.json    # aggregate score, token, model, and cost statistics
├── report.md      # readable score, usage summary, and per-task table
├── report.html    # the detailed report described above
├── task-runs/     # immutable artifact, log, and manifest folder for every task
└── visual-review/ # only when the batch produced image artifacts
```

`batch.json` for the native suite is schema `0.1`; the GABench batch manifest moved to `0.3` when
`aggregate_report.html` was added. Both record `task_root` or `source_manifest`, the agent command
and metadata, start and finish timestamps, per-task outcomes with run IDs and manifest paths, and
the reason every skipped task was skipped.
