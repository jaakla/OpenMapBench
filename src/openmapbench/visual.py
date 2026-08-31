from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from .models import RunManifest
from .taskio import load_task, sha256_file

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class VisualPair:
    task_id: str
    title: str
    prompt: str
    candidate: Path
    reference: Path
    run_id: str | None = None
    source_status: str | None = None
    audit: dict[str, Any] | None = None
    run_manifest_path: Path | None = None


def is_supported_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def image_metadata(path: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        source.verify()
    with Image.open(path) as source:
        return {
            "format": source.format,
            "mode": source.mode,
            "width": source.width,
            "height": source.height,
            "frames": int(getattr(source, "n_frames", 1)),
        }


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow 10.4 compatibility
        return ImageFont.load_default()


def _load_scaled(path: Path, max_width: int, max_height: int) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return image


def compose_side_by_side(
    candidate: Path,
    reference: Path,
    output: Path,
    *,
    title: str,
    expected_label: str = "EXPECTED (REFERENCE)",
    max_panel_width: int = 1200,
    max_panel_height: int = 1000,
) -> dict[str, Any]:
    """Create a labeled, lossless PNG with generated output left and reference right."""
    if max_panel_width < 100 or max_panel_height < 100:
        raise ValueError("panel dimensions must be at least 100 pixels")
    candidate_meta = image_metadata(candidate)
    reference_meta = image_metadata(reference)
    generated = _load_scaled(candidate, max_panel_width, max_panel_height)
    expected = _load_scaled(reference, max_panel_width, max_panel_height)

    margin = 24
    gap = 16
    header_height = 58
    label_height = 38
    panel_width = max(generated.width, expected.width, 320)
    panel_height = max(generated.height, expected.height, 200)
    canvas_width = margin * 2 + panel_width * 2 + gap
    canvas_height = margin * 2 + header_height + label_height + panel_height
    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(20)
    label_font = _font(17)

    safe_title = title if len(title) <= 160 else f"{title[:157]}..."
    draw.text((margin, margin), safe_title, fill="#17202a", font=title_font)
    panel_top = margin + header_height
    left_x = margin
    right_x = margin + panel_width + gap
    draw.rounded_rectangle(
        (left_x, panel_top, left_x + panel_width, panel_top + label_height),
        radius=7,
        fill="#dbeafe",
    )
    draw.rounded_rectangle(
        (right_x, panel_top, right_x + panel_width, panel_top + label_height),
        radius=7,
        fill="#fef3c7",
    )
    draw.text((left_x + 12, panel_top + 8), "GENERATED", fill="#1e3a8a", font=label_font)
    draw.text(
        (right_x + 12, panel_top + 8),
        expected_label,
        fill="#78350f",
        font=label_font,
    )

    image_top = panel_top + label_height
    for panel_x, image in ((left_x, generated), (right_x, expected)):
        x = panel_x + (panel_width - image.width) // 2
        y = image_top + (panel_height - image.height) // 2
        canvas.paste(image, (x, y))
        draw.rectangle(
            (panel_x, image_top, panel_x + panel_width, image_top + panel_height),
            outline="#cbd5e1",
            width=2,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    return {
        "candidate": candidate_meta,
        "reference": reference_meta,
        "composition": {"width": canvas_width, "height": canvas_height, "format": "PNG"},
    }


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.").lower()
    return slug or "visual-check"


def _existing_reviews(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return {
            (row.get("task_id", ""), row.get("run_id", "")): row
            for row in rows
            if row.get("task_id")
        }


def _json_block(value: Any) -> str:
    return html.escape(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _file_size(value: Any) -> str:
    if not isinstance(value, int):
        return "size unavailable"
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def _event_depths(events: list[dict[str, Any]]) -> dict[str, int]:
    by_id = {str(event.get("event_id")): event for event in events}
    depths: dict[str, int] = {}

    def depth(event_id: str, seen: set[str] | None = None) -> int:
        if event_id in depths:
            return depths[event_id]
        event = by_id.get(event_id)
        parent = str(event.get("parent_event_id") or "") if event else ""
        visited = set(seen or ())
        if not parent or parent not in by_id or parent in visited:
            result = 0
        else:
            visited.add(event_id)
            result = min(depth(parent, visited) + 1, 4)
        depths[event_id] = result
        return result

    for event_id in by_id:
        depth(event_id)
    return depths


MAX_INLINE_CAPTURE_CHARS = 40_000


def _capture_text(stored: Path | None) -> str | None:
    """Read a preserved copy so the report can show the logic that produced an output."""
    if stored is None or not stored.is_file():
        return None
    try:
        content = stored.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if len(content) > MAX_INLINE_CAPTURE_CHARS:
        return (
            content[:MAX_INLINE_CAPTURE_CHARS]
            + "\n… truncated in this report; open the stored copy for the complete file."
        )
    return content


def _capture_index(artifacts: list[Any]) -> dict[str, list[str]]:
    """Map each event id to the files whose content was preserved while it ran."""
    index: dict[str, list[str]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = Path(str(artifact.get("path") or "")).name or str(artifact.get("artifact_id") or "")
        for capture in artifact.get("content_captures") or []:
            if not isinstance(capture, dict):
                continue
            for observation in capture.get("observations") or []:
                if not isinstance(observation, dict):
                    continue
                event_id = observation.get("event_id")
                if not event_id:
                    continue
                label = f"{name} · {str(capture.get('sha256') or '')[:12]}"
                index.setdefault(str(event_id), [])
                if label not in index[str(event_id)]:
                    index[str(event_id)].append(label)
    return index


def _content_captures_html(artifact: dict[str, Any], run_dir: Path | None) -> str:
    captures = artifact.get("content_captures")
    if not isinstance(captures, list) or not captures:
        return ""
    blocks: list[str] = []
    for index, capture in enumerate(captures, start=1):
        if not isinstance(capture, dict):
            continue
        stored_path = str(capture.get("stored_path") or "")
        stored = (run_dir / stored_path) if run_dir and stored_path else None
        link = html.escape(stored_path)
        if stored is not None and stored.is_file():
            link = (
                f'<a href="{html.escape(stored.resolve().as_uri(), quote=True)}">'
                f"{html.escape(stored_path)}</a>"
            )
        reasons = sorted(
            {
                str(observation.get("reason"))
                for observation in capture.get("observations") or []
                if isinstance(observation, dict) and observation.get("reason")
            }
        )
        meta = " · ".join(
            part
            for part in (
                _file_size(capture.get("size_bytes")),
                f"{capture.get('line_count')} lines"
                if isinstance(capture.get("line_count"), int)
                else "",
                f"observed via {', '.join(reasons)}" if reasons else "",
                html.escape(str(capture.get("first_observed_at") or "")),
            )
            if part
        )
        content = _capture_text(stored) if capture.get("encoding") == "utf-8" else None
        body = (
            f"<pre>{html.escape(content)}</pre>"
            if content is not None
            else "<p class=\"capture-note\">Binary or unavailable content; open the stored copy.</p>"
        )
        blocks.append(
            f"""
            <div class="capture">
              <div class="capture-meta">version {index} · <code>{html.escape(str(capture.get('sha256') or '')[:16])}</code>
                · {meta} · {link}</div>
              {body}
            </div>
            """
        )
    return (
        '<div class="audit-field"><dt>Preserved content</dt>'
        f'<dd>{"".join(blocks)}</dd></div>'
    )


def _audit_html(audit: dict[str, Any] | None, manifest_path: str | None) -> str:
    if not audit:
        return ""
    events = audit.get("events") if isinstance(audit.get("events"), list) else []
    artifacts = audit.get("artifacts") if isinstance(audit.get("artifacts"), list) else []
    notes = audit.get("notes") if isinstance(audit.get("notes"), list) else []
    trace_status = str(audit.get("inner_trace_status") or "unavailable")
    content_store = audit.get("content_store")
    content_store = content_store if isinstance(content_store, dict) else None
    run_dir = Path(manifest_path).resolve().parent if manifest_path else None
    captures_by_event = _capture_index(artifacts)
    depths = _event_depths([event for event in events if isinstance(event, dict)])
    event_cards: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "")
        sequence = event.get("sequence")
        kind = html.escape(str(event.get("kind") or "event"))
        name = html.escape(str(event.get("name") or "Event"))
        status = str(event.get("status") or "recorded")
        status_text = html.escape(status)
        status_class = _slug(status)
        depth = depths.get(event_id, 0)
        rows: list[str] = []
        command = event.get("command")
        if command is not None:
            command_text = (
                json.dumps(command, ensure_ascii=False)
                if isinstance(command, list)
                else str(command)
            )
            rows.append(
                '<div class="audit-field"><dt>Command</dt>'
                f'<dd><pre class="command">{html.escape(command_text)}</pre></dd></div>'
            )
        tool = event.get("tool")
        if isinstance(tool, dict):
            server = f"{tool.get('server')} / " if tool.get("server") else ""
            rows.append(
                '<div class="audit-field"><dt>Tool</dt>'
                f"<dd><code>{html.escape(server + str(tool.get('name') or 'tool'))}</code></dd>"
                "</div>"
            )
            parameters = tool.get("parameters")
            if parameters:
                rows.append(
                    '<div class="audit-field"><dt>Tool parameters</dt>'
                    f"<dd><pre>{_json_block(parameters)}</pre></dd></div>"
                )
        parameters = event.get("parameters")
        if parameters:
            rows.append(
                '<div class="audit-field"><dt>Parameters</dt>'
                f"<dd><pre>{_json_block(parameters)}</pre></dd></div>"
            )
        result = event.get("result")
        if result and any(value is not None for value in result.values()):
            rows.append(
                '<div class="audit-field"><dt>Result</dt>'
                f"<dd><pre>{_json_block(result)}</pre></dd></div>"
            )
        details = event.get("details")
        if details:
            rows.append(
                '<div class="audit-field"><dt>Details</dt>'
                f"<dd><pre>{_json_block(details)}</pre></dd></div>"
            )
        source_lines = event.get("source_lines") or []
        timing = " → ".join(
            str(value)
            for value in (event.get("started_at"), event.get("finished_at"))
            if value
        )
        source_text = str(event.get("source") or "unknown")
        if source_lines:
            source_text += f" · source lines {', '.join(str(line) for line in source_lines)}"
        preserved = captures_by_event.get(event_id) or []
        if preserved:
            rows.append(
                '<div class="audit-field"><dt>Preserved files</dt><dd><ul>'
                + "".join(f"<li><code>{html.escape(item)}</code></li>" for item in preserved)
                + "</ul></dd></div>"
            )
        rows.append(
            '<div class="audit-field"><dt>Evidence</dt>'
            f"<dd>{html.escape(source_text)}"
            f"{' · ' + html.escape(timing) if timing else ''}</dd></div>"
        )
        event_cards.append(
            f"""
            <details class="audit-event" style="--depth:{depth}">
              <summary>
                <span class="sequence">{int(sequence):02d}</span>
                <span class="event-kind">{kind}</span>
                <span class="event-name">{name}</span>
                <span class="event-status {status_class}">{status_text}</span>
              </summary>
              <dl>{''.join(rows)}</dl>
            </details>
            """
        )

    log_links: list[str] = []
    artifact_cards: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("artifact_id") or "artifact")
        path = Path(str(artifact.get("path") or ""))
        role = str(artifact.get("role") or "artifact")
        exists = bool(artifact.get("exists_at_finish"))
        path_text = html.escape(str(path))
        file_link = (
            f'<a href="{html.escape(path.resolve().as_uri(), quote=True)}">{path_text}</a>'
            if exists
            else path_text
        )
        if role == "log" and exists:
            log_links.append(
                f'<a href="{html.escape(path.resolve().as_uri(), quote=True)}">'
                f"{html.escape(path.name)}</a>"
            )
        lineage = artifact.get("lineage")
        lineage_items = []
        if isinstance(lineage, list):
            for link in lineage:
                if not isinstance(link, dict):
                    continue
                lineage_items.append(
                    f"<li>{html.escape(str(link.get('relationship') or 'related_to'))} "
                    f"<code>{html.escape(str(link.get('target_id') or 'unknown'))}</code> "
                    f"<span class=\"evidence\">({html.escape(str(link.get('evidence') or 'unspecified'))})</span></li>"
                )
        checksum = str(artifact.get("sha256") or "not available")
        metadata = artifact.get("metadata")
        metadata_html = (
            f'<div class="audit-field"><dt>Metadata</dt><dd><pre>{_json_block(metadata)}</pre></dd></div>'
            if metadata
            else ""
        )
        captures = artifact.get("content_captures") or []
        captures_html = _content_captures_html(artifact, run_dir)
        badge = ""
        if captures and not exists:
            badge = '<span class="capture-badge">content preserved</span>'
        elif captures:
            badge = '<span class="capture-badge kept">content preserved</span>'
        finish_note = "present" if exists else "missing"
        if not exists and captures:
            finish_note = "deleted during the run · content preserved below"
        artifact_cards.append(
            f"""
            <details class="artifact">
              <summary><span class="artifact-role {html.escape(_slug(role))}">{html.escape(role)}</span>
                <code>{html.escape(artifact_id)}</code> · {html.escape(path.name or str(path))}{badge}</summary>
              <dl>
                <div class="audit-field"><dt>Path</dt><dd>{file_link}</dd></div>
                <div class="audit-field"><dt>At finish</dt><dd>{finish_note} ·
                  {_file_size(artifact.get('size_bytes'))}</dd></div>
                <div class="audit-field"><dt>SHA-256</dt><dd><code>{html.escape(checksum)}</code></dd></div>
                <div class="audit-field"><dt>Lineage</dt><dd><ul>{''.join(lineage_items) or '<li>None declared</li>'}</ul></dd></div>
                {captures_html}
                {metadata_html}
              </dl>
            </details>
            """
        )
    note_items = "".join(f"<li>{html.escape(str(note))}</li>" for note in notes)
    store_html = ""
    store_summary = ""
    if content_store:
        skipped = content_store.get("skipped") or []
        skipped_items = "".join(
            f"<li><code>{html.escape(str(item.get('path') or ''))}</code>: "
            f"{html.escape(str(item.get('reason') or 'unknown'))}"
            f"{' · ' + html.escape(str(item.get('detail'))) if item.get('detail') else ''}</li>"
            for item in skipped
            if isinstance(item, dict)
        )
        store_summary = f" · {content_store.get('file_count', 0)} preserved files"
        store_html = f"""
          <h3>Preserved file content</h3>
          <p class="audit-links">{content_store.get('file_count', 0)} file(s),
            {content_store.get('version_count', 0)} version(s),
            {_file_size(content_store.get('total_bytes'))} stored under
            <code>{html.escape(str(content_store.get('path') or ''))}/</code> inside the run
            directory. Files the agent deleted before exiting are still readable here.</p>
          <ul class="audit-notes">{skipped_items or '<li>Nothing was skipped.</li>'}</ul>
        """
    manifest_link = ""
    if manifest_path:
        resolved_manifest = Path(manifest_path).resolve()
        manifest_link = (
            f'<a href="{html.escape(resolved_manifest.as_uri(), quote=True)}">run manifest</a>'
        )
    source_links = " · ".join(link for link in [manifest_link, *log_links] if link)
    return f"""
      <details class="audit">
        <summary>
          <span>Execution audit</span>
          <span class="audit-summary">{len(events)} events · {len(artifacts)} artifacts{store_summary} ·
            inner trace: <strong>{html.escape(trace_status)}</strong></span>
        </summary>
        <div class="audit-body">
          <p class="audit-links">Evidence: {source_links or 'embedded manifest data'}</p>
          <h3>Layered timeline</h3>
          <div class="timeline">{''.join(event_cards) or '<p>No events recorded.</p>'}</div>
          <h3>Artifact lineage</h3>
          <div class="artifacts">{''.join(artifact_cards) or '<p>No artifacts recorded.</p>'}</div>
          {store_html}
          <h3>Capture notes</h3>
          <ul class="audit-notes">{note_items or '<li>None</li>'}</ul>
        </div>
      </details>
    """


def _write_html(report: dict[str, Any], output: Path) -> None:
    cards: list[str] = []
    for item in report["comparisons"]:
        title = html.escape(item["title"])
        task_id = html.escape(item["task_id"])
        prompt = html.escape(item["prompt"])
        comparison = html.escape(item["comparison_image"])
        candidate_url = html.escape(Path(item["candidate_path"]).resolve().as_uri(), quote=True)
        reference_url = html.escape(Path(item["reference_path"]).resolve().as_uri(), quote=True)
        run_id = html.escape(item.get("run_id") or "direct GABench output")
        review_result = html.escape(item["manual_review_result"])
        review_class = _slug(item["manual_review_result"])
        audit = _audit_html(item.get("audit"), item.get("run_manifest_path"))
        cards.append(
            f"""
            <article class="card">
              <div class="card-head">
                <div><code>{task_id}</code><h2>{title}</h2></div>
                <span class="review {review_class}">manual review: {review_result}</span>
              </div>
              <div class="task-prompt">
                <h3>Task prompt</h3>
                <div class="prompt-text">{prompt}</div>
              </div>
              <a href="{comparison}"><img src="{comparison}" alt="{title} comparison"></a>
              <p class="meta">Source: {run_id} ·
                <a href="{candidate_url}">generated image</a> ·
                <a href="{reference_url}">expected image</a>
              </p>
              {audit}
            </article>
            """
        )
    skipped = "".join(
        f"<li><code>{html.escape(item.get('task_id', 'unknown'))}</code>: "
        f"{html.escape(item['reason'])}</li>"
        for item in report["skipped"]
    )
    notice = html.escape(report.get("notice") or "")
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenMapBench visual review</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #f5f7fa; color: #17202a; }}
    main {{ max-width: 1500px; margin: 0 auto; padding: 32px 24px 64px; }}
    header {{ margin-bottom: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: 2rem; }}
    h2 {{ margin: 7px 0 0; font-size: 1.15rem; }}
    h3 {{ margin: 0 0 7px; font-size: .78rem; letter-spacing: .04em;
          text-transform: uppercase; color: #475569; }}
    p {{ line-height: 1.55; }}
    .summary {{ color: #475569; }}
    .notice {{ background: #fff7ed; border: 1px solid #fed7aa; padding: 12px 16px;
               border-radius: 10px; }}
    .card {{ background: white; border: 1px solid #dbe2ea; border-radius: 14px;
             padding: 18px; margin: 0 0 24px; box-shadow: 0 8px 28px rgb(15 23 42 / 7%); }}
    .card-head {{ display: flex; justify-content: space-between; gap: 20px; align-items: start;
                  margin-bottom: 14px; }}
    .task-prompt {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 9px;
                    padding: 12px 14px; margin: 0 0 16px; }}
    .prompt-text {{ color: #334155; line-height: 1.5; white-space: pre-wrap;
                    overflow-wrap: anywhere; }}
    .card img {{ display: block; width: 100%; height: auto; border: 1px solid #dbe2ea;
                 border-radius: 8px; }}
    .review {{ white-space: nowrap; color: #334155; background: #e2e8f0; padding: 6px 10px;
               border-radius: 999px; font-size: .82rem; font-weight: 650; }}
    .review.pending {{ color: #92400e; background: #fef3c7; }}
    .review.pass {{ color: #166534; background: #dcfce7; }}
    .review.fail {{ color: #991b1b; background: #fee2e2; }}
    .meta {{ color: #64748b; font-size: .9rem; margin-bottom: 0; }}
    .audit {{ margin-top: 16px; border: 1px solid #cbd5e1; border-radius: 10px;
              background: #f8fafc; }}
    .audit > summary {{ display: flex; justify-content: space-between; gap: 16px;
                        cursor: pointer; padding: 13px 14px; font-weight: 700; }}
    .audit-summary {{ color: #64748b; font-size: .82rem; font-weight: 500; }}
    .audit-body {{ border-top: 1px solid #dbe2ea; padding: 14px; }}
    .audit-body h3 {{ margin-top: 18px; }}
    .audit-links {{ margin: 0; color: #64748b; font-size: .88rem; }}
    .audit-event {{ margin: 7px 0 7px calc(var(--depth) * 22px); background: white;
                    border: 1px solid #dbe2ea; border-left: 4px solid #94a3b8;
                    border-radius: 7px; }}
    .audit-event > summary {{ display: flex; align-items: center; gap: 8px; cursor: pointer;
                              padding: 9px 10px; }}
    .sequence {{ min-width: 2.1em; color: #64748b; font: 650 .78rem ui-monospace, monospace; }}
    .event-kind, .artifact-role {{ color: #334155; background: #e2e8f0; border-radius: 999px;
                                  padding: 3px 7px; font-size: .72rem; font-weight: 700; }}
    .event-name {{ flex: 1; font-weight: 650; }}
    .event-status {{ color: #475569; font-size: .78rem; }}
    .event-status.completed, .event-status.passed {{ color: #166534; }}
    .event-status.failed, .event-status.error, .event-status.timed-out {{ color: #991b1b; }}
    .audit dl, .artifact dl {{ margin: 0; padding: 0 12px 10px; }}
    .audit-field {{ display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 10px;
                    padding: 7px 0; border-top: 1px solid #eef2f7; }}
    .audit-field dt {{ color: #64748b; font-size: .78rem; font-weight: 700;
                       text-transform: uppercase; }}
    .audit-field dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
    .audit pre {{ margin: 0; padding: 9px; max-height: 320px; overflow: auto;
                  white-space: pre-wrap; overflow-wrap: anywhere; background: #0f172a;
                  color: #e2e8f0; border-radius: 6px; font: .78rem/1.5 ui-monospace, monospace; }}
    .artifact {{ margin: 7px 0; background: white; border: 1px solid #dbe2ea;
                 border-radius: 7px; }}
    .artifact > summary {{ cursor: pointer; padding: 9px 10px; }}
    .artifact-role.candidate {{ color: #166534; background: #dcfce7; }}
    .artifact-role.intermediate, .artifact-role.working {{ color: #92400e; background: #fef3c7; }}
    .artifact ul {{ margin: 0; padding-left: 18px; }}
    .capture-badge {{ margin-left: 8px; color: #5b21b6; background: #ede9fe; border-radius: 999px;
                      padding: 3px 7px; font-size: .72rem; font-weight: 700; }}
    .capture-badge.kept {{ color: #155e75; background: #cffafe; }}
    .capture {{ margin: 0 0 10px; }}
    .capture-meta {{ color: #64748b; font-size: .78rem; margin-bottom: 5px; }}
    .capture-note {{ margin: 0; color: #64748b; font-size: .82rem; }}
    .capture pre {{ max-height: 460px; }}
    .evidence {{ color: #64748b; font-size: .82rem; }}
    .audit-notes {{ color: #475569; font-size: .86rem; }}
    code {{ color: #475569; }}
    a {{ color: #075985; }}
  </style>
</head>
<body><main>
  <header>
    <h1>OpenMapBench visual review</h1>
    <p class="summary">{len(report["comparisons"])} comparisons · {len(report["skipped"])} skipped.
      Generated images are on the left; expected reference images are on the right.
      Record decisions and notes in <a href="review.csv">review.csv</a>.</p>
    <p class="notice">{notice}</p>
  </header>
  {"".join(cards)}
  <section><h2>Skipped items</h2><ul>{skipped or "<li>None</li>"}</ul></section>
</main></body></html>
"""
    (output / "index.html").write_text(page, encoding="utf-8")


def build_visual_report(
    pairs: list[VisualPair],
    output: Path,
    *,
    source_type: str,
    source_path: Path,
    notice: str,
    expected_label: str = "EXPECTED (REFERENCE)",
    max_panel_width: int = 1200,
    max_panel_height: int = 1000,
    initial_skipped: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    output = output.resolve()
    comparisons_dir = output / "comparisons"
    comparisons_dir.mkdir(parents=True, exist_ok=True)
    prior_reviews = _existing_reviews(output / "review.csv")
    records: list[dict[str, Any]] = []
    skipped = list(initial_skipped or [])
    for index, pair in enumerate(
        sorted(pairs, key=lambda item: (item.task_id, item.run_id or "")), 1
    ):
        suffix = f"-{_slug(pair.run_id[-12:])}" if pair.run_id else ""
        filename = f"{index:03d}-{_slug(pair.task_id)}{suffix}.png"
        destination = comparisons_dir / filename
        try:
            dimensions = compose_side_by_side(
                pair.candidate,
                pair.reference,
                destination,
                title=f"{pair.task_id} - {pair.title}",
                expected_label=expected_label,
                max_panel_width=max_panel_width,
                max_panel_height=max_panel_height,
            )
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            skipped.append({"task_id": pair.task_id, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        previous = prior_reviews.get((pair.task_id, pair.run_id or ""), {})
        records.append(
            {
                "task_id": pair.task_id,
                "title": pair.title,
                "prompt": pair.prompt,
                "run_id": pair.run_id,
                "source_status": pair.source_status,
                "candidate_path": str(pair.candidate.resolve()),
                "candidate_sha256": sha256_file(pair.candidate),
                "reference_path": str(pair.reference.resolve()),
                "reference_sha256": sha256_file(pair.reference),
                "comparison_image": str(Path("comparisons") / filename),
                "manual_review_result": previous.get("manual_result") or "pending",
                "notes": previous.get("notes") or "",
                "image_metadata": dimensions,
                "audit": pair.audit,
                "run_manifest_path": (
                    str(pair.run_manifest_path.resolve()) if pair.run_manifest_path else None
                ),
            }
        )

    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "0.3",
        "review_mode": "manual_side_by_side",
        "created_at": datetime.now(UTC).isoformat(),
        "source_type": source_type,
        "source_path": str(source_path.resolve()),
        "notice": notice,
        "comparison_count": len(records),
        "skipped_count": len(skipped),
        "comparisons": records,
        "skipped": skipped,
    }
    (output / "manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with (output / "review.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "task_id",
            "title",
            "run_id",
            "comparison_image",
            "generated_image",
            "expected_image",
            "manual_result",
            "notes",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in records:
            writer.writerow(
                {
                    "task_id": item["task_id"],
                    "title": item["title"],
                    "run_id": item["run_id"] or "",
                    "comparison_image": item["comparison_image"],
                    "generated_image": item["candidate_path"],
                    "expected_image": item["reference_path"],
                    "manual_result": item["manual_review_result"],
                    "notes": item["notes"],
                }
            )
    _write_html(report, output)
    return report


def _task_prompt(task_path_value: str) -> str:
    task_path = Path(task_path_value)
    if not task_path.is_file():
        return "Prompt unavailable because the original task contract no longer exists."
    try:
        return load_task(task_path).prompt
    except Exception:  # noqa: BLE001 - a malformed old contract must not block visual review
        return "Prompt unavailable because the original task contract could not be read."


def visual_report_from_runs(
    run_root: Path,
    output: Path,
    *,
    max_panel_width: int = 1200,
    max_panel_height: int = 1000,
) -> dict[str, Any]:
    pairs: list[VisualPair] = []
    skipped: list[dict[str, str]] = []
    for manifest_path in sorted(run_root.rglob("manifest.json")):
        try:
            manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            skipped.append(
                {"task_id": manifest_path.parent.name, "reason": f"invalid run manifest: {exc}"}
            )
            continue
        if not manifest.candidate or not manifest.reference:
            skipped.append(
                {"task_id": manifest.task_id, "reason": "candidate or reference missing"}
            )
            continue
        candidate = Path(manifest.candidate.path)
        reference = Path(manifest.reference.path)
        if not is_supported_image_path(candidate) or not is_supported_image_path(reference):
            continue
        if not candidate.is_file() or not reference.is_file():
            skipped.append({"task_id": manifest.task_id, "reason": "image file no longer exists"})
            continue
        pairs.append(
            VisualPair(
                task_id=manifest.task_id,
                title=manifest.task_title,
                prompt=_task_prompt(manifest.task_file.path),
                candidate=candidate,
                reference=reference,
                run_id=manifest.run_id,
                source_status=manifest.status.value,
                audit=(manifest.audit.model_dump(mode="json") if manifest.audit else None),
                run_manifest_path=manifest_path,
            )
        )
    return build_visual_report(
        pairs,
        output,
        source_type="openmapbench_runs",
        source_path=run_root,
        notice=(
            "Manual visual review only. A side-by-side sheet is not an automated correctness "
            "score and does not count as a strict benchmark pass."
        ),
        expected_label="EXPECTED (REFERENCE)",
        max_panel_width=max_panel_width,
        max_panel_height=max_panel_height,
        initial_skipped=skipped,
    )


def _find_candidate(
    candidate_root: Path, task_id: str, output_name: str
) -> tuple[Path | None, str]:
    direct = candidate_root / output_name
    task_relative = candidate_root / task_id / output_name
    matches = [path for path in (direct, task_relative) if path.is_file()]
    if not matches:
        matches = [path for path in candidate_root.rglob(Path(output_name).name) if path.is_file()]
    unique = sorted({path.resolve() for path in matches})
    if len(unique) == 1:
        return unique[0], ""
    if not unique:
        return None, f"generated image not found: {output_name}"
    return None, f"ambiguous generated image ({len(unique)} matches): {output_name}"


def visual_report_from_gabench(
    manifest_path: Path,
    candidate_root: Path,
    output: Path,
    *,
    max_panel_width: int = 1200,
    max_panel_height: int = 1000,
) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("adapter") != "gabench" or not isinstance(payload.get("tasks"), list):
        raise ValueError("not an OpenMapBench GABench import manifest")
    pairs: list[VisualPair] = []
    skipped: list[dict[str, str]] = []
    for entry in payload["tasks"]:
        task_id = str(entry.get("task_id", "unknown"))
        reference = Path(str(entry.get("reference_path", "")))
        if not is_supported_image_path(reference):
            continue
        task_path = Path(str(entry.get("task_path", "")))
        if not task_path.is_file() or not reference.is_file():
            skipped.append({"task_id": task_id, "reason": "task or expected image missing"})
            continue
        task = load_task(task_path)
        candidate, reason = _find_candidate(candidate_root, task_id, task.output.path)
        if candidate is None:
            skipped.append({"task_id": task_id, "reason": reason})
            continue
        if candidate.resolve() == reference.resolve():
            skipped.append(
                {"task_id": task_id, "reason": "generated and expected paths are identical"}
            )
            continue
        pairs.append(
            VisualPair(
                task_id=task.id,
                title=task.title,
                prompt=task.prompt,
                candidate=candidate,
                reference=reference,
                source_status="external_gabench_output",
            )
        )
    return build_visual_report(
        pairs,
        output,
        source_type="gabench_import_manifest",
        source_path=manifest_path,
        notice=(
            "Local manual review derivative. Expected images come from an external GABench "
            "checkout with undeclared repository licensing; do not commit or redistribute this "
            "review folder without confirming permission."
        ),
        expected_label="EXPECTED (GABench)",
        max_panel_width=max_panel_width,
        max_panel_height=max_panel_height,
        initial_skipped=skipped,
    )
