"""Render vector and raster artifacts as review sheets: reference, candidate, and overlay.

A GIS mistake that a number states obscurely — a missing reprojection, a dropped multipart, a
buffer measured in degrees — is obvious in a picture. These renders exist so a reviewer can see
what an agent produced next to what was expected.

They are review aids and nothing else. Rendering happens after evaluation, reads only the
candidate and reference files, and can never change a run's status: a preview alongside a
`failed` card explains the failure, and one alongside a `passed` card confirms it. Everything is
best effort — a layer that cannot be read is reported as skipped, never raised at the caller.

Drawing uses Pillow, which is a core dependency. Reading the artifacts needs the ``geo`` extra
(GeoPandas for vectors, rasterio for rasters); without it every render is skipped with a reason.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

VECTOR_SUFFIXES = {".gpkg", ".geojson", ".json", ".shp", ".fgb", ".gml", ".kml", ".parquet"}
RASTER_SUFFIXES = {".tif", ".tiff", ".vrt", ".img", ".asc", ".nc"}

REFERENCE_COLOR = (217, 119, 6)  # amber, matching the EXPECTED panel of the image sheets
CANDIDATE_COLOR = (37, 99, 235)  # blue, matching the GENERATED panel
BACKGROUND = (255, 255, 255)
PANEL_BACKGROUND = (248, 250, 252)
NODATA_COLOR = (221, 214, 254)
GRID = (203, 213, 225)
INK = (23, 32, 42)
MUTED = (100, 116, 139)

MAX_FEATURES = 25_000
"""Beyond this a plot is a solid blob anyway; the sheet says it was truncated."""


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow 10.4 compatibility
        return ImageFont.load_default()


class PreviewUnavailable(Exception):
    """Raised internally when an artifact cannot be rendered; callers get a reason string."""


def is_previewable(path: Path, kind: str) -> bool:
    """Whether this artifact kind and suffix have a renderer."""
    suffix = path.suffix.lower()
    if kind == "vector":
        return suffix in VECTOR_SUFFIXES
    if kind == "raster":
        return suffix in RASTER_SUFFIXES
    return False


def _require(module: str):
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - exercised only without the geo extra
        raise PreviewUnavailable(
            f"{module} is not installed; install the geo extra to render previews"
        ) from exc


def _read_vector(path: Path, layer: str | None) -> Any:
    gpd = _require("geopandas")
    try:
        frame = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    except Exception as exc:  # any driver error means "cannot preview"
        raise PreviewUnavailable(f"{type(exc).__name__}: {exc}") from exc
    return frame


def _bounds(frames: list[Any]) -> tuple[float, float, float, float] | None:
    boxes = [tuple(frame.total_bounds) for frame in frames if len(frame)]
    if not boxes:
        return None
    minx = min(box[0] for box in boxes)
    miny = min(box[1] for box in boxes)
    maxx = max(box[2] for box in boxes)
    maxy = max(box[3] for box in boxes)
    if not all(map(_finite, (minx, miny, maxx, maxy))):
        return None
    width = maxx - minx
    height = maxy - miny
    pad = max(width, height) * 0.03 or 1.0
    return minx - pad, miny - pad, maxx + pad, maxy + pad


def _finite(value: float) -> bool:
    return math.isfinite(value)


class _Projector:
    """Map layer coordinates onto panel pixels, preserving aspect and flipping the y axis."""

    def __init__(self, bounds: tuple[float, float, float, float], width: int, height: int) -> None:
        minx, miny, maxx, maxy = bounds
        span_x = max(maxx - minx, 1e-9)
        span_y = max(maxy - miny, 1e-9)
        self.scale = min(width / span_x, height / span_y)
        self.offset_x = (width - span_x * self.scale) / 2
        self.offset_y = (height - span_y * self.scale) / 2
        self.minx = minx
        self.maxy = maxy

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.offset_x + (x - self.minx) * self.scale,
            self.offset_y + (self.maxy - y) * self.scale,
        )

    @property
    def pixel_size(self) -> float:
        return 1 / self.scale if self.scale else 0.0


def _draw_geometry(
    draw: ImageDraw.ImageDraw,
    geometry: Any,
    project: _Projector,
    color: tuple[int, int, int],
    alpha: float = 1.0,
) -> None:
    kind = getattr(geometry, "geom_type", "")
    if geometry is None or getattr(geometry, "is_empty", True):
        return
    if kind in {"MultiPolygon", "MultiLineString", "MultiPoint", "GeometryCollection"}:
        for part in geometry.geoms:
            _draw_geometry(draw, part, project, color, alpha)
        return
    fill_alpha = int(70 * alpha)
    line_alpha = int(235 * alpha)
    if kind == "Polygon":
        exterior = [project(x, y) for x, y in geometry.exterior.coords]
        if len(exterior) >= 3:
            draw.polygon(exterior, fill=(*color, fill_alpha), outline=(*color, line_alpha))
        for ring in geometry.interiors:
            hole = [project(x, y) for x, y in ring.coords]
            if len(hole) >= 3:
                draw.polygon(hole, fill=(*PANEL_BACKGROUND, 255), outline=(*color, line_alpha))
        return
    if kind == "LineString" or kind == "LinearRing":
        line = [project(x, y) for x, y in geometry.coords]
        if len(line) >= 2:
            draw.line(line, fill=(*color, line_alpha), width=2, joint="curve")
        return
    if kind == "Point":
        x, y = project(geometry.x, geometry.y)
        draw.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=(*color, line_alpha))


def _render_layer(
    frame: Any,
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
    color: tuple[int, int, int],
    *,
    base: Image.Image | None = None,
    alpha: float = 1.0,
) -> Image.Image:
    width, height = size
    canvas = base.copy() if base is not None else Image.new("RGB", size, PANEL_BACKGROUND)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    project = _Projector(bounds, width, height)
    geometries = frame.geometry.head(MAX_FEATURES)
    tolerance = project.pixel_size * 0.5
    if tolerance > 0:
        try:
            geometries = geometries.simplify(tolerance, preserve_topology=False)
        except Exception:  # noqa: BLE001 - simplification is an optimisation, not a requirement
            geometries = frame.geometry.head(MAX_FEATURES)
    for geometry in geometries:
        _draw_geometry(draw, geometry, project, color, alpha)
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _vector_panels(
    candidate: Path, reference: Path, layer: str | None, size: tuple[int, int]
) -> tuple[list[tuple[str, str, Image.Image]], dict[str, Any]]:
    candidate_frame = _read_vector(candidate, layer)
    reference_frame = _read_vector(reference, layer)
    common_crs = reference_frame.crs or candidate_frame.crs
    drawn_candidate = candidate_frame
    if common_crs is not None and candidate_frame.crs is not None:
        try:
            drawn_candidate = candidate_frame.to_crs(common_crs)
        except Exception:  # noqa: BLE001 - an unprojectable candidate is drawn as it is
            drawn_candidate = candidate_frame
    bounds = _bounds([drawn_candidate, reference_frame])
    if bounds is None:
        raise PreviewUnavailable("both layers are empty or have no finite extent")

    reference_panel = _render_layer(reference_frame, bounds, size, REFERENCE_COLOR)
    candidate_panel = _render_layer(drawn_candidate, bounds, size, CANDIDATE_COLOR)
    overlay_panel = _render_layer(
        drawn_candidate,
        bounds,
        size,
        CANDIDATE_COLOR,
        base=reference_panel,
        alpha=0.62,
    )

    def caption(frame: Any, drawn: Any) -> str:
        crs = frame.crs.to_string() if frame.crs is not None else "no CRS declared"
        note = ""
        if len(frame) > MAX_FEATURES:
            note = f" · first {MAX_FEATURES:,} drawn"
        reprojected = (
            " · reprojected for drawing"
            if frame.crs is not None and drawn.crs is not None and drawn.crs != frame.crs
            else ""
        )
        return f"{len(frame):,} features · {crs}{reprojected}{note}"

    panels = [
        ("EXPECTED (REFERENCE)", caption(reference_frame, reference_frame), reference_panel),
        ("GENERATED", caption(candidate_frame, drawn_candidate), candidate_panel),
        ("OVERLAY", "reference amber · generated blue", overlay_panel),
    ]
    metadata = {
        "artifact_kind": "vector",
        "candidate": {"features": len(candidate_frame), "crs": _crs_text(candidate_frame)},
        "reference": {"features": len(reference_frame), "crs": _crs_text(reference_frame)},
        "extent": list(bounds),
    }
    return panels, metadata


def _crs_text(frame: Any) -> str | None:
    return frame.crs.to_string() if frame.crs is not None else None


def _stretch(array: Any, low: float, high: float) -> Any:
    numpy = _require("numpy")
    span = high - low
    if span <= 0:
        span = 1.0
    scaled = (array.astype("float64") - low) / span
    return numpy.clip(scaled, 0.0, 1.0)


def _raster_panel(
    array: Any, mask: Any, size: tuple[int, int], low: float, high: float
) -> Image.Image:
    numpy = _require("numpy")
    grey = (_stretch(array, low, high) * 255).astype("uint8")
    rgb = numpy.dstack([grey, grey, grey])
    rgb[mask] = NODATA_COLOR
    image = Image.fromarray(rgb, mode="RGB")
    image.thumbnail(size, Image.Resampling.NEAREST)
    panel = Image.new("RGB", size, PANEL_BACKGROUND)
    panel.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return panel


def _text_panel(size: tuple[int, int], lines: list[str]) -> Image.Image:
    panel = Image.new("RGB", size, PANEL_BACKGROUND)
    draw = ImageDraw.Draw(panel)
    font = _font(15)
    y = size[1] // 2 - len(lines) * 11
    for line in lines:
        draw.text((14, y), line[:70], fill=MUTED, font=font)
        y += 22
    return panel


def _raster_panels(
    candidate: Path, reference: Path, size: tuple[int, int]
) -> tuple[list[tuple[str, str, Image.Image]], dict[str, Any]]:
    rasterio = _require("rasterio")
    numpy = _require("numpy")

    def read(path: Path) -> dict[str, Any]:
        try:
            with rasterio.open(path) as source:
                scale = max(source.width / size[0], source.height / size[1], 1.0)
                shape = (max(int(source.height / scale), 1), max(int(source.width / scale), 1))
                band = source.read(1, out_shape=shape, masked=True)
                return {
                    "array": numpy.ma.filled(band, 0.0),
                    "mask": numpy.ma.getmaskarray(band),
                    "crs": source.crs.to_string() if source.crs else None,
                    "width": source.width,
                    "height": source.height,
                    "transform": tuple(source.transform),
                    "bands": source.count,
                }
        except Exception as exc:  # any driver error means "cannot preview"
            raise PreviewUnavailable(f"{type(exc).__name__}: {exc}") from exc

    candidate_raster = read(candidate)
    reference_raster = read(reference)
    valid = numpy.concatenate(
        [
            candidate_raster["array"][~candidate_raster["mask"]].ravel(),
            reference_raster["array"][~reference_raster["mask"]].ravel(),
        ]
    )
    if valid.size == 0:
        raise PreviewUnavailable("both rasters are entirely NoData")
    low, high = (float(value) for value in numpy.percentile(valid, [2, 98]))

    comparable = (
        candidate_raster["crs"] == reference_raster["crs"]
        and candidate_raster["transform"] == reference_raster["transform"]
        and candidate_raster["array"].shape == reference_raster["array"].shape
    )
    if comparable:
        difference = numpy.abs(candidate_raster["array"] - reference_raster["array"])
        combined_mask = candidate_raster["mask"] | reference_raster["mask"]
        peak = float(difference[~combined_mask].max()) if (~combined_mask).any() else 0.0
        third = (
            _raster_panel(difference, combined_mask, size, 0.0, peak or 1.0),
            f"absolute difference · peak {peak:g}",
        )
    else:
        third = (
            _text_panel(
                size,
                [
                    "Grids differ, so no pixel difference was computed.",
                    (
                        f"generated: {candidate_raster['width']}"
                        f"x{candidate_raster['height']} "
                        f"{candidate_raster['crs'] or 'no CRS'}"
                    ),
                    (
                        f"reference: {reference_raster['width']}"
                        f"x{reference_raster['height']} "
                        f"{reference_raster['crs'] or 'no CRS'}"
                    ),
                ],
            ),
            "grids differ",
        )

    def caption(raster: dict[str, Any]) -> str:
        return (
            f"{raster['width']}x{raster['height']} · {raster['bands']} band(s) · "
            f"{raster['crs'] or 'no CRS declared'}"
        )

    panels = [
        (
            "EXPECTED (REFERENCE)",
            caption(reference_raster),
            _raster_panel(
                reference_raster["array"], reference_raster["mask"], size, low, high
            ),
        ),
        (
            "GENERATED",
            caption(candidate_raster),
            _raster_panel(
                candidate_raster["array"], candidate_raster["mask"], size, low, high
            ),
        ),
        ("DIFFERENCE", third[1], third[0]),
    ]
    metadata = {
        "artifact_kind": "raster",
        "candidate": {
            "size": [candidate_raster["width"], candidate_raster["height"]],
            "crs": candidate_raster["crs"],
        },
        "reference": {
            "size": [reference_raster["width"], reference_raster["height"]],
            "crs": reference_raster["crs"],
        },
        "band_stretch": [low, high],
        "pixel_difference_computed": comparable,
    }
    return panels, metadata


def _compose(
    panels: list[tuple[str, str, Image.Image]], output: Path, title: str, note: str
) -> dict[str, Any]:
    margin = 22
    gap = 14
    header = 54
    label_height = 34
    caption_height = 24
    footer = 26
    panel_width = max(image.width for _, _, image in panels)
    panel_height = max(image.height for _, _, image in panels)
    width = margin * 2 + panel_width * len(panels) + gap * (len(panels) - 1)
    height = margin * 2 + header + label_height + panel_height + caption_height + footer
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    title_font = _font(19)
    label_font = _font(16)
    caption_font = _font(14)

    safe_title = title if len(title) <= 170 else f"{title[:167]}..."
    draw.text((margin, margin), safe_title, fill=INK, font=title_font)
    label_top = margin + header
    panel_top = label_top + label_height
    fills = {
        "EXPECTED (REFERENCE)": ((254, 243, 199), (120, 53, 15)),
        "GENERATED": ((219, 234, 254), (30, 58, 138)),
    }
    for index, (label, caption, image) in enumerate(panels):
        x = margin + index * (panel_width + gap)
        fill, ink = fills.get(label, ((226, 232, 240), (51, 65, 85)))
        draw.rounded_rectangle(
            (x, label_top, x + panel_width, label_top + label_height), radius=7, fill=fill
        )
        draw.text((x + 10, label_top + 8), label, fill=ink, font=label_font)
        canvas.paste(image, (x + (panel_width - image.width) // 2, panel_top))
        draw.rectangle(
            (x, panel_top, x + panel_width, panel_top + panel_height), outline=GRID, width=2
        )
        draw.text(
            (x + 2, panel_top + panel_height + 6), caption[:88], fill=MUTED, font=caption_font
        )
    draw.text((margin, height - margin - 12), note, fill=MUTED, font=caption_font)

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    return {"width": width, "height": height, "format": "PNG"}


def render_comparison(
    candidate: Path,
    reference: Path,
    output: Path,
    *,
    kind: str,
    title: str,
    layer: str | None = None,
    max_panel_width: int = 520,
    max_panel_height: int = 460,
) -> dict[str, Any]:
    """Write a three-panel review sheet for one vector or raster artifact pair.

    Raises ``PreviewUnavailable`` with a human reason when the pair cannot be drawn; callers
    record that as a skipped comparison rather than failing the report.
    """
    if max_panel_width < 120 or max_panel_height < 120:
        raise ValueError("panel dimensions must be at least 120 pixels")
    size = (max_panel_width, max_panel_height)
    if kind == "vector":
        panels, metadata = _vector_panels(candidate, reference, layer, size)
    elif kind == "raster":
        panels, metadata = _raster_panels(candidate, reference, size)
    else:
        raise PreviewUnavailable(f"no renderer for output kind {kind}")
    composition = _compose(
        panels,
        output,
        title,
        (
            "Rendered for review only. The verdict comes from the strict checks, "
            "never from this image."
        ),
    )
    return metadata | {"composition": composition}
