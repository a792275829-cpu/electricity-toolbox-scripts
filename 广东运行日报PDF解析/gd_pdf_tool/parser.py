from __future__ import annotations

import io
import re
from math import ceil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image


class ParseError(RuntimeError):
    """PDF 无法解析为报价容量区间时抛出。"""


@dataclass(frozen=True)
class QuoteRow:
    interval: str
    coal: float
    gas: float
    renewable: float


@dataclass(frozen=True)
class DeclarationRow:
    total: float
    coal: float
    gas: float
    renewable: float


@dataclass(frozen=True)
class DeclarationInfo:
    zero_or_below: DeclarationRow | None
    above_600: DeclarationRow | None
    source: str = "text"


@dataclass(frozen=True)
class ChartAnalysis:
    rows: list[QuoteRow]
    pixel_counts: list[tuple[float, float, float]]
    scale_mw_per_pixel: float
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PdfAnalysis:
    pdf_path: Path
    report_date: str
    baseline_capacity: float | None
    declaration_info: DeclarationInfo
    chart_image: Image.Image
    chart_preview_png: bytes
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PdfParseResult:
    pdf_path: Path
    report_date: str
    baseline_capacity: float
    rows: list[QuoteRow]
    chart_preview_png: bytes
    diagnostics: dict[str, Any]


def price_intervals(category_count: int) -> list[str]:
    """返回图中从零价及以下开始、每 25 元一档的报价区间。"""
    if category_count < 1:
        raise ValueError("category_count must be at least 1")
    intervals = ["(-∞,0]"]
    intervals.extend(
        f"({upper - 25}, {upper}]"
        for upper in range(25, category_count * 25, 25)
    )
    return intervals


def analyze_pdf(pdf_path: str | Path) -> PdfAnalysis:
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise ParseError(f"PDF 不存在：{path}")
    declaration_info = extract_declaration_info(path)
    baseline = (
        declaration_info.zero_or_below.total
        if declaration_info.zero_or_below
        else extract_baseline_capacity(path)
    )
    chart, image_source = extract_quote_chart_image(path)
    return PdfAnalysis(
        pdf_path=path,
        report_date=extract_report_date(path),
        baseline_capacity=baseline,
        declaration_info=declaration_info,
        chart_image=chart,
        chart_preview_png=image_to_png_bytes(chart, max_width=1100),
        diagnostics={
            "chart_image_source": image_source,
            "declaration_info_source": declaration_info.source,
        },
    )


def parse_pdf(
    pdf_path: str | Path,
    baseline_capacity: float | None = None,
) -> PdfParseResult:
    analysis = analyze_pdf(pdf_path)
    baseline = baseline_capacity if baseline_capacity is not None else analysis.baseline_capacity
    if baseline is None or baseline <= 0:
        raise ParseError("未能自动识别申报容量基准值，请手动输入后再生成 Excel。")
    chart = analyze_chart_image(
        analysis.chart_image,
        float(baseline),
        declaration_info=analysis.declaration_info,
    )
    return PdfParseResult(
        pdf_path=analysis.pdf_path,
        report_date=analysis.report_date,
        baseline_capacity=float(baseline),
        rows=chart.rows,
        chart_preview_png=analysis.chart_preview_png,
        diagnostics={**analysis.diagnostics, **chart.diagnostics},
    )


def extract_report_date(pdf_path: Path) -> str:
    year_match = re.search(r"(20\d{2})", pdf_path.name)
    md_match = re.search(r"[\(（](\d{2})\.(\d{2})[\)）]", pdf_path.name)
    if year_match and md_match:
        return f"{year_match.group(1)}{md_match.group(1)}{md_match.group(2)}"

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages[:3])
    except Exception:
        text = ""
    match = re.search(
        r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})",
        text,
    )
    if match:
        return f"{int(match.group(1)):04d}{int(match.group(2)):02d}{int(match.group(3)):02d}"
    return "unknown"


def extract_declaration_info(pdf_path: Path) -> DeclarationInfo:
    zero_or_below: DeclarationRow | None = None
    above_600: DeclarationRow | None = None
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[: min(3, len(pdf.pages))]:
                parsed = _read_declaration_tables(page.extract_tables() or [])
                zero_or_below = parsed.zero_or_below or zero_or_below
                above_600 = parsed.above_600 or above_600
                if zero_or_below and above_600:
                    return DeclarationInfo(zero_or_below, above_600)
            # Some reports embed all Chinese headings as images while keeping
            # the numbers as text. Recover labels by cell geometry, then reuse
            # the same header mapping and native numeric extraction.
            from .image_labels import extract_image_label_tables

            for page in pdf.pages[: min(3, len(pdf.pages))]:
                try:
                    tables = extract_image_label_tables(page)
                except RuntimeError as exc:
                    raise ParseError(f"申报信息图片标签识别失败：{exc}") from exc
                parsed = _read_declaration_tables(tables, require_header=True)
                zero_or_below = zero_or_below or parsed.zero_or_below
                above_600 = above_600 or parsed.above_600
                if zero_or_below and above_600:
                    return DeclarationInfo(zero_or_below, above_600, source="image-labels")
    except ParseError:
        raise
    except Exception:
        pass
    return DeclarationInfo(zero_or_below, above_600)


