# Run manifests and reports

Every `openmapbench run` invocation creates a unique run directory and writes `manifest.json`, even
when the agent fails, times out, omits the artifact, or triggers an evaluator error.

The manifest records:

- run ID, timestamps, duration, and terminal status;
- task identity, category, output kind, task checksum, and benchmark commit;
- resolved input paths, sizes, and checksums;
- the exact agent argument vector and optional agent/model/skill/tool metadata;
- Python and platform metadata without copying arbitrary environment values;
- candidate and reference paths, sizes, and checksums;
- detected token usage, model, reasoning effort, and a cost estimate when supported;
- subprocess exit code, deterministic evaluation checks, diagnostics, or explicit error.

Run-manifest schema `0.2` adds `token_usage` and `cost_estimate`; schema `0.1` remains readable.
Codex CLI `--json` output can provide input, cached-input, cache-write, output, and reasoning-output
token categories. Without JSON output, OpenMapBench recovers the final `tokens used` total from
`agent.stderr.log`. Total-only usage receives a cost range because the mix of differently priced
token categories is unknown. Detailed category usage receives a point estimate.

Pricing is model-specific, dated, and includes the official source URL in every cost record. It is
an API-equivalent list-price estimate for comparisons—not a statement of actual ChatGPT billing.
Unknown models retain token statistics without a cost estimate.

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
