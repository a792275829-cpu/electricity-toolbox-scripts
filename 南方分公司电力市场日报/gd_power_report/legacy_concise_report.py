"""Legacy 17/18-style concise portrait long-page report renderer."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, timedelta
from math import ceil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import Browser
from gd_power_report.pdf_export import pdf_browser
from pypdf import PdfReader

from gd_power_report.report import WEEKDAYS, _date_cn, _move_realtime_profit_column_last, _table_block, prepare_report_data
from gd_power_report.workday_calendar import format_date_status


PENDING_TEXT = "数据待更新"
CSS_PX_PER_INCH = 96
MM_PER_INCH = 25.4
PAGE_WIDTH_MM = 210
HEADER_TOP_SPACE_MM = 8
MARGIN_TOP_MM = 12 - HEADER_TOP_SPACE_MM
MARGIN_RIGHT_MM = 10
MARGIN_BOTTOM_MM = 12
MARGIN_LEFT_MM = 10


def _short_date(value: str) -> str:
    return re.sub(r"（周.）", "", str(value or "").strip()) or PENDING_TEXT


def _normalized_operation(value: str) -> str:
    return "".join(str(value or "").split()).replace("停备", "备用").rstrip("。")


def _combined_operation_rows(operations: dict[str, Any]) -> list[dict[str, Any]]:
    dates = list(operations.get("dates") or [])
    current_date = _short_date(dates[0] if dates else "")
    previous_date = _short_date(dates[1] if len(dates) > 1 else "")
    result: list[dict[str, Any]] = []
    for row in operations.get("rows") or []:
        statuses = list(row.get("statuses") or [])
        current = str(statuses[0] if statuses else PENDING_TEXT).strip() or PENDING_TEXT
        previous = str(statuses[1] if len(statuses) > 1 else PENDING_TEXT).strip() or PENDING_TEXT
        if current == "与前一日运行方式一致。":
            current = previous
        if previous == "与前一日运行方式一致。":
            previous = current
        same = (
            current != PENDING_TEXT
            and previous != PENDING_TEXT
            and _normalized_operation(current) == _normalized_operation(previous)
        )
        display = (
            f"{previous_date}—{current_date}：{current}"
            if same
            else f"{current_date}：{current}\n{previous_date}：{previous}"
        )
        result.append(
            {"unit": str(row.get("unit") or ""), "display": display, "same": same}
        )
    return result


def _split_strategy_profit(strategy: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(strategy)
    text = str(prepared.get("text") or "").strip()
    if str(prepared.get("plant", "")).startswith("粤东"):
        prepared["plant"] = "粤东区域"
        prepared["paragraphs"] = [re.sub(r"各机组(?:日前)?收益(?:分别)?为[：:]?", "各机组日前偏差收益分别为：", line.strip()) for line in text.splitlines() if line.strip()]
        prepared["body_text"] = text
        prepared["profit_text"] = ""
        return prepared
    match = re.search(r"(各机组(?:日前)?收益.*)$", text)
    if match:
        prepared["body_text"] = text[: match.start()].rstrip()
        prepared["profit_text"] = re.sub(
            r"^各机组(?:日前)?收益(?:分别)?为[：:]?",
            "各机组日前偏差收益分别为：",
            match.group(1).strip(),
        )
    else:
        prepared["body_text"] = text
        prepared["profit_text"] = ""
    return prepared


def _parsed_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _full_day_block(record: dict[str, Any] | None, realtime_date: date) -> dict[str, Any] | None:
    if record is None:
        return None
    title = f"火电日前/实时分时明细（{realtime_date.year}/{realtime_date.month}/{realtime_date.day}）"
    unit = "单位：万千瓦时、元/MWh"
    wrong_date = _parsed_date(record.get("date")) != realtime_date
    table = record.get("table")
    if wrong_date or record.get("pending") or not table:
        return {
            "title": title, "unit": unit, "block_kind": "realtime-full-day",
            "pending": True,
            "pending_message": (
                "全天出清明细日期与实时市场日期不一致，请检查所选实时工作簿。"
                if wrong_date else record.get("pending_message") or "未找到对应日期的全天出清明细。"
            ),
        }
    block = _table_block(
        title, unit, table["rows"], record["header_rows"],
        table["column_widths"], table["range"], "realtime-full-day",
    )
    for row in block["rows"][:block["header_rows"]]:
        for cell in row["cells"]:
            if cell["value"] in {"日前出清", "实时出清", "分公司火电"}:
                cell["wrap_parts"] = [cell["value"]]
    if record.get("source_warnings"):
        block["notes"] = ["部分单元格数据缺失或错误，已用“—”标示。"]
    return block


def _preserve_source_backgrounds(block: dict[str, Any] | None) -> None:
    """Keep Excel fills above the concise/print templates' header and stripe CSS."""
    if block is None:
        return
    for row in block.get("rows", []):
        for cell in row.get("cells", []):
            rules = [rule.strip() for rule in str(cell.get("style") or "").split(";") if rule.strip()]
            backgrounds = [
                rule.split(":", 1)[1].replace("!important", "").strip()
                for rule in rules if rule.split(":", 1)[0].strip() == "background-color"
            ]
            # Excel cells without a solid fill are white, including alternating rows.
            background = backgrounds[-1] if backgrounds else "#FFFFFF"
            rules = [rule for rule in rules if rule.split(":", 1)[0].strip() != "background-color"]
            rules.append(f"background-color:{background}!important")
            cell["style"] = ";".join(rules)


