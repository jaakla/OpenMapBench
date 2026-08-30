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
    candidate: Path
    reference: Path
    run_id: str | None = None
    source_status: str | None = None


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


def _write_html(report: dict[str, Any], output: Path) -> None:
    cards: list[str] = []
    for item in report["comparisons"]:
        title = html.escape(item["title"])
        task_id = html.escape(item["task_id"])
        comparison = html.escape(item["comparison_image"])
        candidate_url = html.escape(Path(item["candidate_path"]).resolve().as_uri(), quote=True)
        reference_url = html.escape(Path(item["reference_path"]).resolve().as_uri(), quote=True)
        run_id = html.escape(item.get("run_id") or "direct GABench output")
        review_result = html.escape(item["manual_review_result"])
        review_class = _slug(item["manual_review_result"])
        cards.append(
            f"""
            <article class="card">
              <div class="card-head">
                <div><code>{task_id}</code><h2>{title}</h2></div>
                <span class="review {review_class}">manual review: {review_result}</span>
              </div>
              <a href="{comparison}"><img src="{comparison}" alt="{title} comparison"></a>
              <p class="meta">Source: {run_id} ·
                <a href="{candidate_url}">generated image</a> ·
                <a href="{reference_url}">expected image</a>
              </p>
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
    p {{ line-height: 1.55; }}
    .summary {{ color: #475569; }}
    .notice {{ background: #fff7ed; border: 1px solid #fed7aa; padding: 12px 16px;
               border-radius: 10px; }}
    .card {{ background: white; border: 1px solid #dbe2ea; border-radius: 14px;
             padding: 18px; margin: 0 0 24px; box-shadow: 0 8px 28px rgb(15 23 42 / 7%); }}
    .card-head {{ display: flex; justify-content: space-between; gap: 20px; align-items: start;
                  margin-bottom: 14px; }}
    .card img {{ display: block; width: 100%; height: auto; border: 1px solid #dbe2ea;
                 border-radius: 8px; }}
    .review {{ white-space: nowrap; color: #334155; background: #e2e8f0; padding: 6px 10px;
               border-radius: 999px; font-size: .82rem; font-weight: 650; }}
    .review.pending {{ color: #92400e; background: #fef3c7; }}
    .review.pass {{ color: #166534; background: #dcfce7; }}
    .review.fail {{ color: #991b1b; background: #fee2e2; }}
    .meta {{ color: #64748b; font-size: .9rem; margin-bottom: 0; }}
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
            }
        )

    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "0.1",
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
                candidate=candidate,
                reference=reference,
                run_id=manifest.run_id,
                source_status=manifest.status.value,
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
