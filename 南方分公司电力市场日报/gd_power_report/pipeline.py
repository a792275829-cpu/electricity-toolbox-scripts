from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Callable

from gd_power_report.collector.market import collect_realtime_market_comparison
from gd_power_report.company_clearing_xlsx import (
    parse_day_ahead_comparison_workbook,
    parse_daily_report_workbook,
)
from gd_power_report.config import AppConfig
from gd_power_report.full_day_clearing_xlsx import read_full_day_clearing_table
from gd_power_report.legacy_concise_report import (
    export_legacy_concise_pdf,
    render_legacy_concise_report,
)
from gd_power_report.legacy_print_report import export_print_pdf, render_print_report
from gd_power_report.market_analysis import summarize_realtime_units
from gd_power_report.manual_market import normalize_manual_market, merge_manual_market
from gd_power_report.realtime_unit_xlsx import parse_realtime_unit_workbook
from gd_power_report.pdf_export import pdf_browser
from gd_power_report.output_commit import publish_pdf_pair
from gd_power_report.workbook_reader import reuse_workbook_inputs


@dataclass(frozen=True)
class RunResult:
    run_dir: Path
    preview_pdf_path: Path
    print_pdf_path: Path


def _render_concise_outputs(
    run_dir: Path,
    report: dict[str, Any],
    progress: Callable[[str], None],
) -> tuple[Path, Path, Path]:
    progress("生成手机预览版 HTML")
    html_path = render_legacy_concise_report(run_dir, report)
    with pdf_browser() as browser:
        progress("导出手机预览版 PDF")
        pdf_path = export_legacy_concise_pdf(html_path, browser=browser)
        progress("生成打印版 HTML")
        print_html_path = render_print_report(run_dir, report)
        progress("导出打印版 PDF")
        print_pdf_path = export_print_pdf(print_html_path, browser=browser)
    return html_path, pdf_path, print_pdf_path


def _merge_market_text_cards(
    website_cards: list[dict[str, Any]],
    workbook_cards: list[dict[str, Any]],
    labels: set[str],
) -> list[dict[str, Any]]:
    """Keep workbook-only metrics and comparisons when website cards replace values."""

    labels = set(labels) | {"必开机组容量"}
    workbook_by_label = {
        str(card.get("label") or ""): card
        for card in workbook_cards
    }
    aliases = {"在线机组数": "市场开机", "市场开机": "在线机组数"}
    merged: list[dict[str, Any]] = []
    for website_card in website_cards:
        card = dict(website_card)
        card.setdefault("source_kind", "website")
        label = str(card.get("label") or "")
        workbook_card = workbook_by_label.get(label) or workbook_by_label.get(
            aliases.get(label, "")
        )
        if (
            workbook_card is not None
            and not card.get("pending")
            and not card.get("comparison_note")
            and card.get("delta") is None
            and workbook_card.get("delta") is not None
        ):
            card["delta"] = workbook_card["delta"]
            card["comparison_source_kind"] = "workbook"
            for key in ("delta_prefix", "delta_unit"):
                if workbook_card.get(key) is not None:
                    card[key] = workbook_card[key]
        merged.append(card)
    existing = {str(card.get("label") or "") for card in merged}
    for card in workbook_cards:
        label = str(card.get("label") or "")
        if label in labels and label not in existing:
            merged.append({**card, "source_kind": "workbook"})
            existing.add(label)
    return merged