def prepare_legacy_concise_context(report_data: dict[str, Any]) -> dict[str, Any]:
    prepared, analysis_date = prepare_report_data(
        deepcopy(report_data),
        operations_mode="comparison",
        source_faithful_day_recap=True,
    )
    day_blocks = [
        block
        for block in prepared.get("appendices", {}).get("day_ahead", {}).get("blocks", [])
        if block.get("block_kind") in {"day-recap", "day-hourly"}
    ]
    day_blocks.sort(
        key=lambda block: {"day-recap": 0, "day-hourly": 1}.get(
            str(block.get("block_kind") or ""), 99
        )
    )
    realtime_blocks = [
        _move_realtime_profit_column_last(block)
        for block in prepared.get("appendices", {}).get("realtime", {}).get("blocks", [])
        if block.get("block_kind") == "realtime-recap"
    ]
    for block in day_blocks + realtime_blocks:
        block["title"] = str(block.get("title") or "").replace(
            "出清复盘", "出清总览"
        )
    for cards in (
        prepared.get("market", {}).get("day_ahead", []),
        prepared.get("day_ahead", {}).get("cards", []),
    ):
        for card in cards:
            if card.get("delta_text") and not card.get("comparison_note"):
                card["delta_prefix"] = "环比"
    prepared["day_ahead"]["cards"] = [
        card
        for card in prepared.get("day_ahead", {}).get("cards", [])
        if str(card.get("label") or "").strip() != "平均负荷率"
    ]
    second_row_order = (
        "中长期电量", "中长期边际收益", "日前较中长期偏差", "日前边际收益"
    )
    day_cards = prepared["day_ahead"]["cards"]
    positions = [
        index for index, card in enumerate(day_cards)
        if card.get("label") in second_row_order
    ]
    selected = {day_cards[index]["label"]: day_cards[index] for index in positions}
    if len(positions) == len(second_row_order) and set(selected) == set(second_row_order):
        for index, label in zip(positions, second_row_order):
            day_cards[index] = selected[label]
    for card in prepared.get("realtime", {}).get("cards", []):
        if str(card.get("label") or "").strip() == "加权均价":
            card["label"] = "实时加权均价"
    prepared["strategies"] = [
        _split_strategy_profit(strategy)
        for strategy in prepared.get("strategies", [])
    ]
    realtime_analysis_date = (
        _parsed_date(prepared.get("market", {}).get("realtime_date"))
        or _parsed_date(prepared.get("realtime_date"))
        or analysis_date - timedelta(days=2)
    )
    full_day_block = _full_day_block(prepared.get("realtime_full_day"), realtime_analysis_date)
    for block in day_blocks + realtime_blocks + [full_day_block]:
        _preserve_source_backgrounds(block)
    return {
        "report": prepared,
        "analysis_date": analysis_date,
        "analysis_date_cn": _date_cn(analysis_date),
        "analysis_date_short": f"{analysis_date.month}月{analysis_date.day}日",
        "analysis_weekday": f"周{WEEKDAYS[analysis_date.weekday()]}",
        "analysis_date_meta": format_date_status(analysis_date),
        "realtime_date_meta": format_date_status(realtime_analysis_date),
        "operation_rows": _combined_operation_rows(prepared.get("operations", {})),
        "day_blocks": day_blocks,
        "realtime_blocks": realtime_blocks,
        "full_day_block": full_day_block,
    }


