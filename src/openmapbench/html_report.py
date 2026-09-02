"""Detailed, self-contained HTML report for a directory of OpenMapBench runs.

The page is deliberately dependency-free: one file, inline CSS and a few lines of filter
JavaScript, so a batch report can be opened straight from disk or attached to a review.
It reads only run manifests and the task contracts they point at.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import RunManifest, RunStatus, TaskSpec
from .reporting import aggregate_manifests, load_manifests
from .taskio import load_task
from .visual import AUDIT_CSS, audit_html, format_bytes, json_block

STATUS_ORDER: tuple[RunStatus, ...] = (
    RunStatus.PASSED,
    RunStatus.FAILED,
    RunStatus.NEEDS_REVIEW,
    RunStatus.MISSING_OUTPUT,
    RunStatus.AGENT_ERROR,
    RunStatus.EVALUATOR_ERROR,
)

STATUS_TEXT: dict[RunStatus, str] = {
    RunStatus.PASSED: "passed",
    RunStatus.FAILED: "failed",
    RunStatus.NEEDS_REVIEW: "needs review",
    RunStatus.MISSING_OUTPUT: "missing output",
    RunStatus.AGENT_ERROR: "agent error",
    RunStatus.EVALUATOR_ERROR: "evaluator error",
}

DEFAULT_NOTICE = (
    "Strict success is binary and artifact-based: a task passes only when every required "
    "check passes. Diagnostics report closeness and never convert a near miss into a pass. "
    "Raster and image artifacts have no strict evaluator and stay outside the score. Costs "
    "are API-equivalent list-price estimates, not billed amounts."
)

MAX_LOG_TAIL_CHARS = 6000


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _attribute(value: str) -> str:
    return html.escape(value, quote=True)


def _status_class(status: str) -> str:
    return status.replace("_", "-")


def _percent(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "—"


def _timestamp(value: Any) -> str:
    """Render an ISO timestamp as a readable UTC instant, falling back to the raw text."""
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _thousands(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else "—"


def _cost_text(manifest: RunManifest) -> str:
    cost = manifest.cost_estimate
    if cost is None:
        return "—"
    if cost.estimated_cost_usd is not None:
        return f"${cost.estimated_cost_usd:.4f}"
    return f"${cost.minimum_cost_usd:.4f}–${cost.maximum_cost_usd:.4f}"


def _total_cost_text(cost: dict[str, Any], *, missing: str = "not available") -> str:
    if cost.get("estimated_cost_usd") is not None:
        return f"${cost['estimated_cost_usd']:.4f}"
    if cost.get("minimum_cost_usd") is not None:
        return f"${cost['minimum_cost_usd']:.4f}–${cost['maximum_cost_usd']:.4f}"
    return missing


def _file_link(path: Path | str | None, *, label: str | None = None) -> str:
    """Link a local file when it still exists; otherwise show its path as plain text."""
    if not path:
        return '<span class="muted">—</span>'
    resolved = Path(str(path))
    text = _escape(label or resolved.name or str(resolved))
    if not resolved.exists():
        return f'<span class="muted" title="{_attribute(str(resolved))}">{text}</span>'
    return (
        f'<a href="{_attribute(resolved.resolve().as_uri())}" '
        f'title="{_attribute(str(resolved))}">{text}</a>'
    )


def _task_spec(path: str | None) -> TaskSpec | None:
    if not path:
        return None
    task_path = Path(path)
    if not task_path.is_file():
        return None
    try:
        return load_task(task_path)
    except Exception:  # noqa: BLE001 - a stale contract must not break the report
        return None


def _log_tail(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    content = content.strip()
    if not content:
        return None
    if len(content) > MAX_LOG_TAIL_CHARS:
        return "… earlier output truncated …\n" + content[-MAX_LOG_TAIL_CHARS:]
    return content


def _chips(manifest: RunManifest) -> str:
    chips = [f'<span class="chip">{_escape(manifest.category)}</span>']
    if manifest.output_kind.value != manifest.category:
        chips.append(f'<span class="chip kind">{_escape(manifest.output_kind.value)}</span>')
    for field in ("difficulty", "family"):
        value = manifest.task_metadata.get(field)
        if value:
            chips.append(f'<span class="chip">{_escape(value)}</span>')
    modes = manifest.task_metadata.get("failure_modes") or []
    if isinstance(modes, str):
        modes = [modes]
    chips.extend(f'<span class="chip mode">{_escape(mode)}</span>' for mode in modes)
    return "".join(chips)


def _metrics(manifest: RunManifest) -> str:
    usage = manifest.token_usage
    token_detail = "—"
    if usage:
        parts = [f"{usage.total_tokens:,} total"]
        if usage.input_tokens is not None:
            parts.append(f"{usage.input_tokens:,} in")
        if usage.output_tokens is not None:
            parts.append(f"{usage.output_tokens:,} out")
        if usage.reasoning_output_tokens:
            parts.append(f"{usage.reasoning_output_tokens:,} reasoning")
        token_detail = " · ".join(parts)
    model = (usage.model if usage and usage.model else manifest.agent.get("model")) or "—"
    exit_code = "—" if manifest.exit_code is None else str(manifest.exit_code)
    score = manifest.evaluation.get("score") if manifest.evaluation else None
    cells = [
        ("Duration", f"{manifest.duration_seconds:.2f} s"),
        ("Exit code", exit_code),
        ("Diagnostic score", f"{score:.3f}" if isinstance(score, int | float) else "—"),
        ("Tokens", token_detail),
        ("Estimated cost", _cost_text(manifest)),
        ("Model", _escape(model)),
    ]
    return "".join(
        f'<div class="metric"><span class="metric-label">{label}</span>'
        f'<span class="metric-value">{value}</span></div>'
        for label, value in cells
    )


def _contract_html(manifest: RunManifest, spec: TaskSpec | None) -> str:
    output = spec.output if spec else None
    rows: list[tuple[str, str]] = [
        ("Artifact", f"<code>{_escape(output.path if output else '—')}</code>"),
        ("Kind", _escape(manifest.output_kind.value)),
    ]
    if output and output.layer:
        rows.append(("Layer", f"<code>{_escape(output.layer)}</code>"))
    if output and output.geometry_type:
        rows.append(("Geometry", _escape(output.geometry_type)))
    if output and output.crs:
        rows.append(("CRS", f"<code>{_escape(output.crs)}</code>"))
    if output and output.required_fields:
        rows.append(
            (
                "Required fields",
                " ".join(f"<code>{_escape(field)}</code>" for field in output.required_fields),
            )
        )
    if spec and spec.evaluation.strict:
        rows.append(
            (
                "Strict contract",
                (
                    "<details><summary>declared checks</summary>"
                    f"<pre>{json_block(spec.evaluation.strict)}</pre></details>"
                ),
            )
        )
    body = "".join(
        f'<div class="row"><dt>{label}</dt><dd>{value}</dd></div>' for label, value in rows
    )
    return f'<dl class="contract">{body}</dl>'


def _checks_html(manifest: RunManifest) -> str:
    evaluation = manifest.evaluation
    if not evaluation:
        return (
            '<p class="muted">No evaluation was recorded; the run did not reach the '
            "evaluator.</p>"
        )
    checks = evaluation.get("checks") or []
    if not checks:
        return '<p class="muted">The evaluator recorded no individual checks.</p>'
    rows: list[str] = []
    for check in checks:
        passed = bool(check.get("passed"))
        required = bool(check.get("required", True))
        details = check.get("details") or {}
        detail_cell = (
            f"<details><summary>details</summary><pre>{json_block(details)}</pre></details>"
            if details
            else '<span class="muted">—</span>'
        )
        rows.append(
            f'<tr class="{"check-pass" if passed else "check-fail"}">'
            f'<td><code>{_escape(check.get("id", "check"))}</code></td>'
            f'<td>{"required" if required else "advisory"}</td>'
            f'<td class="verdict">{"pass" if passed else "fail"}</td>'
            f"<td>{detail_cell}</td></tr>"
        )
    return (
        '<table class="checks"><thead><tr><th>Check</th><th>Role</th><th>Verdict</th>'
        f"<th>Evidence</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _diagnostics_html(manifest: RunManifest, *, expand: bool) -> str:
    """Closeness evidence: opened for a failed run, where it names the actual mismatch."""
    diagnostics = (manifest.evaluation or {}).get("diagnostics")
    if not diagnostics:
        return ""
    return (
        f'<details class="block"{" open" if expand else ""}>'
        "<summary>Diagnostics (never affect strict success)</summary>"
        f"<pre>{json_block(diagnostics)}</pre></details>"
    )


def _failure_summary(manifest: RunManifest) -> str:
    if manifest.status == RunStatus.PASSED:
        return ""
    parts: list[str] = []
    if manifest.error:
        parts.append(f"<p><strong>Error:</strong> {_escape(manifest.error)}</p>")
    failed = [
        str(check.get("id"))
        for check in ((manifest.evaluation or {}).get("checks") or [])
        if check.get("required", True) and not check.get("passed")
    ]
    if failed:
        parts.append(
            "<p><strong>Failed required checks:</strong> "
            + ", ".join(f"<code>{_escape(item)}</code>" for item in failed)
            + "</p>"
        )
    if not parts:
        return ""
    return f'<div class="failure">{"".join(parts)}</div>'


def _logs_html(run_dir: Path, *, expand_stderr: bool) -> str:
    """Show the agent's own output; on a bad run its stderr is the first thing to read."""
    blocks: list[str] = []
    for name, label in (
        ("agent.stderr.log", "Agent stderr"),
        ("agent.stdout.log", "Agent stdout"),
    ):
        tail = _log_tail(run_dir / name)
        if tail is None:
            continue
        open_attribute = " open" if expand_stderr and name == "agent.stderr.log" else ""
        blocks.append(
            f'<details class="block"{open_attribute}><summary>{label} ({name})</summary>'
            f"<pre>{_escape(tail)}</pre></details>"
        )
    return "".join(blocks)


