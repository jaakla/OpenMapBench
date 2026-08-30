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
- invalid-manifest diagnostics.

JSON is the default. A `.md` or `.markdown` output suffix produces a human-readable summary while
preserving the same strict score semantics.