def render_legacy_concise_report(
    output_dir: Path, report_data: dict[str, Any]
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    context = prepare_legacy_concise_context(report_data)
    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("legacy_concise_report.html.j2")
    analysis_date = context["analysis_date"]
    path = output_dir / f"{analysis_date.isoformat()}_南方分公司电力市场日报_手机预览版.html"
    rendered = template.render(**context)
    path.write_text(
        "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n",
        encoding="utf-8",
    )
    return path


def _sync_metric_card_heights(page: Any) -> None:
    page.evaluate("""async () => {
        await document.fonts.ready;
        if (window.metricCardsReady) await window.metricCardsReady;
        if (typeof window.syncMetricCardHeights === 'function') {
            window.syncMetricCardHeights();
        }
    }""")


def export_legacy_concise_pdf(html_path: str | Path, *, browser: Browser | None = None) -> Path:
    source = Path(html_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".html":
        raise ValueError(f"精简版HTML不存在或格式不正确：{source}")
    output = source.with_suffix(".pdf")
    content_width_px = round(
        (PAGE_WIDTH_MM - MARGIN_LEFT_MM - MARGIN_RIGHT_MM)
        * CSS_PX_PER_INCH
        / MM_PER_INCH
    )
    with pdf_browser(browser) as renderer:
        page = None
        try:
            page = renderer.new_page(
                viewport={"width": content_width_px, "height": 1200}
            )
            page.emulate_media(media="print")
            page.goto(source.as_uri(), wait_until="networkidle")
            _sync_metric_card_heights(page)
            content_height_px = int(
                page.locator("main").evaluate(
                    "(e, extra)=>Math.ceil(e.getBoundingClientRect().height - extra)",
                    HEADER_TOP_SPACE_MM * CSS_PX_PER_INCH / MM_PER_INCH,
                )
            )
            content_height_mm = content_height_px * MM_PER_INCH / CSS_PX_PER_INCH
            page_height_mm = ceil(
                content_height_mm + MARGIN_TOP_MM + HEADER_TOP_SPACE_MM + MARGIN_BOTTOM_MM + 2
            )
            page.pdf(
                path=str(output),
                width=f"{PAGE_WIDTH_MM}mm",
                height=f"{page_height_mm}mm",
                print_background=True,
                margin={
                    "top": f"{MARGIN_TOP_MM}mm",
                    "right": f"{MARGIN_RIGHT_MM}mm",
                    "bottom": f"{MARGIN_BOTTOM_MM}mm",
                    "left": f"{MARGIN_LEFT_MM}mm",
                },
            )
        finally:
            if page is not None:
                page.close()
    reader = PdfReader(str(output))
    if len(reader.pages) != 1:
        raise ValueError(f"精简版PDF应为1张长页，实际为{len(reader.pages)}页")
    width = float(reader.pages[0].mediabox.width)
    if abs(width - PAGE_WIDTH_MM * 72 / MM_PER_INCH) > 1.0:
        raise ValueError(f"精简版PDF页面宽度异常：{width:.2f}pt")
    return output