def _files_html(manifest: RunManifest, manifest_path: Path) -> str:
    run_dir = manifest_path.parent
    candidate_size = (
        f" ({format_bytes(manifest.candidate.size_bytes)})"
        if manifest.candidate and manifest.candidate.size_bytes is not None
        else ""
    )
    links = [
        f"run directory: {_file_link(run_dir, label=run_dir.name)}",
        f"manifest: {_file_link(manifest_path, label='manifest.json')}",
        f"task: {_file_link(manifest.task_file.path, label='task.yaml')}",
        "candidate: "
        + (
            _file_link(manifest.candidate.path) + candidate_size
            if manifest.candidate
            else '<span class="muted">not produced</span>'
        ),
        "reference: "
        + (
            _file_link(manifest.reference.path)
            if manifest.reference
            else '<span class="muted">—</span>'
        ),
    ]
    return f'<p class="meta">{" · ".join(links)}</p>'


def _run_card(manifest: RunManifest, manifest_path: Path) -> str:
    spec = _task_spec(manifest.task_file.path)
    audit = audit_html(
        manifest.audit.model_dump(mode="json") if manifest.audit else None, str(manifest_path)
    )
    status = manifest.status
    prompt = (
        spec.prompt
        if spec
        else "Prompt unavailable because the task contract could not be read."
    )
    search_terms = " ".join(
        [
            manifest.task_id,
            manifest.task_title,
            status.value,
            manifest.category,
            manifest.output_kind.value,
            " ".join(str(mode) for mode in (manifest.task_metadata.get("failure_modes") or [])),
        ]
    ).lower()
    return f"""
        <article class="card" data-status="{_attribute(status.value)}"
                 data-search="{_attribute(search_terms)}">
          <div class="card-head">
            <div>
              <code class="task-id">{_escape(manifest.task_id)}</code>
              <h2>{_escape(manifest.task_title)}</h2>
              <div class="chips">{_chips(manifest)}</div>
            </div>
            <div class="card-status">
              <span class="pill {_status_class(status.value)}">{STATUS_TEXT[status]}</span>
              <span class="run-id" title="run id">{_escape(manifest.run_id)}</span>
            </div>
          </div>
          <div class="metrics">{_metrics(manifest)}</div>
          {_failure_summary(manifest)}
          <div class="split">
            <section>
              <h3>Task prompt</h3>
              <div class="prompt-text">{_escape(prompt)}</div>
            </section>
            <section>
              <h3>Required artifact</h3>
              {_contract_html(manifest, spec)}
            </section>
          </div>
          <section>
            <h3>Strict checks</h3>
            {_checks_html(manifest)}
          </section>
          {_diagnostics_html(manifest, expand=status == RunStatus.FAILED)}
          {_logs_html(manifest_path.parent, expand_stderr=status != RunStatus.PASSED)}
          {_files_html(manifest, manifest_path)}
          {audit}
        </article>
    """