def _read_declaration_tables(
    tables: list[list[list]], *, require_header: bool = False,
) -> DeclarationInfo:
    zero_or_below = above_600 = None
    for table in tables:
        header_indices: dict[str, int] | None = None
        for row in table:
            normalized = [_normalize_cell(cell) for cell in row]
            joined = "".join(normalized)
            if all(key in joined for key in ("申报容量", "煤机容量", "气机容量", "新能源容量")):
                header_indices = _declaration_header_indices(normalized)
                continue
            if require_header and not header_indices:
                continue
            if "零价" in joined:
                zero_or_below = _parse_declaration_table_row(row, header_indices) or zero_or_below
            elif all(key in joined for key in ("报价", "600", "以上")):
                above_600 = _parse_declaration_table_row(row, header_indices) or above_600
    return DeclarationInfo(zero_or_below, above_600)


def extract_baseline_capacity(pdf_path: Path) -> float | None:
    declaration = extract_declaration_info(pdf_path)
    if declaration.zero_or_below:
        return declaration.zero_or_below.total
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            texts = [
                page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                for page in pdf.pages[: min(3, len(pdf.pages))]
            ]
    except Exception:
        return None
    joined = "\n".join(texts)
    candidates: list[str] = []
    for marker in ("零价及以", "零价及以下"):
        start = joined.find(marker)
        if start < 0:
            continue
        end = joined.find("报价", start + len(marker))
        snippet = joined[start : end if end > start else start + 400]
        candidates.extend(re.findall(r"[-+]?\d+(?:\.\d+)?", snippet))
    for value in candidates:
        try:
            number = float(value)
        except ValueError:
            continue
        if number > 1000:
            return number
    return None


def _normalize_cell(cell: object) -> str:
    return "" if cell is None else re.sub(r"\s+", "", str(cell))


def _declaration_header_indices(cells: list[str]) -> dict[str, int]:
    indices: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        for label, key in (
            ("申报容量", "total"),
            ("煤机容量", "coal"),
            ("气机容量", "gas"),
            ("新能源容量", "renewable"),
        ):
            if label in cell and key not in indices:
                indices[key] = idx
                break
    return indices


def _parse_declaration_table_row(
    row: list[object],
    header_indices: dict[str, int] | None,
) -> DeclarationRow | None:
    indices = header_indices or {"total": 1, "coal": 4, "gas": 9, "renewable": 13}
    values: dict[str, float] = {}
    for key in ("total", "coal", "gas", "renewable"):
        idx = indices.get(key)
        if idx is None or idx >= len(row):
            return None
        value = _first_number(row[idx])
        if value is None:
            return None
        values[key] = value
    return DeclarationRow(**values)


def _first_number(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace("\n", ""))
    return float(match.group(0)) if match else None