def _override_market_card_value(
    cards: list[dict[str, Any]],
    label: str,
    value: float,
    unit: str,
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    found = False
    for source in cards:
        card = dict(source)
        if str(card.get("label") or "") == label:
            card.update({"value": value, "unit": unit, "pending": False})
            if label == "检修容量":
                card["value_basis"] = "manual_override"
                baseline = card.get("comparison_value")
                if isinstance(baseline, (int, float)) and isfinite(baseline):
                    card["delta"] = value - baseline
                    card.pop("comparison_note", None)
                else:
                    card["delta"] = None
                    card["comparison_note"] = "同日日前对比待更新"
                card.pop("pending_reason", None)
            found = True
        updated.append(card)
    if not found:
        updated.append(
            {
                "label": label,
                "value": value,
                "unit": unit,
                "delta": None,
                "pending": False,
            }
        )
    return updated


def _apply_day_ahead_comparisons(
    current_cards: list[dict[str, Any]],
    previous_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_by_label = {
        str(card.get("label") or ""): card
        for card in previous_cards
        if not card.get("pending") and isinstance(card.get("value"), (int, float))
    }
    compared: list[dict[str, Any]] = []
    for current in current_cards:
        card = dict(current)
        previous = previous_by_label.get(str(card.get("label") or ""))
        if card.get("label") in {"含中长期电能电价", "含容量综合电价"}:
            current_value = card.get("value")
            previous_value = previous.get("value") if previous else None
            card["delta"] = (
                float(current_value) - float(previous_value)
                if card.get("price_basis") == "day_fee"
                and previous is not None and previous.get("price_basis") == "day_fee"
                and not card.get("pending")
                and isinstance(current_value, (int, float))
                and isinstance(previous_value, (int, float))
                and isfinite(current_value) and isfinite(previous_value)
                else None
            )
            compared.append(card)
            continue
        if (
            previous is not None
            and not card.get("pending")
            and isinstance(card.get("value"), (int, float))
        ):
            card["delta"] = float(card["value"]) - float(previous["value"])
        compared.append(card)
    return compared


def _adopt_realtime_detail_source(
    report: dict[str, Any],
    detail_report: dict[str, Any],
    unit_rows: list[dict[str, Any]],
) -> tuple[float, float]:
    """Use one selected D-2 workbook for realtime cards, appendix, and units."""

    company_total, unit_total = _check_realtime_detail_totals(detail_report, unit_rows)
    detail_cards = list(detail_report.get("realtime", {}).get("cards") or [])
    day_cards = list(detail_report.get("day_ahead", {}).get("cards") or [])
    day_by_label = {str(card.get("label") or ""): card for card in day_cards}
    comparison_labels = {
        "电能电价": "含中长期电能电价",
        "含容量综合电价": "含容量综合电价",
    }
    for card in detail_cards:
        baseline = day_by_label.get(comparison_labels.get(str(card.get("label") or ""), ""))
        current_value = card.get("value")
        baseline_value = baseline.get("value") if baseline else None
        if isinstance(current_value, (int, float)) and isinstance(baseline_value, (int, float)):
            card["delta"] = float(current_value) - float(baseline_value)
            card["delta_prefix"] = "较日前"

    detail_market = detail_report.get("market", {})
    detail_realtime_price = next(
        (
            card
            for card in detail_market.get("realtime", [])
            if card.get("label") == "全省实时均价"
            and isinstance(card.get("value"), (int, float))
        ),
        None,
    )
    detail_day_price = next(
        (
            card
            for card in detail_market.get("day_ahead", [])
            if card.get("label") == "全省日前均价"
            and isinstance(card.get("value"), (int, float))
        ),
        None,
    )
    if detail_realtime_price is not None:
        realtime_price_value = float(detail_realtime_price["value"])
        updated_market_cards: list[dict[str, Any]] = []
        for source in report.get("market", {}).get("realtime", []):
            market_card = dict(source)
            if market_card.get("label") == "全省实时均价":
                if (market_card.get("value_source_kind", market_card.get("source_kind")) == "website"
                        and isinstance(market_card.get("value"), (int, float))
                        and not isinstance(market_card["value"], bool)
                        and isfinite(market_card["value"]) and not market_card.get("pending")):
                    updated_market_cards.append(market_card)
                    continue
                market_card.update(detail_realtime_price)
                market_card.update(source_kind="workbook", value_source_kind="workbook", comparison_source_kind="workbook")
                market_card["pending"] = False
                if detail_day_price is not None:
                    market_card["comparison_value"] = float(detail_day_price["value"])
                    market_card["delta"] = (
                        realtime_price_value - float(detail_day_price["value"])
                    )
                    market_card["delta_prefix"] = "较日前"
            updated_market_cards.append(market_card)
        report.setdefault("market", {})["realtime"] = updated_market_cards
    report["realtime"]["cards"] = detail_cards
    detail_appendix = detail_report.get("appendices", {}).get("realtime")
    if detail_appendix is not None:
        report["appendices"]["realtime"] = detail_appendix
    return company_total, unit_total


def _check_realtime_detail_totals(
    detail_report: dict[str, Any], unit_rows: list[dict[str, Any]],
) -> tuple[float, float]:
    company_card = next(
        (
            card
            for card in detail_report.get("realtime", {}).get("cards", [])
            if card.get("label") == "实时出清电量" and not card.get("pending")
        ),
        None,
    )
    if not isinstance(
        company_card.get("value") if company_card else None,
        (int, float),
    ):
        raise ValueError("D-2表格缺少分公司实时出清电量汇总。")

    unit_total = sum(float(row["realtime"]) for row in unit_rows)
    company_total = float(company_card["value"])
    if abs(unit_total - company_total) > 1.0:
        raise ValueError(
            "D-2表格内部实时出清电量核对不一致："
            f"分公司汇总{company_total:g}万千瓦时，"
            f"机组汇总{unit_total:g}万千瓦时。"
        )

    return company_total, unit_total


def _normalized_operation(value: str) -> str:
    return "".join(str(value or "").split()).replace("停备", "备用").rstrip("。")


def _compare_operation_tables(
    current_table: dict[str, Any],
    previous_operations: dict[str, str],
    current_date_label: str,
    previous_date_label: str,
) -> dict[str, Any]:
    dates = list(current_table.get("dates") or [])
    has_embedded_previous_day = dates[:2] == [current_date_label, previous_date_label]
    rows: list[dict[str, Any]] = []
    for row in current_table.get("rows") or []:
        plant = str(row.get("unit") or "")
        statuses = row.get("statuses") or []
        current = str(statuses[0] if statuses else "—")
        embedded_previous = str(statuses[1]) if has_embedded_previous_day and len(statuses) > 1 else ""
        if embedded_previous == "与前一日运行方式一致。":
            previous = current
        elif embedded_previous not in {"", "—", "无对比数据"}:
            previous = embedded_previous
        else:
            previous = str(previous_operations.get(plant) or "—")
        current_display = current
        if current != "—" and previous != "—" and _normalized_operation(current) == _normalized_operation(previous):
            current_display = "与前一日运行方式一致。"
        rows.append({"unit": plant, "statuses": [current_display, previous]})
    return {
        "dates": [current_date_label, previous_date_label],
        "rows": rows,
    }


@reuse_workbook_inputs
def run_pipeline(
    config: AppConfig,
    analysis_date: date,
    company_clearing_xlsx_path: str | Path,
    progress: Callable[[str], None] = lambda _message: None,
    *,
    comparison_clearing_xlsx_path: str | Path | None = None,
    realtime_maintenance_capacity: float | None = None,
    realtime_detail_xlsx_path: str | Path | None = None,
    dongguan_operation_override: str | None = None,
    manual_market: dict[str, Any] | None = None,
) -> RunResult:
    if not comparison_clearing_xlsx_path:
        raise ValueError("请选择上一日对比出清情况表。")
    if not realtime_detail_xlsx_path:
        raise ValueError("请选择 D-2 收益表 Excel。")
    if realtime_maintenance_capacity is not None:
        realtime_maintenance_capacity = float(realtime_maintenance_capacity)
        if not isfinite(realtime_maintenance_capacity) or realtime_maintenance_capacity < 0:
            raise ValueError("实时检修容量必须是大于或等于0的数值。")
    progress("解析当前 Excel 的日报工作表")
    report = parse_daily_report_workbook(
        company_clearing_xlsx_path,
        realtime_day_ahead_path=realtime_detail_xlsx_path,
    )
    realtime_energy_source = report.get("realtime_energy_comparison_source_path")
    if realtime_energy_source:
        progress(f"实时电能指标的同日日前对比来源：{realtime_energy_source}")
    else:
        progress("未找到与实时数据同日且可读取日前电能指标的对比 Excel。")
    operation_override = str(dongguan_operation_override or "").strip()
    if operation_override:
        dongguan_row = next(
            (
                row
                for row in report.get("operations", {}).get("rows", [])
                if row.get("unit") == "东莞厂"
            ),
            None,
        )
        if dongguan_row is None:
            raise ValueError("东莞厂运行方式修正失败：当前日报缺少东莞厂运行方式行。")
        statuses = dongguan_row.get("statuses")
        if not isinstance(statuses, list) or not statuses:
            raise ValueError("东莞厂运行方式修正失败：当前日报缺少当前状态栏。")
        statuses[0] = operation_override
        report.setdefault("operation_overrides", {})["东莞厂"] = operation_override
        progress(f"东莞厂当前运行方式已按手动修正：{operation_override}")
    workbook_date = date.fromisoformat(report["report_date"])
    if workbook_date != analysis_date:
        progress(f"工作簿日期为 {workbook_date.isoformat()}，报告按该日期生成。")
        analysis_date = workbook_date

    manual_market = normalize_manual_market(manual_market, analysis_date)
    comparison_date = analysis_date - timedelta(days=1)
    if comparison_clearing_xlsx_path:
        progress(f"解析 {comparison_date.isoformat()}（D）日前对比表")
        comparison_report = parse_day_ahead_comparison_workbook(
            comparison_clearing_xlsx_path
        )
        detected_comparison_date = date.fromisoformat(comparison_report["report_date"])
        if detected_comparison_date != comparison_date:
            raise ValueError(
                f"D 日前对比表日期为 {detected_comparison_date.isoformat()}，"
                f"应为 {comparison_date.isoformat()}。"
            )
        report["day_ahead"]["cards"] = _apply_day_ahead_comparisons(
            report["day_ahead"]["cards"],
            comparison_report["cards"],
        )
        report["day_ahead_comparison_source_path"] = comparison_report["source_path"]
        report["day_ahead_comparison_date"] = comparison_report["report_date"]
        report["operations"] = _compare_operation_tables(
            report["operations"],
            comparison_report["operations"],
            report["report_date_label"],
            comparison_report["report_date_label"],
        )

    realtime_market_date = analysis_date - timedelta(days=2)
    report["market"]["realtime_date"] = realtime_market_date.isoformat()
    report["market"]["realtime_date_label"] = (
        f"{realtime_market_date.month}月{realtime_market_date.day}日"
    )
    progress(f"解析 {realtime_market_date.isoformat()} D-2 收益表机组明细")
    parsed_units = parse_realtime_unit_workbook(
        realtime_detail_xlsx_path,
        realtime_market_date,
    )
    unit_rows = summarize_realtime_units(parsed_units)
    missing_realtime_rows = [
        row for row in unit_rows if row.get("realtime") is None
    ]
    if missing_realtime_rows:
        raise ValueError(
            "精简加大版实时明细核对不完整："
            f"{len(missing_realtime_rows)} 台机组缺少实时出清电量。"
        )
    detail_report = parse_daily_report_workbook(realtime_detail_xlsx_path)
    if date.fromisoformat(detail_report["report_date"]) != realtime_market_date:
        raise ValueError(
            f"D-2 收益表日期为 {detail_report['report_date']}，应为 {realtime_market_date.isoformat()}。"
        )
    _check_realtime_detail_totals(detail_report, unit_rows)
    realtime_full_day = read_full_day_clearing_table(
        realtime_detail_xlsx_path, realtime_market_date
    )

    progress(f"读取 {realtime_market_date.isoformat()} 市场实时数据（输入日期 D-2）")
    workbook_day_ahead_market = list(report["market"]["day_ahead"])
    workbook_realtime_market = list(report["market"]["realtime"])
    # These comparisons require matching website records, even when collection fails.
    for period, labels in (
        ("day_ahead", ("检修容量",)),
        ("realtime", ("检修容量", "西电东送电力")),
    ):
        report["market"][period] = [
            card for card in report["market"][period] if card.get("label") not in labels
        ] + [
            {
                "label": label,
                "value": None,
                "unit": "万千瓦",
                "delta": None,
                "pending": True,
                "pending_reason": "未取得清能系统对应日期的有效数据",
            }
            for label in labels
        ]
    try:
        try:
            raw = collect_realtime_market_comparison(
                config, realtime_market_date, progress, day_ahead_date=analysis_date,
                **({"allow_partial_market": True} if manual_market else {}),
            )
        except Exception as exc:
            if not manual_market:
                raise
            progress(f"网站读取失败，保留可用输入并检查手动补值：{exc}")
            raw = {"errors": {"website_unavailable": str(exc)}}
        if manual_market:
            for period, workbook_cards, price_label in (
                ("day_ahead", workbook_day_ahead_market, "全省日前均价"),
                ("realtime", workbook_realtime_market, "全省实时均价"),
            ):
                cards = _merge_market_text_cards(raw.get(f"market_{period}_cards") or [], workbook_cards, {price_label})
                merged = merge_manual_market(cards, period, manual_market, analysis_date)
                raw[f"market_{period}_cards"] = merged["cards"]
                progress(f"{'日前' if period == 'day_ahead' else '实时'}市场：手动补齐 {merged['used']} 个数值，网站有效值优先。")
        errors = raw.get("errors") or {}
        for topic, error in errors.items():
            progress(f"网站数据提示：{topic}：{error}")
        for period, label, current_date, baseline_date, error_topics in (
            ("day_ahead", "日前", analysis_date, comparison_date,
             ("market_day_ahead", "thermal_unit_count")),
            ("realtime", "实时", realtime_market_date, realtime_market_date,
             ("market_realtime", "realtime_thermal_unit_count")),
        ):
            details = "；".join(str(errors[topic]) for topic in error_topics if topic in errors)
            if not raw.get(f"market_{period}_cards"):
                raise RuntimeError(f"{current_date} {label}市场数据缺失。{details}")
            unit_label = "市场开机" if period == "day_ahead" else "在线机组数"
            thermal = next((c for c in raw[f"market_{period}_cards"] if c.get("label") == unit_label), {})
            breakdown = thermal.get("thermal_breakdown") or {}
            missing = [name for fuel, name in (("coal", "煤机"), ("gas", "气机"))
                       if breakdown.get(f"{fuel}_delta") is None]
            if missing and not manual_market:
                baseline = "前一日日前" if period == "day_ahead" else "同日日前"
                raise RuntimeError(
                    f"{current_date} {label}煤机、气机台数读取不完整："
                    f"缺少{'、'.join(missing)}台数或 {baseline_date} {baseline}对比数据"
                    f"（分时分类型出清接口）。{details}"
                )
        if raw.get("market_day_ahead_cards"):
            report["market"]["day_ahead"] = _merge_market_text_cards(
                raw["market_day_ahead_cards"],
                workbook_day_ahead_market,
                {"全省日前均价"},
            )
        if raw.get("market_realtime_cards"):
            report["market"]["realtime"] = _merge_market_text_cards(
                raw["market_realtime_cards"],
                workbook_realtime_market,
                {"全省实时均价"},
            )
    except Exception as exc:
        message = f"市场数据读取失败（日前 {analysis_date}、实时 {realtime_market_date}）：{exc}"
        progress(message)
        raise RuntimeError(message) from exc

    if realtime_maintenance_capacity is not None and not manual_market.get("realtime", {}).get("maintenance"):
        report["market"]["realtime"] = _override_market_card_value(
            report["market"]["realtime"],
            "检修容量",
            realtime_maintenance_capacity,
            "万千瓦",
        )
        report["realtime_maintenance_override"] = realtime_maintenance_capacity
        raw["manual_realtime_maintenance_capacity"] = realtime_maintenance_capacity
        progress(f"实时检修容量按手动修正值 {realtime_maintenance_capacity:g} 万千瓦生成。")

    company_realtime_total, unit_realtime_total = _adopt_realtime_detail_source(
        report,
        detail_report,
        unit_rows,
    )
    if manual_market:
        for period in ("day_ahead", "realtime"):
            merged = merge_manual_market(report["market"][period], period, manual_market, analysis_date)
            if merged["missing"]:
                name = "日前" if period == "day_ahead" else "实时"
                raise ValueError(f"{name}市场数据尚未完整，请补充手动输入或检查自动读取：{'、'.join(merged['missing'])}")
            report["market"][period] = merged["cards"]
    report["realtime_date"] = realtime_market_date.isoformat()
    report["realtime_energy_comparison_source_path"] = parsed_units["source_path"]
    report["realtime_energy_comparison_date"] = realtime_market_date.isoformat()
    report["realtime_detail_source_path"] = parsed_units["source_path"]
    report["realtime_full_day"] = realtime_full_day
    progress(
        "全天出清明细：" + (
            report["realtime_full_day"]["pending_message"]
            if report["realtime_full_day"].get("pending")
            else f"已读取 {realtime_market_date.isoformat()} 的24小时及合计。"
        )
    )
    progress(
        "实时出清电量核对通过："
        f"分公司汇总{company_realtime_total:g}万千瓦时，"
        f"机组汇总{unit_realtime_total:g}万千瓦时。"
    )

    output_dir = config.run_dir(analysis_date)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="market-report-") as temporary:
        _, preview_pdf, print_pdf = _render_concise_outputs(
            Path(temporary), report, progress
        )
        assert preview_pdf is not None and print_pdf is not None
        progress("保存两份 PDF")
        try:
            preview_target, print_target = publish_pdf_pair(output_dir, (preview_pdf, print_pdf))
        except Exception as exc:
            raise RuntimeError(f"两份 PDF 保存失败：{exc}") from exc
    progress("完成")
    return RunResult(output_dir, preview_target, print_target)