def _tiles(aggregate: dict[str, Any], batch: dict[str, Any] | None) -> str:
    usage = aggregate["usage"]
    tiles: list[tuple[str, str, str]] = [
        (
            "Strict success rate",
            _percent(aggregate["strict_success_rate"]),
            (
                f"{aggregate['strict_successes']} of "
                f"{aggregate['strictly_scored_tasks']} strictly scored"
            ),
        ),
        (
            "Tasks attempted",
            str(aggregate["attempted_tasks"]),
            f"{batch['skipped_count']} skipped before running"
            if batch
            else "runs found under the run root",
        ),
        (
            "Needs manual review",
            str(aggregate["needs_manual_review"]),
            "outside both sides of the score",
        ),
        (
            "Total tokens",
            f"{usage['total_tokens']:,}",
            f"{usage['runs_with_usage']} of {aggregate['attempted_tasks']} runs reported usage",
        ),
        (
            "Estimated cost",
            _total_cost_text(usage["cost"], missing="—"),
            "API-equivalent list prices"
            if usage["cost"]["priced_runs"]
            else "no run reported a priced model",
        ),
    ]
    if batch and batch.get("duration_seconds") is not None:
        tiles.append(
            (
                "Batch wall time",
                f"{batch['duration_seconds'] / 60:.1f} min",
                f"{batch['executed_count']} tasks executed",
            )
        )
    return "".join(
        f'<div class="tile"><span class="tile-label">{label}</span>'
        f'<span class="tile-value">{value}</span>'
        f'<span class="tile-note">{note}</span></div>'
        for label, value, note in tiles
    )