def extract_quote_chart_image(pdf_path: Path) -> tuple[Image.Image, str]:
    """提取第 3 页报价分布图，兼容内嵌整图和页面拼接图。"""
    candidates: list[tuple[Image.Image, str]] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            if len(pdf.pages) >= 3:
                page = pdf.pages[2]
                images = sorted(
                    page.images,
                    key=lambda item: (item.get("top", 9999), -item.get("width", 0)),
                )
                for image in images:
                    if image.get("width", 0) < page.width * 0.45:
                        continue
                    if image.get("top", 9999) > page.height * 0.4:
                        continue
                    stream = image.get("stream")
                    if not stream:
                        continue
                    try:
                        chart = Image.open(io.BytesIO(stream.get_data())).convert("RGB")
                    except Exception:
                        continue
                    candidates.append((chart, "embedded-image"))
    except Exception:
        pass

    try:
        candidates.append((render_quote_chart_crop(pdf_path), "rendered-page-crop"))
    except Exception:
        pass
    if not candidates:
        raise ParseError("未能提取报价分布图。")

    scored = sorted(
        ((_score_quote_chart_candidate(image), image, source) for image, source in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    embedded = [item for item in scored if item[2] == "embedded-image" and item[0] >= 50]
    if embedded:
        _, image, source = embedded[0]
        return image, source
    best_score, best_image, best_source = scored[0]
    if best_score < 5:
        raise ParseError("未能从 PDF 中识别报价分布柱状图。")
    return best_image, best_source


def render_quote_chart_crop(pdf_path: Path) -> Image.Image:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        if len(pdf) < 3:
            raise ParseError("PDF 页数不足，未找到第 3 页报价分布图。")
        scale = 8
        page = pdf[2]
        try:
            width = ceil(page.get_width() * scale)
            height = ceil(page.get_height() * scale)
            if width < 506 * scale or height < 271 * scale:
                bitmap = page.render(scale=scale)
                try:
                    return bitmap.to_pil().convert("RGB").crop(
                        (95 * scale, 58 * scale, 505 * scale, 270 * scale)
                    )
                finally:
                    bitmap.close()
            # Use the full render's rounded pixel dimensions so the retained
            # pixels match the previous full-page render followed by PIL crop.
            # A one-unit border avoids clipping antialiasing at the crop edge.
            bitmap = page.render(scale=scale, crop=(
                94, (height - 271 * scale) / scale,
                (width - 506 * scale) / scale, 57,
            ))
            try:
                return bitmap.to_pil().convert("RGB").crop(
                    (scale, scale, 411 * scale, 213 * scale)
                )
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()


def image_to_png_bytes(image: Image.Image, max_width: int | None = None) -> bytes:
    converted = image.convert("RGB")
    if max_width and converted.width > max_width:
        height = int(converted.height * max_width / converted.width)
        converted = converted.resize((max_width, height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    converted.save(output, format="PNG")
    return output.getvalue()


def analyze_chart_image(
    image: Image.Image,
    baseline_capacity: float,
    declaration_info: DeclarationInfo | None = None,
) -> ChartAnalysis:
    array = np.array(image.convert("RGB"))
    masks = _make_color_masks(array)
    top_y, baseline_y, y_spacing = _detect_plot_rows(array)
    masks = _select_palette_masks(masks, top_y, baseline_y)
    (
        first_center,
        x_spacing,
        half_width,
        x_clusters,
        category_count,
        geometry_source,
    ) = _detect_category_geometry(array, masks, top_y, baseline_y)
    pixel_counts = _measure_pixel_counts(
        array,
        masks,
        top_y,
        baseline_y,
        first_center,
        x_spacing,
        half_width,
        category_count,
    )
    first_total_pixels = sum(pixel_counts[0])
    if first_total_pixels <= 0:
        raise ParseError("未能识别报价分布图中的零价及以下柱形。")
    scale = baseline_capacity / first_total_pixels
    rows = _rows_from_pixels(pixel_counts, scale, baseline_capacity, declaration_info)
    diagnostics: dict[str, Any] = {
        "height_measurement": "subpixel-color-coverage",
        "color_validation": "bar-hue-evidence",
        "plot_top_y": top_y,
        "plot_baseline_y": baseline_y,
        "y_grid_spacing": y_spacing,
        "first_category_center_x": first_center,
        "category_spacing_x": x_spacing,
        "category_half_width": half_width,
        "category_count": category_count,
        "category_geometry_source": geometry_source,
        "first_total_pixels": first_total_pixels,
        "detected_x_cluster_count": len(x_clusters),
        "scale_mw_per_pixel": scale,
        "low_height_segments": [
            {"interval": interval, "source": source, "height_pixels": round(value, 3)}
            for interval, counts in zip(price_intervals(category_count), pixel_counts, strict=True)
            for source, value in zip(("燃煤", "燃气", "新能源"), counts, strict=True)
            if 0 < value < 3
        ],
    }
    if declaration_info:
        diagnostics["declaration_zero_or_below"] = _declaration_row_to_dict(
            declaration_info.zero_or_below
        )
        diagnostics["declaration_above_600"] = _declaration_row_to_dict(
            declaration_info.above_600
        )
    return ChartAnalysis(rows, pixel_counts, scale, diagnostics)


def _declaration_row_to_dict(row: DeclarationRow | None) -> dict[str, float] | None:
    if row is None:
        return None
    return {
        "total": row.total,
        "coal": row.coal,
        "gas": row.gas,
        "renewable": row.renewable,
    }


def _rows_from_pixels(
    pixel_counts: list[tuple[float, float, float]],
    scale: float,
    baseline_capacity: float,
    declaration_info: DeclarationInfo | None,
) -> list[QuoteRow]:
    rows = [
        QuoteRow(
            interval,
            coal * scale,
            gas * scale,
            renewable * scale,
        )
        for interval, (coal, gas, renewable) in zip(
            price_intervals(len(pixel_counts)), pixel_counts, strict=True
        )
    ]
    adjusted = _apply_declaration_overrides(rows, baseline_capacity, declaration_info)
    return [
        QuoteRow(
            row.interval,
            round(row.coal + 1e-9, 1),
            round(row.gas + 1e-9, 1),
            round(row.renewable + 1e-9, 1),
        )
        for row in adjusted
    ]


def _apply_declaration_overrides(
    rows: list[QuoteRow],
    baseline_capacity: float,
    declaration_info: DeclarationInfo | None,
) -> list[QuoteRow]:
    if not rows:
        return rows
    adjusted = list(rows)
    zero_row = declaration_info.zero_or_below if declaration_info else None
    if zero_row:
        adjusted[0] = QuoteRow(
            adjusted[0].interval,
            round(zero_row.coal, 1),
            round(zero_row.gas, 1),
            round(zero_row.renewable, 1),
        )
    else:
        first = adjusted[0]
        rounded = QuoteRow(
            first.interval, round(first.coal, 1), round(first.gas, 1), round(first.renewable, 1)
        )
        adjusted[0] = _calibrate_row_total(rounded, round(baseline_capacity, 1))
    above_600 = declaration_info.above_600 if declaration_info else None
    if above_600:
        adjusted = _calibrate_above_600_rows(adjusted, above_600)
    return adjusted


def _calibrate_row_total(row: QuoteRow, target_total: float) -> QuoteRow:
    difference = round(target_total - row.coal - row.gas - row.renewable, 1)
    if abs(difference) < 0.1:
        return row
    parts = [row.coal, row.gas, row.renewable]
    largest = max(range(3), key=lambda idx: parts[idx])
    parts[largest] = round(parts[largest] + difference, 1)
    return QuoteRow(row.interval, *parts)


def _calibrate_above_600_rows(
    rows: list[QuoteRow], target: DeclarationRow
) -> list[QuoteRow]:
    adjusted = list(rows)
    start_idx = min(25, len(adjusted))
    if start_idx >= len(adjusted):
        return adjusted
    for attribute, target_value in (
        ("coal", target.coal),
        ("gas", target.gas),
        ("renewable", target.renewable),
    ):
        current = [float(getattr(row, attribute)) for row in adjusted[start_idx:]]
        calibrated = _scale_values_to_total(current, float(target_value))
        for offset, value in enumerate(calibrated):
            row_idx = start_idx + offset
            row = adjusted[row_idx]
            values = {"coal": row.coal, "gas": row.gas, "renewable": row.renewable}
            values[attribute] = value
            adjusted[row_idx] = QuoteRow(row.interval, **values)
    return adjusted


def _scale_values_to_total(values: list[float], target_total: float) -> list[float]:
    rounded_target = round(target_total, 1)
    if rounded_target <= 0:
        return [0.0 for _ in values]
    current_total = sum(values)
    if current_total <= 0:
        raise ParseError(
            "报价 600 以上的某类电源柱形全部未识别，但表格汇总容量大于零；"
            "无法可靠分配到报价区间，请核对原图。"
        )
    # Allocate tenths by largest remainder. Independent rounding followed by
    # subtracting the whole residual from one tiny bar can make it negative.
    target_units = int(round(rounded_target * 10))
    quotas = [value / current_total * target_units for value in values]
    units = [int(value) for value in quotas]
    remaining = target_units - sum(units)
    order = sorted(range(len(values)), key=lambda idx: quotas[idx] - units[idx], reverse=True)
    for idx in order[:remaining]:
        units[idx] += 1
    return [value / 10 for value in units]


def _detect_plot_rows(array: np.ndarray) -> tuple[int, int, float]:
    height, width, _ = array.shape
    red = array[:, :, 0].astype(int)
    green = array[:, :, 1].astype(int)
    blue = array[:, :, 2].astype(int)
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    # JPEG compression tints the zero line near very short coloured bars.
    gray = (maximum - minimum < 18) & (minimum > 145) & (maximum < 245)
    row_counts = gray.sum(axis=1)
    y_index = np.arange(height)
    candidates = np.where(
        (row_counts > width * 0.45)
        & (y_index > 20)
        & (y_index < height - 50)
    )[0]
    rows = [
        int((start + end) / 2)
        for start, end in _cluster_indices(candidates)
        if int((start + end) / 2) > 30
    ]
    if len(rows) < 5:
        raise ParseError("未能定位报价分布图的水平网格线。")
    regular = _select_regular_grid_rows(rows)
    if regular:
        gaps = np.diff(np.array(regular, dtype=float))
        return regular[0], regular[-1], float(np.median(gaps))
    top_y = rows[0]
    gaps = [rows[i + 1] - rows[i] for i in range(min(len(rows) - 1, 10))]
    regular_gaps = [gap for gap in gaps if 30 <= gap <= 160]
    spacing = float(np.median(regular_gaps or gaps[:5]))
    baseline_y = int(round(top_y + 9 * spacing))
    baseline_y = max(top_y + 10, min(height - 1, baseline_y))
    return top_y, baseline_y, spacing


def _select_regular_grid_rows(rows: list[int]) -> list[int] | None:
    if len(rows) < 10:
        return None
    best_by_size: dict[int, tuple[list[int], float, float]] = {}
    for window_size in (11, 10):
        if len(rows) < window_size:
            continue
        best: tuple[list[int], float, float] | None = None
        for start in range(len(rows) - window_size + 1):
            window = rows[start : start + window_size]
            gaps = np.diff(np.array(window, dtype=float))
            if np.any(gaps < 40) or np.any(gaps > 160):
                continue
            median_gap = float(np.median(gaps))
            max_deviation = float(np.max(np.abs(gaps - median_gap)))
            # Axis label strokes must not replace a missing grid line.
            if max_deviation > max(4, median_gap * 0.08):
                continue
            score = float(np.std(gaps)) + max_deviation * 0.25
            if best is None or score < best[1]:
                best = (window, score, max_deviation)
        if best:
            best_by_size[window_size] = best
    eleven = best_by_size.get(11)
    if eleven and eleven[2] <= 4:
        return eleven[0]
    ten = best_by_size.get(10)
    return ten[0] if ten else None


def _make_color_masks(array: np.ndarray) -> dict[str, np.ndarray]:
    rgb = array.astype(float) / 255.0
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = (maximum - minimum) / (maximum + 1e-9)
    red = array[:, :, 0].astype(int)
    green = array[:, :, 1].astype(int)
    blue = array[:, :, 2].astype(int)
    blue_mask = (
        (saturation > 0.1) & (blue > green + 8) & (blue > red + 18) & (blue > 75)
    )
    gas_mask = (
        (saturation > 0.05) & (red > blue + 8) & (red > green + 4) & (red > 90)
    )
    renewable_green = (
        (saturation > 0.1) & (green > blue + 10) & (green > red + 6) & (green > 85)
    )
    renewable_gray = (
        (np.abs(red - green) < 12)
        & (np.abs(green - blue) < 12)
        & (red >= 90)
        & (red <= 190)
    )
    presence = blue_mask | gas_mask | renewable_green
    return {
        "blue": blue_mask,
        "red": gas_mask,
        "green": renewable_green,
        "green_raw": renewable_green,
        "gray_raw": renewable_gray,
        "presence": presence,
    }


def _select_palette_masks(
    masks: dict[str, np.ndarray], top_y: int, baseline_y: int
) -> dict[str, np.ndarray]:
    plot_slice = slice(max(0, top_y - 5), min(masks["presence"].shape[0], baseline_y + 5))
    green_count = int(masks["green_raw"][plot_slice, :].sum())
    gray_count = int(masks["gray_raw"][plot_slice, :].sum())
    use_gray = green_count < 300 and gray_count > 300
    renewable = masks["green_raw"] | masks["gray_raw"] if use_gray else masks["green_raw"]
    return {
        **masks,
        "green": renewable,
        "presence": masks["blue"] | masks["red"] | renewable,
        "use_gray_renewable": np.array(use_gray),
    }


def _detect_category_geometry(
    array: np.ndarray,
    masks: dict[str, np.ndarray],
    top_y: int,
    baseline_y: int,
) -> tuple[float, float, int, list[tuple[int, int, int]], int, str]:
    plot_left, plot_right = _detect_plot_x_bounds(array, top_y)
    color = masks["blue"] | masks["red"] | masks["green"]
    y0 = max(0, top_y - 5)
    y1 = min(array.shape[0], baseline_y + 5)
    col_counts = color[y0:y1, :].sum(axis=0)
    raw_clusters = _cluster_indices(np.where(col_counts > 2)[0])
    clusters: list[tuple[int, int, int]] = []
    for start, end in raw_clusters:
        center = (start + end) / 2
        if end - start + 1 >= 2 and plot_left - 5 <= center <= plot_right + 5:
            clusters.append((start, end, int(col_counts[start : end + 1].max())))
    if not clusters:
        raise ParseError("未能定位报价分布图中的柱形。")
    centers = np.array([(start + end) / 2 for start, end, _ in clusters], dtype=float)
    widths = np.array([end - start + 1 for start, end, _ in clusters], dtype=float)

    label_geometry = _detect_axis_label_geometry(
        array, top_y, baseline_y, plot_left, plot_right
    )
    if label_geometry:
        first_center, x_spacing, category_count = label_geometry
        source = "axis-labels"
    else:
        anchors = _detect_edge_anchor_geometry(
            masks, top_y, baseline_y, plot_left, plot_right
        )
        if anchors:
            first_center, x_spacing = anchors
        else:
            first_center, x_spacing = _detect_cluster_based_geometry(centers)
        category_count = max(1, int(round((plot_right - plot_left) / x_spacing)))
        source = "plot-bounds-fallback"
    half_width = int(round(float(np.median(widths)) / 2 + 5))
    half_width = max(7, min(15, half_width))
    return first_center, x_spacing, half_width, clusters, category_count, source


def _detect_axis_label_geometry(
    array: np.ndarray,
    top_y: int,
    baseline_y: int,
    plot_left: int,
    plot_right: int,
) -> tuple[float, float, int] | None:
    height = array.shape[0]
    plot_height = max(1, baseline_y - top_y)
    y0 = min(height, baseline_y + max(12, int(plot_height * 0.02)))
    y1 = min(height, baseline_y + max(65, int(plot_height * 0.36)))
    if y1 - y0 < 20:
        return None
    red = array[:, :, 0].astype(int)
    green = array[:, :, 1].astype(int)
    blue = array[:, :, 2].astype(int)
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    dark_text = (maximum < 185) & (maximum - minimum < 35)
    column_counts = dark_text[y0:y1, :].sum(axis=0)
    threshold = max(3, int(round((y1 - y0) * 0.02)))
    centers: list[float] = []
    for start, end in _cluster_indices(np.where(column_counts >= threshold)[0]):
        if end - start + 1 < 2:
            continue
        center = (start + end) / 2
        if plot_left - 8 <= center <= plot_right + 8:
            centers.append(float(center))
    return _fit_regular_axis_centers(centers)


def _fit_regular_axis_centers(
    centers: list[float],
) -> tuple[float, float, int] | None:
    if len(centers) < 5:
        return None
    values = np.array(sorted(centers), dtype=float)
    positive_gaps = np.diff(values)
    positive_gaps = positive_gaps[positive_gaps > 3]
    if len(positive_gaps) < 4:
        return None
    spacing = float(np.median(positive_gaps))
    if not 12 <= spacing <= 90:
        return None
    first_center = float(values[0])
    selected_indices: list[int] = []
    selected_centers: list[float] = []
    for _ in range(4):
        best_by_index: dict[int, tuple[float, float]] = {}
        tolerance = spacing * 0.2
        for center in values:
            index = int(round((center - first_center) / spacing))
            if index < 0:
                continue
            residual = abs(center - (first_center + index * spacing))
            if residual > tolerance:
                continue
            current = best_by_index.get(index)
            if current is None or residual < current[0]:
                best_by_index[index] = (residual, float(center))
        if len(best_by_index) < 5:
            return None
        selected_indices = sorted(best_by_index)
        selected_centers = [best_by_index[index][1] for index in selected_indices]
        fitted_spacing, fitted_first = np.polyfit(
            np.array(selected_indices, dtype=float),
            np.array(selected_centers, dtype=float),
            1,
        )
        if not 12 <= fitted_spacing <= 90:
            return None
        first_center = float(fitted_first)
        spacing = float(fitted_spacing)
    category_count = selected_indices[-1] + 1
    if category_count < 5 or len(selected_indices) < category_count * 0.8:
        return None
    return first_center, spacing, category_count


def _detect_edge_anchor_geometry(
    masks: dict[str, np.ndarray],
    top_y: int,
    baseline_y: int,
    plot_left: int,
    plot_right: int,
) -> tuple[float, float] | None:
    plot_width = plot_right - plot_left
    if plot_width <= 0:
        return None
    y0 = max(0, top_y - 5)
    y1 = min(masks["presence"].shape[0], baseline_y + 5)
    plot_height = max(1, baseline_y - top_y)
    left_region = (plot_left - 10, plot_left + int(plot_width * 0.2))
    right_region = (plot_right - int(plot_width * 0.25), plot_right + 10)
    left = _dominant_column_cluster(
        masks["blue"] | masks["red"] | masks["green"],
        y0,
        y1,
        *left_region,
        min_height=max(50, int(plot_height * 0.12)),
    )
    right = _dominant_column_cluster(
        masks["red"],
        y0,
        y1,
        *right_region,
        min_height=max(15, int(plot_height * 0.02)),
    )
    if not left or not right:
        return None
    left_center = left[0]
    right_center = right[0]
    expected_spacing = plot_width / 48
    right_index = int(round((right_center - left_center) / expected_spacing))
    if not 1 <= right_index <= 48:
        return None
    anchor_index = right_index if right_index >= 47 else 48
    spacing = (right_center - left_center) / anchor_index
    if not 15 <= spacing <= 90:
        return None
    if not expected_spacing * 0.65 <= spacing <= expected_spacing * 1.2:
        return None
    return float(left_center), float(spacing)


def _dominant_column_cluster(
    mask: np.ndarray,
    y0: int,
    y1: int,
    x_min: int,
    x_max: int,
    min_height: int,
) -> tuple[float, int, int, int] | None:
    width = mask.shape[1]
    x0 = max(0, x_min)
    x1 = min(width, x_max)
    if x0 >= x1:
        return None
    col_counts = mask[y0:y1, :].sum(axis=0)
    indices = np.arange(width)
    candidates = np.where((col_counts > 2) & (indices >= x0) & (indices <= x1))[0]
    best: tuple[float, int, int, int] | None = None
    best_score = -1
    for start, end in _cluster_indices(candidates):
        if end - start + 1 < 2:
            continue
        height = int(col_counts[start : end + 1].max())
        if height < min_height:
            continue
        total = int(col_counts[start : end + 1].sum())
        score = height * 1000 + total
        if score > best_score:
            best_score = score
            best = (float((start + end) / 2), height, total, end - start + 1)
    return best


def _detect_cluster_based_geometry(centers: np.ndarray) -> tuple[float, float]:
    gaps = np.diff(centers)
    regular = gaps[(gaps >= 15) & (gaps <= 90)]
    spacing = float(np.median(regular)) if len(regular) else 37.0
    first_center = float(centers[0])
    assignments = [
        (int(round((float(center) - first_center) / spacing)), float(center))
        for center in centers
    ]
    usable = [(idx, center) for idx, center in assignments if 0 <= idx <= 36]
    if len({idx for idx, _ in usable}) >= 4:
        grouped = [
            (idx, float(np.median([center for item_idx, center in usable if item_idx == idx])))
            for idx in sorted({idx for idx, _ in usable})
        ]
        slope, intercept = np.polyfit(
            np.array([idx for idx, _ in grouped], dtype=float),
            np.array([center for _, center in grouped], dtype=float),
            1,
        )
        if 15 <= slope <= 60 and abs(intercept - first_center) <= spacing:
            spacing = float(slope)
            first_center = float(intercept)
    return first_center, spacing


def _detect_plot_x_bounds(array: np.ndarray, top_y: int) -> tuple[int, int]:
    red = array[:, :, 0].astype(int)
    green = array[:, :, 1].astype(int)
    blue = array[:, :, 2].astype(int)
    gray = (
        (np.abs(red - green) < 8)
        & (np.abs(green - blue) < 8)
        & (red > 120)
        & (red < 240)
    )
    best = (0, array.shape[1] - 1)
    best_length = 0
    for y in range(max(0, top_y - 3), min(array.shape[0], top_y + 4)):
        for start, end in _cluster_indices(np.where(gray[y])[0]):
            length = end - start + 1
            if length > best_length:
                best = (start, end)
                best_length = length
    return best


def _measure_pixel_counts(
    array: np.ndarray,
    masks: dict[str, np.ndarray],
    top_y: int,
    baseline_y: int,
    first_center: float,
    x_spacing: float,
    half_width: int,
    category_count: int,
) -> list[tuple[float, float, float]]:
    """Measure fractional ink coverage in bar interiors, not binary row counts.

    A half-covered edge contributes half a pixel. Adjacent stack colours are
    unmixed so their shared antialiased row is not counted twice. Background
    samples remove horizontal grid lines, including for grey renewables.
    """
    y0 = max(0, top_y - 5)
    y1 = min(array.shape[0], baseline_y + 1)
    left = max(0, int(first_center - x_spacing / 2))
    right = min(array.shape[1], int(first_center + (category_count - 0.5) * x_spacing))
    background = np.median(array[y0:y1, left:right], axis=1)
    contrast = np.max(np.abs(array[y0:y1].astype(float) - background[:, None]), axis=2)
    presence = masks["presence"][y0:y1] & (contrast > 25)
    interiors: list[tuple[int, int]] = []
    for idx in range(category_count):
        center_x = first_center + idx * x_spacing
        radius = min(half_width, max(1, int(x_spacing * 0.4)))
        x0 = max(0, int(round(center_x - radius)))
        x1 = min(array.shape[1], int(round(center_x + radius + 1)))
        if x0 >= x1:
            interiors.append((x0, x0))
            continue
        strength = presence[:, x0:x1].sum(axis=0)
        runs = _cluster_indices(np.where(strength >= max(1, float(strength.max()) * 0.5))[0])
        runs = [run for run in runs if run[1] - run[0] + 1 >= 3]
        if runs:
            start, end = min(runs, key=lambda run: abs(x0 + sum(run) / 2 - center_x))
            # Stay away from vertical edges, where width antialiasing would
            # otherwise be mistaken for reduced height.
            trim = int((end - start + 1) * 0.2)
            interiors.append((x0 + start + trim, x0 + end + 1 - trim))
        else:
            middle = int(round(center_x))
            interiors.append((max(x0, middle - 2), min(x1, middle + 3)))

    palette: list[np.ndarray | None] = []
    for key in ("blue", "red", "green"):
        samples = [
            array[y0:y1, x0:x1][
                masks[key][y0:y1, x0:x1] & (contrast[:, x0:x1] > 35)
            ]
            for x0, x1 in interiors if x0 < x1
        ]
        pixels = np.concatenate(samples) if samples else np.empty((0, 3))
        if not len(pixels):
            palette.append(None)
            continue
        # Learn the actual report's solid fill, robust to JPEG colour noise.
        bins, counts = np.unique(pixels // 16, axis=0, return_counts=True)
        dominant = bins[int(np.argmax(counts))]
        near = np.max(np.abs(pixels.astype(float) - (dominant * 16 + 7.5)), axis=1) < 24
        palette.append(np.median(pixels[near], axis=0))

    rows: list[tuple[float, float, float]] = []
    for x0, x1 in interiors:
        if x0 >= x1:
            rows.append((0.0, 0.0, 0.0))
            continue
        coverage = _unmix_bar_colors(array[y0:y1, x0:x1], background, palette)
        profile = np.median(coverage, axis=1)
        # Only bridge short grid occlusions surrounded by observed bar ink.
        # Never extrapolate an invisible bar or add a fixed offset to all bars.
        active = np.where(profile.sum(axis=1) > 0.08)[0]
        max_gap = max(3, int((baseline_y - top_y) * 0.008))
        for before, after in zip(active[:-1], active[1:]):
            if 1 < after - before <= max_gap + 1:
                gap = slice(before + 1, after)
                if np.any(background[gap].mean(axis=1) < 245):
                    for channel in range(3):
                        profile[gap, channel] = np.linspace(
                            profile[before, channel], profile[after, channel], after - before + 1
                        )[1:-1]
        # Reject detached JPEG speckles. A real stacked bar reaches the zero
        # line (allowing the zero line itself to obscure a few pixels).
        active = np.where(profile.sum(axis=1) > 0.08)[0]
        runs: list[tuple[int, int]] = []
        for start, end in _cluster_indices(active):
            if runs and start - runs[-1][1] <= max_gap + 1:
                runs[-1] = (runs[-1][0], end)
            else:
                runs.append((start, end))
        grounded = [run for run in runs if len(profile) - 1 - run[1] <= max_gap]
        if not grounded:
            rows.append((0.0, 0.0, 0.0))
            continue
        start, end = max(grounded, key=lambda run: float(profile[run[0]:run[1] + 1].sum()))
        # Retain faint fractional edge rows next to the detected segment.
        start = max(0, start - 1)
        end = min(len(profile), end + 2)
        counts = profile[start:end].sum(axis=0)
        rows.append(tuple(float(value) for value in counts))
    return rows


def _unmix_bar_colors(
    pixels: np.ndarray,
    background: np.ndarray,
    palette: list[np.ndarray | None],
    *,
    filter_sources: bool = True,
) -> np.ndarray:
    """Fit background + one/two fills with nonnegative fractional coverage."""
    delta = pixels.astype(float) - background[:, None, :]
    best_error = np.sum(delta * delta, axis=2)
    result = np.zeros((*pixels.shape[:2], 3), dtype=float)
    directions = [None if color is None else color - background for color in palette]
    for channel, direction in enumerate(directions):
        if direction is None:
            continue
        norm = np.sum(direction * direction, axis=1)[:, None]
        weight = np.clip(np.sum(delta * direction[:, None], axis=2) / np.maximum(norm, 1), 0, 1)
        residual = delta - weight[:, :, None] * direction[:, None]
        error = np.sum(residual * residual, axis=2)
        better = error < best_error
        result[better] = 0
        result[:, :, channel][better] = weight[better]
        best_error[better] = error[better]
    single_error = best_error.copy()
    for first in range(3):
        for second in range(first + 1, 3):
            a, b = directions[first], directions[second]
            if a is None or b is None:
                continue
            aa = np.sum(a * a, axis=1)[:, None]
            bb = np.sum(b * b, axis=1)[:, None]
            ab = np.sum(a * b, axis=1)[:, None]
            ad = np.sum(delta * a[:, None], axis=2)
            bd = np.sum(delta * b[:, None], axis=2)
            determinant = aa * bb - ab * ab
            safe = np.maximum(determinant, 1)
            wa = (ad * bb - bd * ab) / safe
            wb = (bd * aa - ad * ab) / safe
            valid = (determinant > 1) & (wa > 0.025) & (wb > 0.025) & (wa + wb <= 1.03)
            total = np.maximum(1, wa + wb)
            wa, wb = wa / total, wb / total
            residual = delta - wa[:, :, None] * a[:, None] - wb[:, :, None] * b[:, None]
            error = np.sum(residual * residual, axis=2)
            # Require a meaningful improvement, so compression noise does not
            # create tiny amounts of a second electricity source everywhere.
            better = valid & (error + 36 < best_error)
            result[better] = 0
            result[:, :, first][better] = wa[better]
            result[:, :, second][better] = wb[better]
            best_error[better] = error[better]
    # JPEG chroma subsampling can almost remove the saturation of a one-row
    # bar while retaining its luminance. Do not interpret that loss as a mix
    # of coal and gas. For a clearly matching hue and poor single-fill RGB
    # fit, recover coverage from luminance. Exact stack mixtures still use
    # the RGB decomposition above; neutral grid rows never use this fallback.
    chroma = delta - delta.mean(axis=2, keepdims=True)
    chroma_norm = np.linalg.norm(chroma, axis=2)
    hue_threshold = 0.94 if sum(color is not None for color in palette) == 1 else 0.98
    for channel, direction in enumerate(directions):
        if direction is None:
            continue
        color_chroma = direction - direction.mean(axis=1, keepdims=True)
        norm = np.linalg.norm(color_chroma, axis=1)[:, None]
        cosine = np.sum(chroma * color_chroma[:, None], axis=2) / np.maximum(chroma_norm * norm, 1)
        luminance = direction.mean(axis=1)[:, None]
        weight = np.clip(delta.mean(axis=2) / np.minimum(luminance, -1), 0, 1)
        recover = (
            (single_error > 12 ** 2) & (cosine > hue_threshold)
            & (chroma_norm > 1.5) & (norm > 20) & (weight >= 0.04)
            & (background.mean(axis=1)[:, None] >= 245)
        )
        result[recover] = 0
        result[:, :, channel][recover] = weight[recover]
        best_error[recover] = 0
    result[best_error > 24 ** 2] = 0
    result[result < 0.025] = 0
    if filter_sources:
        supported = _supported_bar_sources(pixels, background, palette, result)
        if any(color is not None and not supported[i] for i, color in enumerate(palette)):
            # Refit using only sources supported by this bar. Simply deleting
            # a false blue fraction would also lose real gas coverage.
            restricted = [color if supported[i] else None for i, color in enumerate(palette)]
            return _unmix_bar_colors(pixels, background, restricted, filter_sources=False)
    return result


def _supported_bar_sources(
    pixels: np.ndarray,
    background: np.ndarray,
    palette: list[np.ndarray | None],
    coverage: np.ndarray,
) -> list[bool]:
    """Require observed hue or a substantial mixed boundary for each source.

    RGB least squares alone can explain desaturated red as red + blue.
    A source with no matching hue anywhere in the bar must not appear solely
    as a small correction to another source's JPEG-distorted colour.
    """
    rgb = np.median(pixels.astype(float), axis=1)
    delta = rgb - background
    chroma = delta - delta.mean(axis=1, keepdims=True)
    chroma_norm = np.linalg.norm(chroma, axis=1)
    profile = np.median(coverage, axis=1)
    total = profile.sum(axis=1)
    visible = (total > 0.08) & (background.mean(axis=1) >= 245)
    cosines = np.full((len(rgb), 3), -1.0)
    supported = [False, False, False]
    for channel, color in enumerate(palette):
        if color is None:
            continue
        direction = color - background
        color_chroma = direction - direction.mean(axis=1, keepdims=True)
        norm = np.linalg.norm(color_chroma, axis=1)
        cosine = np.sum(chroma * color_chroma, axis=1) / np.maximum(chroma_norm * norm, 1)
        cosines[:, channel] = cosine
        hue_evidence = (cosine > 0.94) & (chroma_norm > 1.5) & (norm > 20)
        gray_evidence = (norm < 20) & (chroma_norm < 12) & (profile[:, channel] > 0.08)
        supported[channel] = bool(np.any(visible & (hue_evidence | gray_evidence)))
    # Preserve boundaries well outside any single-fill hue cone (~26 degrees)
    # or fractional edges that fit the RGB mixture to within quantization.
    # A small hue drift or fitted fraction alone is not a new source.
    predicted = background.copy()
    for channel, color in enumerate(palette):
        if color is not None:
            predicted += profile[:, channel, None] * (color - background)
    exact_fractional_boundary = (total < 0.95) & (np.max(np.abs(rgb - predicted), axis=1) < 2)
    mixed = visible & ((cosines.max(axis=1) < 0.9) | exact_fractional_boundary)
    fractions = profile / np.maximum(total[:, None], 1e-9)
    for channel in range(3):
        supported[channel] |= bool(np.any(mixed & (fractions[:, channel] >= 0.2)))
    return supported


def _score_quote_chart_candidate(image: Image.Image) -> float:
    array = np.array(image.convert("RGB"))
    height, width, _ = array.shape
    if width < 400 or height < 180:
        return 0.0
    try:
        top_y, baseline_y, _ = _detect_plot_rows(array)
    except ParseError:
        return 0.0
    masks = _select_palette_masks(_make_color_masks(array), top_y, baseline_y)
    plot = masks["presence"][max(0, top_y - 5) : min(height, baseline_y + 5), :]
    col_counts = plot.sum(axis=0)
    clusters = [
        item for item in _cluster_indices(np.where(col_counts > 2)[0])
        if item[1] - item[0] >= 2
    ]
    if not clusters:
        return 0.0
    centers = np.array([(start + end) / 2 for start, end in clusters], dtype=float)
    gaps = np.diff(centers)
    regular = gaps[(gaps >= 15) & (gaps <= 100)]
    first = plot[:, max(0, clusters[0][0] - 3) : min(width, clusters[0][1] + 4)]
    first_height = int((first.sum(axis=1) > 2).sum())
    score = len(regular) * 2 + min(first_height / 30, 20)
    if width / max(height, 1) < 1.6:
        score -= 8
    return float(score)


def _cluster_indices(indices: np.ndarray) -> list[tuple[int, int]]:
    if len(indices) == 0:
        return []
    clusters: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for item in indices[1:]:
        current = int(item)
        if current <= previous + 1:
            previous = current
            continue
        clusters.append((start, previous))
        start = previous = current
    clusters.append((start, previous))
    return clusters
