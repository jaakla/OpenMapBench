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
- subprocess exit code, deterministic evaluation checks, diagnostics, or explicit error.

Terminal statuses are `passed`, `failed`, `agent_error`, `missing_output`, and `evaluator_error`.
Only `passed` contributes to strict successes.

`openmapbench report <run-root>` validates every discovered run manifest and emits:

- attempted task count;
- strict successes and strict success rate;
- status counts;
- category and output-kind breakdowns;
- compact per-run outcomes;
- invalid-manifest diagnostics.

JSON is the default. A `.md` or `.markdown` output suffix produces a human-readable summary while
preserving the same strict score semantics.