def _status_bar(status_counts: dict[str, int], total: int) -> str:
    if not total:
        return ""
    segments: list[str] = []
    legend: list[str] = []
    for status in STATUS_ORDER:
        count = status_counts.get(status.value, 0)
        if not count:
            continue
        width = 100 * count / total
        segments.append(
            f'<span class="seg {_status_class(status.value)}" style="width:{width:.4f}%" '
            f'title="{STATUS_TEXT[status]}: {count}"></span>'
        )
        legend.append(
            f'<span class="legend-item"><span class="swatch {_status_class(status.value)}">'
            f"</span>{STATUS_TEXT[status]} · {count}</span>"
        )
    return (
        f'<div class="statusbar">{"".join(segments)}</div>'
        f'<div class="legend">{"".join(legend)}</div>'
    )


def _breakdown_table(caption: str, data: dict[str, dict[str, Any]]) -> str:
    if not data:
        return ""
    rows: list[str] = []
    for key, stats in data.items():
        rate = stats["strict_success_rate"]
        width = 0.0 if rate is None else 100 * rate
        rows.append(
            f"<tr><td>{_escape(key)}</td>"
            f"<td class='num'>{stats['attempted']}</td>"
            f"<td class='num'>{stats['strictly_scored']}</td>"
            f"<td class='num'>{stats['strict_successes']}</td>"
            f"<td class='num'>{stats['needs_manual_review']}</td>"
            f"<td class='rate'><div class='bar'><span style='width:{width:.2f}%'></span></div>"
            f"<span>{_percent(rate)}</span></td></tr>"
        )
    return f"""
      <div class="panel">
        <h3>{_escape(caption)}</h3>
        <table class="breakdown">
          <thead><tr><th>Group</th><th class="num">Runs</th><th class="num">Scored</th>
            <th class="num">Passed</th><th class="num">Review</th><th>Strict rate</th></tr>
          </thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>
    """


def _model_table(usage: dict[str, Any]) -> str:
    by_model = usage.get("by_model") or {}
    if not by_model:
        return ""
    rows = []
    for model, stats in by_model.items():
        tokens = stats["tokens_per_task"]
        rows.append(
            f"<tr><td><code>{_escape(model)}</code></td>"
            f"<td class='num'>{stats['runs']}</td>"
            f"<td class='num'>{stats['total_tokens']:,}</td>"
            f"<td class='num'>{_thousands(tokens['minimum'])}</td>"
            f"<td class='num'>{tokens['average']:,.0f}</td>"
            f"<td class='num'>{_thousands(tokens['maximum'])}</td>"
            f"<td class='num'>{_total_cost_text(stats['cost'])}</td></tr>"
        )
    return f"""
      <div class="panel">
        <h3>Token usage by model</h3>
        <table class="breakdown">
          <thead><tr><th>Model</th><th class="num">Runs</th><th class="num">Tokens</th>
            <th class="num">Min</th><th class="num">Average</th><th class="num">Max</th>
            <th class="num">Estimated cost</th></tr></thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>
    """


def _run_context(batch: dict[str, Any] | None, run_root: Path, created: str) -> str:
    rows: list[tuple[str, str]] = [("Run root", f"<code>{_escape(run_root)}</code>")]
    if batch:
        agent = batch.get("agent") or {}
        source = batch.get("task_root") or batch.get("source_manifest") or "—"
        rows = [
            ("Batch", f"<code>{_escape(batch.get('batch_id', '—'))}</code>"),
            ("Task source", f"<code>{_escape(source)}</code>"),
            ("Agent", _escape(agent.get("name") or "—")),
            ("Model", _escape(agent.get("model") or "—")),
            ("Agent command", f"<code>{_escape(batch.get('agent_command', '—'))}</code>"),
            ("Started", _escape(_timestamp(batch.get("started_at")))),
            ("Finished", _escape(_timestamp(batch.get("finished_at")))),
            ("Run root", f"<code>{_escape(run_root)}</code>"),
        ]
        skills = agent.get("skills") or []
        if skills:
            rows.append(("Skills", " ".join(f"<code>{_escape(s)}</code>" for s in skills)))
    rows.append(("Report generated", _escape(_timestamp(created))))
    body = "".join(
        f'<div class="row"><dt>{label}</dt><dd>{value}</dd></div>' for label, value in rows
    )
    return f'<dl class="context">{body}</dl>'


def _skipped_html(batch: dict[str, Any] | None, invalid: list[dict[str, str]]) -> str:
    items: list[str] = []
    for entry in (batch or {}).get("skipped", []):
        items.append(
            f"<li><code>{_escape(entry.get('task_id', 'unknown'))}</code>: "
            f"{_escape(entry.get('reason', 'unknown reason'))}</li>"
        )
    for entry in invalid:
        items.append(
            f"<li><code>{_escape(entry['path'])}</code>: unreadable manifest — "
            f"{_escape(entry['error'])}</li>"
        )
    if not items:
        return ""
    return f"""
      <section class="panel">
        <h3>Not scored</h3>
        <p class="muted">These tasks never reached the evaluator, so they are absent from both
          sides of the strict rate.</p>
        <ul class="skipped">{"".join(items)}</ul>
      </section>
    """


def _filter_bar(status_counts: dict[str, int], total: int) -> str:
    buttons = [f'<button class="filter is-active" data-filter="all">all · {total}</button>']
    for status in STATUS_ORDER:
        count = status_counts.get(status.value, 0)
        if not count:
            continue
        buttons.append(
            f'<button class="filter {_status_class(status.value)}" '
            f'data-filter="{_attribute(status.value)}">{STATUS_TEXT[status]} · {count}</button>'
        )
    return f"""
      <div class="controls">
        <input id="report-search" type="search"
               placeholder="Filter by task id, title, category, or failure mode…">
        <div class="filters">{"".join(buttons)}</div>
        <span id="filter-count" class="muted"></span>
      </div>
    """


REPORT_CSS = """
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #f5f7fa; color: #17202a; }
    main { max-width: 1500px; margin: 0 auto; padding: 32px 24px 64px; }
    h1 { margin: 0 0 6px; font-size: 2rem; }
    h2 { margin: 7px 0 0; font-size: 1.15rem; }
    h3 { margin: 0 0 9px; font-size: .78rem; letter-spacing: .04em;
         text-transform: uppercase; color: #475569; }
    p { line-height: 1.55; }
    .subtitle { margin: 0 0 20px; color: #475569; }
    .muted { color: #64748b; }
    .panel { background: white; border: 1px solid #dbe2ea; border-radius: 12px;
             padding: 16px 18px; margin: 0 0 18px; }
    .context { display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr));
               gap: 2px 28px; margin: 0; }
    .context .row, .contract .row { display: grid; grid-template-columns: 150px minmax(0, 1fr);
                                    gap: 10px; padding: 6px 0; border-top: 1px solid #eef2f7; }
    .context dt, .contract dt { color: #64748b; font-size: .76rem; font-weight: 700;
                                text-transform: uppercase; }
    .context dd, .contract dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
    .tiles { display: grid; gap: 14px; margin: 0 0 18px;
             grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
    .tile { background: white; border: 1px solid #dbe2ea; border-radius: 12px; padding: 14px 16px;
            display: flex; flex-direction: column; gap: 3px; }
    .tile-label { color: #64748b; font-size: .74rem; font-weight: 700; letter-spacing: .04em;
                  text-transform: uppercase; }
    .tile-value { font-size: 1.85rem; font-weight: 700; line-height: 1.15; }
    .tile-note { color: #64748b; font-size: .82rem; }
    .statusbar { display: flex; height: 16px; border-radius: 999px; overflow: hidden;
                 background: #e2e8f0; }
    .seg { display: block; height: 100%; }
    .legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 10px; color: #475569;
              font-size: .84rem; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 11px; height: 11px; border-radius: 3px; background: #94a3b8; }
    .seg.passed, .swatch.passed { background: #16a34a; }
    .seg.failed, .swatch.failed { background: #dc2626; }
    .seg.needs-review, .swatch.needs-review { background: #f59e0b; }
    .seg.missing-output, .swatch.missing-output { background: #7c3aed; }
    .seg.agent-error, .swatch.agent-error { background: #0891b2; }
    .seg.evaluator-error, .swatch.evaluator-error { background: #64748b; }
    table { width: 100%; border-collapse: collapse; font-size: .9rem; }
    th { text-align: left; color: #64748b; font-size: .74rem; letter-spacing: .04em;
         text-transform: uppercase; padding: 0 8px 7px 0; border-bottom: 1px solid #e2e8f0; }
    td { padding: 7px 8px 7px 0; border-bottom: 1px solid #eef2f7; vertical-align: top; }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .rate { display: flex; align-items: center; gap: 9px; min-width: 170px; }
    .bar { flex: 1; height: 8px; background: #e2e8f0; border-radius: 999px; overflow: hidden; }
    .bar span { display: block; height: 100%; background: #16a34a; }
    .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
                margin: 26px 0 16px; }
    .controls input { flex: 1 1 280px; padding: 9px 12px; border: 1px solid #cbd5e1;
                      border-radius: 9px; font: inherit; background: white; }
    .filters { display: flex; flex-wrap: wrap; gap: 7px; }
    .filter { cursor: pointer; border: 1px solid #cbd5e1; background: white; color: #334155;
              border-radius: 999px; padding: 6px 12px; font: 650 .8rem inherit; }
    .filter.is-active { background: #0f172a; border-color: #0f172a; color: white; }
    .card { background: white; border: 1px solid #dbe2ea; border-radius: 14px; padding: 18px;
            margin: 0 0 22px; box-shadow: 0 8px 28px rgb(15 23 42 / 7%); }
    .card-head { display: flex; justify-content: space-between; gap: 20px; align-items: start; }
    .card-status { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
    .task-id { font: 650 .82rem ui-monospace, monospace; color: #475569; }
    .run-id { color: #94a3b8; font: .72rem ui-monospace, monospace; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .chip { color: #334155; background: #e2e8f0; border-radius: 999px; padding: 3px 9px;
            font-size: .74rem; font-weight: 650; }
    .chip.kind { color: #1e3a8a; background: #dbeafe; }
    .chip.mode { color: #7c2d12; background: #ffedd5; }
    .pill { white-space: nowrap; border-radius: 999px; padding: 6px 12px; font-size: .82rem;
            font-weight: 700; color: #334155; background: #e2e8f0; }
    .pill.passed { color: #166534; background: #dcfce7; }
    .pill.failed { color: #991b1b; background: #fee2e2; }
    .pill.needs-review { color: #92400e; background: #fef3c7; }
    .pill.missing-output { color: #5b21b6; background: #ede9fe; }
    .pill.agent-error { color: #155e75; background: #cffafe; }
    .pill.evaluator-error { color: #334155; background: #e2e8f0; }
    .metrics { display: grid; gap: 10px; margin: 16px 0;
               grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
    .metric { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 9px;
              padding: 9px 11px; display: flex; flex-direction: column; gap: 2px; }
    .metric-label { color: #64748b; font-size: .7rem; font-weight: 700; letter-spacing: .04em;
                    text-transform: uppercase; }
    .metric-value { font-weight: 650; font-size: .95rem; overflow-wrap: anywhere; }
    .failure { background: #fef2f2; border: 1px solid #fecaca; border-radius: 9px;
               padding: 4px 14px; margin: 0 0 16px; color: #7f1d1d; }
    .failure p { margin: 10px 0; font-size: .9rem; }
    .split { display: grid; gap: 18px; margin-bottom: 18px;
             grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); }
    .prompt-text { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 9px;
                   padding: 12px 14px; color: #334155; line-height: 1.5; white-space: pre-wrap;
                   overflow-wrap: anywhere; }
    .checks .verdict { font-weight: 700; }
    .check-pass .verdict { color: #166534; }
    .check-fail .verdict { color: #991b1b; }
    .checks td:first-child { width: 26%; }
    .block { margin: 14px 0; border: 1px solid #dbe2ea; border-radius: 9px; background: #f8fafc; }
    .block > summary { cursor: pointer; padding: 10px 12px; font-weight: 650; }
    .block pre, .checks pre, .contract pre { margin: 0 12px 12px; padding: 9px; max-height: 340px;
              overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: #0f172a;
              color: #e2e8f0; border-radius: 6px; font: .78rem/1.5 ui-monospace, monospace; }
    .checks pre, .contract pre { margin: 6px 0 0; }
    .meta { color: #64748b; font-size: .88rem; margin: 14px 0 0; }
    .skipped { color: #475569; font-size: .9rem; line-height: 1.7; }
    .notice { background: #fff7ed; border: 1px solid #fed7aa; border-radius: 10px;
              padding: 12px 16px; color: #7c2d12; }
    code { color: #475569; }
    a { color: #075985; }
"""

REPORT_JS = """
  (function () {
    const cards = Array.from(document.querySelectorAll('.card'));
    const search = document.getElementById('report-search');
    const counter = document.getElementById('filter-count');
    const buttons = Array.from(document.querySelectorAll('.filter'));
    let active = 'all';
    function apply() {
      const needle = (search ? search.value : '').trim().toLowerCase();
      let shown = 0;
      cards.forEach(function (card) {
        const byStatus = active === 'all' || card.dataset.status === active;
        const byText = !needle || (card.dataset.search || '').indexOf(needle) !== -1;
        const visible = byStatus && byText;
        card.hidden = !visible;
        if (visible) { shown += 1; }
      });
      if (counter) { counter.textContent = shown + ' of ' + cards.length + ' runs shown'; }
    }
    buttons.forEach(function (button) {
      button.addEventListener('click', function () {
        active = button.dataset.filter;
        buttons.forEach(function (other) {
          other.classList.toggle('is-active', other === button);
        });
        apply();
      });
    });
    if (search) { search.addEventListener('input', apply); }
    apply();
  })();
"""


def render_html_report(
    runs: list[tuple[Path, RunManifest]],
    aggregate: dict[str, Any],
    *,
    title: str,
    subtitle: str = "",
    run_root: Path,
    batch: dict[str, Any] | None = None,
    invalid_manifests: list[dict[str, str]] | None = None,
    extra_links: list[tuple[str, Path]] | None = None,
    notice: str = DEFAULT_NOTICE,
) -> str:
    """Render the complete report page for a set of loaded run manifests."""
    created = datetime.now(UTC).isoformat(timespec="seconds")
    status_counts = aggregate["status_counts"]
    total = aggregate["attempted_tasks"]
    ordered = sorted(runs, key=lambda item: (item[1].task_id, item[1].run_id))
    cards = "".join(_run_card(manifest, path) for path, manifest in ordered)
    links = "".join(
        f' · <a href="{_attribute(path.resolve().as_uri())}">{_escape(label)}</a>'
        for label, path in (extra_links or [])
        if path.exists()
    )
    breakdowns = "".join(
        [
            _breakdown_table("Strict success by category", aggregate["by_category"]),
            _breakdown_table("Strict success by output kind", aggregate["by_output_kind"]),
            _breakdown_table(
                "Strict success by tagged failure mode",
                {
                    key: value
                    for key, value in aggregate["by_failure_mode"].items()
                    if key != "untagged"
                },
            ),
            _model_table(aggregate["usage"]),
        ]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
  <style>{REPORT_CSS}{AUDIT_CSS}</style>
</head>
<body><main>
  <header>
    <h1>{_escape(title)}</h1>
    <p class="subtitle">{_escape(subtitle)}{links}</p>
  </header>
  <section class="tiles">{_tiles(aggregate, batch)}</section>
  <section class="panel">
    <h3>Outcome distribution</h3>
    {_status_bar(status_counts, total) or '<p class="muted">No runs were recorded.</p>'}
  </section>
  <section class="panel">
    <h3>Run context</h3>
    {_run_context(batch, run_root, created)}
  </section>
  {breakdowns}
  {_skipped_html(batch, invalid_manifests or [])}
  <p class="notice">{_escape(notice)}</p>
  <h2 id="tasks">Per-task results</h2>
  {_filter_bar(status_counts, total)}
  {cards or '<p class="muted">No run manifests were found under the run root.</p>'}
</main>
<script>{REPORT_JS}</script>
</body></html>
"""


def write_html_report(
    run_root: Path,
    output: Path,
    *,
    title: str = "OpenMapBench report",
    subtitle: str = "",
    batch: dict[str, Any] | None = None,
    aggregate: dict[str, Any] | None = None,
    extra_links: list[tuple[str, Path]] | None = None,
    notice: str = DEFAULT_NOTICE,
) -> dict[str, Any]:
    """Write a detailed HTML report for every run manifest under ``run_root``."""
    run_root = run_root.resolve()
    runs, invalid = load_manifests(run_root)
    aggregate = aggregate if aggregate is not None else aggregate_manifests(run_root)
    page = render_html_report(
        runs,
        aggregate,
        title=title,
        subtitle=subtitle,
        run_root=run_root,
        batch=batch,
        invalid_manifests=invalid,
        extra_links=extra_links,
        notice=notice,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return {
        "path": str(output.resolve()),
        "run_count": len(runs),
        "invalid_manifest_count": len(invalid),
    }


__all__ = ["DEFAULT_NOTICE", "render_html_report", "write_html_report"]
