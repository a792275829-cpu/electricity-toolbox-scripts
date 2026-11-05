#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.paragraph import Paragraph

SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in {"scripts", "\u811a\u672c"} else SCRIPT_DIR
if str(APP_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(APP_ROOT.parent))
from toolbox_shared.excel_read import read_cell_snapshots
from toolbox_shared.dates import normalize_date_input
from toolbox_shared.storage import data_path

OUTPUT_DIR = data_path(APP_ROOT / "\u8f93\u51fa")
CLEARING_WORKBOOK_DIR = Path.home() / "Downloads"
LOCAL_TEMPLATE_PATH = APP_ROOT / "\u6a21\u677f" / "\u6bcf\u65e5\u751f\u4ea7\u7ecf\u8425\u60c5\u51b5\u6c47\u62a5\uff08\u81ea\u52a8\uff09.docx"
TEMPLATE_PATH = LOCAL_TEMPLATE_PATH if LOCAL_TEMPLATE_PATH.exists() else (
    Path.home()
    / "Desktop"
    / "\u590d\u76d8"
    / "\u6bcf\u65e5\u751f\u4ea7\u7ecf\u8425\u60c5\u51b5\u6c47\u62a5\uff08\u81ea\u52a8\uff09.docx"
)


LOAD_SUBJECT_CODES = ["1", "6", "2", "3"]
GUANGDONG_WEATHER_LATITUDE = 23.1291
GUANGDONG_WEATHER_LONGITUDE = 113.2644
QINGYUAN_DAILY_CLEARING_VALUES = [
    "清  远",
    "清  远",
    "223",
    "697",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "/",
    "4",
    "/",
    "4",
    "182",
    "0",
    "790",
]

DAILY_CLEARING_COLUMNS = 24

DAILY_CLEARING_REQUIRED_HEADERS = (
    "实时偏差（较日前）",
    "实际上网偏差（较实时）",
    "中长期+现货偏差收益",
)

# 快报（日清分）表列号，与 每日生产经营（分列） 的「复制粘贴页」一致。
FIRE_COLUMNS = {
    "中长期电量": 4,
    "中长期电价": 5,
    "日前电量": 6,
    "日前电价": 7,
    "实时电量": 8,
    "实时电价": 9,
    "上网电量": 10,
    "综合电价": 12,
    "总电费": 13,
    "中长期收益": 15,
    "日前偏差电量": 16,
    "日前偏差电价": 17,
    "日前偏差收益": 18,
    "实时偏差电量": 19,
    "实时偏差电价": 20,
    "实时偏差收益": 21,
    "实际电量": 22,
    "实际电价": 23,
    "实际收益": 24,
    "现货电量": 25,
    "现货电价": 26,
    "现货收益": 27,
    "容量电费": 31,
}

RENEWABLE_COLUMNS = {
    "机制电量": 3,
    "中长期电量": 5,
    "中长期电价": 6,
    "日前电量": 7,
    "日前电价": 8,
    "实时电量": 9,
    "实时电价": 10,
    "上网电量": 11,
    "综合电价": 13,
    "总电费": 14,
    "机制收益": 15,
    "中长期收益": 16,
    "日前收益": 19,
    "实时偏差电量": 20,
    "实时偏差电价": 21,
    "实时偏差收益": 22,
    "实际电量": 23,
    "实际电价": 24,
    "实际收益": 25,
    "现货收益": 28,
}


def log(message: str) -> None:
    print(message, flush=True)


def date_label(date_text: str) -> str:
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    return f"{dt.month}\u6708{dt.day}\u65e5"


def day_number(date_text: str) -> str:
    return str(datetime.strptime(date_text, "%Y-%m-%d").day)


def report_filename(date_text: str) -> str:
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    return f"\u6bcf\u65e5\u751f\u4ea7\u7ecf\u8425\u60c5\u51b5\u6c47\u62a5\uff08{dt.month}\u6708{dt.day}\u65e5\uff09.docx"


def previous_report_path(report_date: str, output_dir: Path) -> Path:
    previous_day = datetime.strptime(report_date, "%Y-%m-%d").date() - timedelta(days=1)
    return output_dir / report_filename(previous_day.strftime("%Y-%m-%d"))


def resolve_template_path(
    report_date: str,
    template_path: Path,
    output_dir: Path,
    *,
    prefer_previous_report: bool = True,
) -> Path:
    previous_path = previous_report_path(report_date, output_dir)
    if prefer_previous_report and previous_path.exists():
        return previous_path
    return template_path


def validate_date(date_text: str) -> str:
    return normalize_date_input(date_text)


def trend_word(value: float) -> str:
    return "\u4e0a\u5347" if value >= 0 else "\u4e0b\u964d"


def change_word(value: float) -> str:
    return "\u589e\u52a0" if value >= 0 else "\u51cf\u5c11"


def mw_to_10k_kw(value_mw: float) -> int:
    return round(value_mw / 10)


def fmt_number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "\u5f85\u8865\u5145"
    if digits == 0:
        return str(int(round(value)))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def fmt_mw_as_10k_kw(value_mw: float | None) -> str:
    """Format an optional MW value as ten-thousand kW without crashing on missing API data."""
    return fmt_number(value_mw / 10 if value_mw is not None else None)


UNIT_YUAN_PER_MWH = "\u5143/\u5146\u74e6\u65f6"
UNIT_TAI = "\u53f0"


def delta_phrase(value: float | None, *, unit: str = "", scale: float = 1.0,
                 digits: int = 0, up: str = "\u4e0a\u5347", down: str = "\u4e0b\u964d") -> str:
    """环比措辞：变化量按展示精度取整后为 0 时统一写「持平」。"""
    shown = round((value or 0.0) * scale, digits)
    if abs(shown) < 0.5 * 10 ** (-digits):
        return "\u6301\u5e73"
    return f"{up if shown > 0 else down}{fmt_number(abs(shown), digits)}{unit}"


def startup_change_text(diff: float | None) -> str:
    """开机台数环比，台数为 0 时写「持平」。"""
    return delta_phrase(diff, unit=UNIT_TAI, up="\u589e\u52a0", down="\u51cf\u5c11")


def load_trend_word(diff_mw: float) -> str:
    """负荷方向词：只输出「上升／下降／持平」，不带数值，供句首使用。"""
    return "\u6301\u5e73" if mw_to_10k_kw(diff_mw) == 0 else trend_word(diff_mw)


def load_change_text(diff_mw: float, unit: str = "\u4e07\u5343\u74e6") -> str:
    """负荷环比：变化量四舍五入为 0 时写「持平」，与人工报告一致。"""
    return delta_phrase(diff_mw, unit=unit, scale=0.1)


def weather_code_label(code: Any) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "\u591a\u4e91"
    if value == 0:
        return "\u6674"
    if value in {1, 2}:
        return "\u591a\u4e91"
    if value == 3:
        return "\u9634"
    if value in {45, 48}:
        return "\u96fe"
    if value in {51, 53, 55, 56, 57, 61, 80}:
        return "\u5c0f\u96e8"
    if value in {63, 66, 81}:
        return "\u4e2d\u96e8"
    if value in {65, 67, 82}:
        return "\u5927\u96e8"
    if value in {71, 73, 75, 77, 85, 86}:
        return "\u96ea"
    if value in {95, 96, 99}:
        return "\u96f7\u9635\u96e8"
    return "\u591a\u4e91"


def fetch_guangdong_weather_text(report_date: str) -> str:
    start = datetime.strptime(report_date, "%Y-%m-%d").date()
    end = start + timedelta(days=2)
    query = urllib.parse.urlencode(
        {
            "latitude": GUANGDONG_WEATHER_LATITUDE,
            "longitude": GUANGDONG_WEATHER_LONGITUDE,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "Asia/Shanghai",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.load(response)
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    else:
        raise RuntimeError(f"\u8bfb\u53d6\u5e7f\u4e1c\u5929\u6c14\u5931\u8d25\uff1a{last_error}") from last_error
    daily = data.get("daily") or {}
    labels = [weather_code_label(code) for code in daily.get("weather_code") or []]
    max_values = [parse_float(value) for value in daily.get("temperature_2m_max") or [] if value is not None]
    min_values = [parse_float(value) for value in daily.get("temperature_2m_min") or [] if value is not None]
    if not labels or not max_values or not min_values:
        raise RuntimeError("\u672a\u8bfb\u53d6\u5230\u5e7f\u4e1c\u672a\u6765\u4e09\u5929\u5929\u6c14\u6570\u636e")
    weather_text = labels[0] if labels[0] == labels[-1] else f"{labels[0]}\u8f6c{labels[-1]}"
    return f"\u672a\u6765\u4e09\u5929{weather_text}\uff0c\u6c14\u6e29{round(min(min_values))}\u2103-{round(max(max_values))}\u2103\uff0c"


def replace_weather_prefix(paragraph, report_date: str) -> None:
    prefix = fetch_guangdong_weather_text(report_date)
    text = paragraph.text
    updated = re.sub(r"^\u672a\u6765\u4e09\u5929.*?\uff0c\u6c14\u6e29\s*.*?\u2103\s*-\s*.*?\u2103\uff0c", prefix, text, count=1)
    if updated == text:
        # 新模板写作「…，气温于23-30℃之间波动；」，只有一处 ℃，按分句截断。
        updated = re.sub(r"^\u672a\u6765\u4e09\u5929.*?\uff0c\u6c14\u6e29[^\uff1b;\u3002]*[\uff1b;\u3002]", prefix, text, count=1)
    if updated == text:
        updated = prefix + text
    replace_paragraph(paragraph, updated)


def parse_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def find_first_column_row(sheet, labels: tuple[str, ...]) -> int:
    wanted = {label.strip() for label in labels}
    for row in range(1, sheet.max_row + 1):
        value = sheet.cell(row, 1).value
        if isinstance(value, str) and value.strip() in wanted:
            return row
    raise KeyError(f"{sheet.title} 中未找到行：{'/'.join(labels)}")


def find_clearing_workbook(date_text: str, workbook_dir: Path = CLEARING_WORKBOOK_DIR) -> Path:
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    date_token = f"{dt.year}.{dt.month}.{dt.day}"
    candidates = [
        path
        for path in workbook_dir.glob("*.xlsx")
        if not path.name.startswith("~$")
        and date_token in path.name
        and "\u51fa\u6e05\u60c5\u51b5" in path.name
    ]
    if not candidates:
        raise FileNotFoundError(f"未找到 {date_token} 的出清情况 Excel：{workbook_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


_clearing_inputs = ContextVar("early_report_clearing_inputs", default=None)


@contextmanager
def clearing_input_cache():
    """Keep parsed values only for one report, never open workbook handles."""
    if _clearing_inputs.get() is not None:
        yield
        return
    token = _clearing_inputs.set({})
    try:
        yield
    finally:
        _clearing_inputs.reset(token)


def _read_clearing_values(path):
    source = Path(path).resolve()
    info = source.stat()
    key = (source, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    cache = _clearing_inputs.get()
    if cache is not None and key in cache:
        return cache[key]
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        sheet_name = next((name for name in workbook.sheetnames if "快报" in name and "日前" in name), None)
        if sheet_name is None:
            raise KeyError(f"{path} 中未找到快报（日前）工作表")
        values, labels = {}, {}
        for row_number, row in enumerate(workbook[sheet_name].iter_rows(min_col=1, max_col=8, values_only=True), 1):
            if isinstance(row[0], str):
                labels.setdefault(row[0].strip(), row_number)
            for column in (6, 7, 8):
                if row[column - 1] is not None:
                    values[(row_number, column)] = row[column - 1]
        basic = None
        unit_rows: list[tuple] = []
        if "基础信息" in workbook.sheetnames:
            snapshots = read_cell_snapshots(workbook, [("基础信息", cell) for cell in ("AA64", "AC64")])
            basic = {cell: snapshots[("基础信息", cell)][0] for cell in ("AA64", "AC64")}
            unit_rows = [
                row
                for row in workbook["基础信息"].iter_rows(
                    min_col=1, max_col=7, max_row=80, values_only=True
                )
            ]
        result = (sheet_name, values, labels, basic, unit_rows)
    finally:
        workbook.close()
    if cache is not None:
        cache[key] = result
    return result


def _clearing_label_row(labels, wanted, sheet_name):
    matches = [labels[label.strip()] for label in wanted if label.strip() in labels]
    if not matches:
        raise KeyError(f"{sheet_name} 中未找到行：{'/'.join(wanted)}")
    return min(matches)


def load_day_ahead_clearing_summary(date_text: str, workbook_path: Path | None = None) -> dict[str, Any]:
    if workbook_path is None:
        workbook_path = find_clearing_workbook(date_text)
    sheet_name, values, label_rows, basic, _unit_rows = _read_clearing_values(workbook_path)
    value = lambda row, column: values.get((row, column))
    qingyuan_raw_values = basic if basic is not None else {"AA64": 0, "AC64": 0}
    if basic is not None:
        missing_qingyuan_cells = [cell for cell, number in basic.items() if number is None]
        if missing_qingyuan_cells:
            raise ValueError(
                f"{workbook_path} 的基础信息!{'/'.join(missing_qingyuan_cells)}没有可用计算结果，"
                "请先在 Excel 中重新计算并保存"
            )
    qingyuan_components = {
        cell: parse_float(value)
        for cell, value in qingyuan_raw_values.items()
    }
    qingyuan_energy = sum(qingyuan_components.values()) / 10

    rows = {
        "company": 7,
        "\u6c55\u5934": 8,
        "\u6d77\u95e8": 13,
        "\u4e1c\u839e": 19,
        "\u6d77\u4e0a\u98ce\u7535": _clearing_label_row(label_rows, ("\u6c55\u5934\u6d77\u98ce", "\u6d77\u4e0a\u98ce\u7535"), sheet_name),
        "\u9b80\u83b2": _clearing_label_row(label_rows, ("\u9b80\u83b2\u5149\u4f0f", "\u9b80\u83b2"), sheet_name),
        "\u5f52\u6e56": _clearing_label_row(label_rows, ("\u5f52\u6e56\u5149\u4f0f", "\u5f52\u6e56"), sheet_name),
    }
    base_total_energy = parse_float(value(rows["company"], 6))
    # 分公司口径 F7 只含火电三厂，新能源在下方独立区块（G 列为电量），
    # 报告要求「所有单元相加」，因此总数＝火电 + 新能源 + 清远计划。
    renewable_energy = sum(
        parse_float(value(rows[label], 7))
        for label in ("\u6d77\u4e0a\u98ce\u7535", "\u9b80\u83b2", "\u5f52\u6e56")
    )
    return {
        "workbook": str(workbook_path),
        "sheet": sheet_name,
        "qingyuanSource": {
            "sheet": "基础信息" if basic is not None else None,
            "cells": qingyuan_components,
        },
        "baseTotalEnergy": base_total_energy,
        "renewableEnergy": renewable_energy,
        "totalEnergy": base_total_energy + renewable_energy + qingyuan_energy,
        "averagePrice": parse_float(value(rows["company"], 7)),
        "companies": {
            "\u6c55\u5934": {
                "energy": parse_float(value(rows["\u6c55\u5934"], 6)),
                "price": parse_float(value(rows["\u6c55\u5934"], 7)),
            },
            "\u6d77\u95e8": {
                "energy": parse_float(value(rows["\u6d77\u95e8"], 6)),
                "price": parse_float(value(rows["\u6d77\u95e8"], 7)),
            },
            "\u4e1c\u839e": {
                "energy": parse_float(value(rows["\u4e1c\u839e"], 6)),
                "price": parse_float(value(rows["\u4e1c\u839e"], 7)),
            },
            "\u6d77\u4e0a\u98ce\u7535": {
                "energy": parse_float(value(rows["\u6d77\u4e0a\u98ce\u7535"], 7)),
                "price": parse_float(value(rows["\u6d77\u4e0a\u98ce\u7535"], 8)),
            },
            "\u9b80\u83b2": {
                "energy": parse_float(value(rows["\u9b80\u83b2"], 7)),
                "price": parse_float(value(rows["\u9b80\u83b2"], 8)),
            },
            "\u5f52\u6e56": {
                "energy": parse_float(value(rows["\u5f52\u6e56"], 7)),
                "price": parse_float(value(rows["\u5f52\u6e56"], 8)),
            },
            "\u6e05\u8fdc": {
                "energy": qingyuan_energy,
            },
        },
    }


def load_online_price_summary(date_text: str, workbook_path: Path | None = None) -> dict[str, Any]:
    if workbook_path is None:
        workbook_path = find_clearing_workbook(date_text)
    sheet_name, values, label_rows, basic, _unit_rows = _read_clearing_values(workbook_path)
    value = lambda row, column: values.get((row, column))
    renewable_rows = {
        "\u6d77\u4e0a\u98ce\u7535": _clearing_label_row(label_rows, ("\u6c55\u5934\u6d77\u98ce", "\u6d77\u4e0a\u98ce\u7535"), sheet_name),
        "\u9b80\u83b2": _clearing_label_row(label_rows, ("\u9b80\u83b2\u5149\u4f0f", "\u9b80\u83b2"), sheet_name),
        "\u5f52\u6e56": _clearing_label_row(label_rows, ("\u5f52\u6e56\u5149\u4f0f", "\u5f52\u6e56"), sheet_name),
    }
    return {
        "workbook": str(workbook_path),
        "sheet": sheet_name,
        "averagePrice": parse_float(value(7, 7)),
        "companies": {
            "\u6c55\u5934": parse_float(value(8, 7)),
            "\u6d77\u95e8": parse_float(value(13, 7)),
            "\u4e1c\u839e": parse_float(value(19, 7)),
            "\u6d77\u4e0a\u98ce\u7535": parse_float(value(renewable_rows["\u6d77\u4e0a\u98ce\u7535"], 8)),
            "\u9b80\u83b2": parse_float(value(renewable_rows["\u9b80\u83b2"], 8)),
            "\u5f52\u6e56": parse_float(value(renewable_rows["\u5f52\u6e56"], 8)),
        },
    }


def _cell_number(sheet, row: int, column: int) -> float | None:
    value = sheet.cell(row, column).value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _pick_sheet(workbook, keyword: str, *, exclude: tuple[str, ...] = ()):
    for name in workbook.sheetnames:
        if keyword in name and not any(token in name for token in exclude):
            return workbook[name]
    raise KeyError(f"{workbook} 中未找到包含「{keyword}」的工作表")


def _locate_row(sheet, *, label: str | None = None, sublabel: str | None = None,
                start: int = 1, end: int | None = None) -> int:
    last = end if end is not None else sheet.max_row
    for row in range(start, last + 1):
        first = sheet.cell(row, 1).value
        second = sheet.cell(row, 2).value
        first = first.strip() if isinstance(first, str) else None
        second = second.strip() if isinstance(second, str) else None
        if label is not None and first != label:
            continue
        if sublabel is not None:
            if second != sublabel:
                continue
            if first not in (None, label or first):
                continue
        if label is not None or sublabel is not None:
            return row
    target = label or sublabel or ""
    raise KeyError(f"{sheet.title} 中未找到行：{target}")


def _clear_int(value: float | None) -> str:
    """按 0 位小数格式化；负值四舍五入到 0 时保留 -0，与 Excel 显示一致。"""
    if value is None:
        return "/"
    negative = value < 0
    rounded = int(abs(value) + 0.5)
    if rounded == 0:
        return "-0" if negative else "0"
    return f"-{rounded}" if negative else str(rounded)


def _add(*values: float | None) -> float | None:
    if any(value is None for value in values):
        return None
    return sum(values)  # type: ignore[arg-type]


def _weighted_price(pairs: list[tuple[float | None, float | None]]) -> float | None:
    usable = [(weight, price) for weight, price in pairs if weight and price is not None]
    total = sum(weight for weight, _ in usable)
    if not total:
        return None
    return sum(weight * price for weight, price in usable) / total


def _energy_price(total_fee: float | None, capacity_fee: float | None,
                  online_energy: float | None) -> float | None:
    if not online_energy:
        return 0.0
    if total_fee is None:
        return None
    return (total_fee - (capacity_fee or 0.0)) / online_energy * 1000


def _fire_row(sheet, row: int) -> dict[str, float | None]:
    return {name: _cell_number(sheet, row, column) for name, column in FIRE_COLUMNS.items()}


def _renewable_row(sheet, row: int) -> dict[str, float | None]:
    return {name: _cell_number(sheet, row, column) for name, column in RENEWABLE_COLUMNS.items()}


def _fire_table_values(source: dict[str, float | None], *, spot_income: float | None,
                       total_fee: float | None, energy_price: float | None,
                       composite_price: float | None, blank_online: bool = False,
                       spot_blank: bool = False) -> dict[int, str]:
    long_term_income = source.get("中长期收益")
    returns = None if spot_income is None else spot_income + (long_term_income or 0.0)
    return {
        3: _clear_int(source.get("中长期电量")),
        4: _clear_int(source.get("中长期电价")),
        5: _clear_int(source.get("日前电量")),
        6: _clear_int(source.get("日前电价")),
        7: _clear_int(source.get("实时电量")),
        8: _clear_int(source.get("实时电价")),
        9: "/" if blank_online else _clear_int(source.get("上网电量")),
        10: _clear_int(source.get("日前偏差电量")),
        11: _clear_int(source.get("日前偏差电价")),
        12: _clear_int(source.get("日前偏差收益")),
        13: _clear_int(source.get("实时偏差电量")),
        14: _clear_int(source.get("实时偏差电价")),
        15: _clear_int(source.get("实时偏差收益")),
        16: _clear_int(source.get("实际电量")),
        17: _clear_int(source.get("实际电价")),
        18: _clear_int(source.get("实际收益")),
        19: _clear_int(long_term_income),
        20: "/" if (spot_blank or spot_income is None) else _clear_int(spot_income),
        21: _clear_int(returns if not spot_blank else long_term_income),
        22: _clear_int(total_fee),
        23: _clear_int(energy_price),
        24: _clear_int(composite_price),
    }


def _renewable_table_values(source: dict[str, float | None]) -> dict[int, str]:
    long_term_income = source.get("中长期收益")
    spot_income = source.get("现货收益")
    returns = None if spot_income is None else spot_income + (long_term_income or 0.0)
    return {
        3: _clear_int(source.get("中长期电量")),
        4: _clear_int(source.get("中长期电价")),
        5: _clear_int(source.get("日前电量")),
        6: _clear_int(source.get("日前电价")),
        7: _clear_int(source.get("实时电量")),
        8: _clear_int(source.get("实时电价")),
        9: _clear_int(source.get("上网电量")),
        10: _clear_int(source.get("机制电量")),
        11: _clear_int(source.get("机制收益")),
        12: _clear_int(source.get("日前收益")),
        13: _clear_int(source.get("实时偏差电量")),
        14: _clear_int(source.get("实时偏差电价")),
        15: _clear_int(source.get("实时偏差收益")),
        16: _clear_int(source.get("实际电量")),
        17: _clear_int(source.get("实际电价")),
        18: _clear_int(source.get("实际收益")),
        19: _clear_int(long_term_income),
        20: _clear_int(spot_income),
        21: "/" if returns is None else _clear_int(returns),
        22: _clear_int(source.get("总电费")),
        23: _clear_int(_energy_price(source.get("总电费"), source.get("机制收益"),
                                    source.get("上网电量"))),
        24: _clear_int(source.get("综合电价")),
    }


def load_daily_clearing_inputs(date_text: str,
                               workbook_path: Path | None = None) -> dict[str, Any]:
    """读取 D-2 日出清情况工作簿，生成日清分表各行的取值与现货偏差分析用数。"""
    if workbook_path is None:
        workbook_path = find_clearing_workbook(date_text)
    workbook = load_workbook(workbook_path, data_only=True)
    try:
        brief = _pick_sheet(workbook, "快报", exclude=("日前",))
        qingyuan_row = _locate_row(brief, label="清远", sublabel="合计")
        haimen_row = _locate_row(brief, label="海门", sublabel="合计")
        shantou_row = _locate_row(brief, label="汕头", sublabel="合计")
        company_row = _locate_row(brief, label="分公司")
        dongguan_row = _locate_row(brief, label="东莞", sublabel="合计")

        shantou_1 = _fire_row(brief, _locate_row(brief, sublabel="#1机组", start=shantou_row,
                                                 end=haimen_row))
        shantou_2 = _fire_row(brief, _locate_row(brief, sublabel="#2机组", start=shantou_row,
                                                 end=haimen_row))
        shantou_3 = _fire_row(brief, _locate_row(brief, sublabel="#3机组", start=shantou_row,
                                                 end=haimen_row))
        combined = {
            name: _add(shantou_2.get(name), shantou_3.get(name))
            for name in ("中长期电量", "日前电量", "实时电量", "上网电量", "中长期收益",
                         "日前偏差电量", "实时偏差电量", "实际电量",
                         "日前偏差收益", "实时偏差收益", "实际收益", "总电费", "容量电费")
        }
        for name, weight_name in (("中长期电价", "中长期电量"), ("日前电价", "日前电量"),
                                  ("实时电价", "实时电量"), ("日前偏差电价", "日前偏差电量"),
                                  ("实时偏差电价", "实时偏差电量"), ("实际电价", "实际电量")):
            combined[name] = _weighted_price([
                (shantou_2.get(weight_name), shantou_2.get(name)),
                (shantou_3.get(weight_name), shantou_3.get(name)),
            ])
        denominator = _add(shantou_3.get("中长期电量"), shantou_3.get("现货电量"),
                           shantou_2.get("中长期电量"), shantou_2.get("现货电量"))
        if denominator and shantou_2.get("总电费") is not None and shantou_3.get("总电费") is not None:
            combined["综合电价"] = (shantou_2["总电费"] + shantou_3["总电费"]) / denominator * 1000
        else:
            combined["综合电价"] = None

        company = _fire_row(brief, company_row)
        shantou = _fire_row(brief, shantou_row)
        haimen = _fire_row(brief, haimen_row)
        dongguan = _fire_row(brief, dongguan_row)
        qingyuan = _fire_row(brief, qingyuan_row)

        # 「现货偏差收益」口径：汕头/东莞取现货偏差合计边际贡献，海门取三项偏差边际贡献之和，
        # 分公司 = 三厂之和（不含清远），与「每日生产经营（分列）」表一致。
        shantou_spot = shantou.get("现货收益")
        dongguan_spot = dongguan.get("现货收益")
        haimen_spot = _add(haimen.get("日前偏差收益"), haimen.get("实时偏差收益"),
                           haimen.get("实际收益"))
        company_spot = _add(shantou_spot, haimen_spot, dongguan_spot)
        company_fee = _add(shantou.get("总电费"), haimen.get("总电费"),
                           dongguan.get("总电费"), qingyuan.get("总电费"))

        rows: list[tuple[str, str | None, dict[int, str]]] = [
            ("分公司火电", None, _fire_table_values(
                company, spot_income=company_spot, total_fee=company_fee,
                energy_price=_energy_price(company.get("总电费"), company.get("容量电费"),
                                           company.get("上网电量")),
                composite_price=company.get("综合电价"))),
            ("汕头", None, _fire_table_values(
                shantou, spot_income=shantou_spot, total_fee=shantou.get("总电费"),
                energy_price=_energy_price(shantou.get("总电费"), shantou.get("容量电费"),
                                           shantou.get("上网电量")),
                composite_price=shantou.get("综合电价"))),
            ("汕头", "#1", _fire_table_values(
                shantou_1, spot_income=shantou_1.get("现货收益"),
                total_fee=shantou_1.get("总电费"),
                energy_price=_energy_price(shantou_1.get("总电费"), shantou_1.get("容量电费"),
                                           shantou_1.get("上网电量")),
                composite_price=shantou_1.get("综合电价"))),
            ("汕头", "#2#3", _fire_table_values(
                combined, spot_income=_add(shantou_2.get("现货收益"), shantou_3.get("现货收益")),
                total_fee=combined.get("总电费"),
                energy_price=_energy_price(combined.get("总电费"), combined.get("容量电费"),
                                           combined.get("上网电量")),
                composite_price=combined.get("综合电价"))),
            ("海门", None, _fire_table_values(
                haimen, spot_income=haimen_spot, total_fee=haimen.get("总电费"),
                energy_price=_energy_price(haimen.get("总电费"), haimen.get("容量电费"),
                                           haimen.get("上网电量")),
                composite_price=haimen.get("综合电价"))),
            ("东莞", None, _fire_table_values(
                dongguan, spot_income=dongguan_spot, total_fee=dongguan.get("总电费"),
                energy_price=_energy_price(dongguan.get("总电费"), dongguan.get("容量电费"),
                                           dongguan.get("上网电量")),
                composite_price=dongguan.get("综合电价"))),
            ("清远", None, _fire_table_values(
                qingyuan, spot_income=None, total_fee=qingyuan.get("总电费"),
                energy_price=0.0,
                composite_price=qingyuan.get("综合电价") or 0.0,
                blank_online=True, spot_blank=True)),
        ]

        heading = next((row for row in range(company_row, brief.max_row + 1)
                        if isinstance(brief.cell(row, 1).value, str)
                        and brief.cell(row, 1).value.strip() == "新能源"), None)
        if heading is not None:
            renewable_rows = {
                "分公司新能源": _locate_row(brief, label="分公司", start=heading),
                "海风": _locate_row(brief, label="汕头海风", start=heading),
                "鮀莲": _locate_row(brief, label="鮀莲光伏", start=heading),
                "归湖": _locate_row(brief, label="归湖光伏", start=heading),
            }
            for label, row in renewable_rows.items():
                rows.append((label, None, _renewable_table_values(_renewable_row(brief, row))))
        else:
            log("快报中未找到新能源区，新能源各行保留模板原值")

        spot_skip_reason = None
        try:
            spot = _load_spot_deviation(workbook)
        except SpotDataNotReady as exc:
            spot = None
            spot_skip_reason = str(exc)
            log(f"现货偏差分析未取数，保留模板原文：{spot_skip_reason}")
    finally:
        workbook.close()
    return {
        "workbook": str(workbook_path),
        "rows": rows,
        "spot": spot,
        "spotSkipReason": spot_skip_reason,
    }


UNIT_STATUS_PLANTS = ("汕头", "海门", "东莞", "清远")
UNIT_STATUS_RANK = ("运行", "备用", "供热", "检修")
UNIT_STATUS_WORDS = {"运行": "运行", "备用": "备用", "停备": "备用", "停机": "备用", "供热": "供热", "检修": "检修"}
DONGGUAN_STATUS_WORDS = {"运行": "调峰", "备用": "备用", "停备": "备用", "供热": "供热", "检修": "检修"}
CHINESE_DIGITS = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八"}
SECTION_HEADING = re.compile(r"^[一二三四五六七八九十]+、")


def _parse_unit_status(rows: list[tuple]) -> dict[str, list[tuple[str, str]]]:
    """解析基础信息「机组状态」区块，返回 {厂名: [(机组号, 状态), ...]}。"""
    start = None
    for index, row in enumerate(rows):
        if len(row) > 2 and isinstance(row[2], str) and row[2].strip() == "机组状态":
            start = index + 1
            break
    if start is None:
        return {}

    parsed: dict[str, list[tuple[str, str]]] = {}
    plant = None
    for row in rows[start:]:
        first = row[0].strip() if isinstance(row[0], str) else ""
        second = row[1].strip() if len(row) > 1 and isinstance(row[1], str) else ""
        status = row[2].strip() if len(row) > 2 and isinstance(row[2], str) else ""
        if first and SECTION_HEADING.match(first):
            break
        if first in UNIT_STATUS_PLANTS:
            plant = first
        if plant is None or not second.startswith("#"):
            continue
        parsed.setdefault(plant, []).append((second[1:].strip(), status))
    return parsed


def _status_rank(status: str) -> int:
    normalized = status if status in UNIT_STATUS_RANK else (
        "备用" if status in ("停备", "停机") else status
    )
    try:
        return UNIT_STATUS_RANK.index(normalized)
    except ValueError:
        return len(UNIT_STATUS_RANK)


def _unit_label(plant: str, key: str) -> str:
    if plant in ("东莞", "清远"):
        # 东莞、清远按「套」分组：#1,2=一套、#3,4=二套、#5,6=三套、#7,8=四套。
        head = key.split(",")[0].strip()
        if not head.isdigit():
            return head
        suite = (int(head) + 1) // 2
        return CHINESE_DIGITS.get(str(suite), str(suite))
    return f"#{key}"


def build_unit_status_text(plant: str, units: list[tuple[str, str]]) -> str | None:
    """按厂内既有写法拼机组启停句子，返回不带「某某厂：」前缀的正文。"""
    units = [(key, status) for key, status in units if status]
    if not units:
        return None

    if plant == "清远":
        clauses = [
            f"{_unit_label(plant, key)}套机组{UNIT_STATUS_WORDS.get(status, status)}"
            for key, status in units
        ]
        return "；".join(clauses) + "。"

    words = DONGGUAN_STATUS_WORDS if plant == "东莞" else UNIT_STATUS_WORDS
    unit_suffix = "套机组" if plant == "东莞" else "机组"
    separator = "，" if plant == "东莞" else "；"

    # 按「输出用词」聚合：备用/停备/停机都写「备用」，不能因为输入不同就拆成两句。
    grouped: dict[str, list[str]] = {}
    ranks: dict[str, int] = {}
    for key, status in units:
        word = words.get(status, status)
        grouped.setdefault(word, []).append(_unit_label(plant, key))
        ranks[word] = min(ranks.get(word, _status_rank(status)), _status_rank(status))

    ordered = sorted(grouped.items(), key=lambda item: ranks[item[0]])
    clauses = [f"{'、'.join(labels)}{unit_suffix}{word}" for word, labels in ordered]
    return separator.join(clauses) + "。"


def load_unit_status_texts(date_text: str,
                           workbook_path: Path | None = None) -> dict[str, str]:
    if workbook_path is None:
        workbook_path = find_clearing_workbook(date_text)
    rest = _read_clearing_values(workbook_path)
    unit_rows = rest[-1]
    return {
        plant: text
        for plant, units in _parse_unit_status(unit_rows).items()
        if (text := build_unit_status_text(plant, units))
    }


def _startup_blocks(document) -> list[list]:
    dates = startup_date_paragraphs(document)[:2]
    if len(dates) < 2:
        return []
    heading = find_exact_paragraph(document, "二、分公司电量、电价完成情况")
    blocks = []
    for index, date_paragraph in enumerate(dates):
        start = paragraph_index(document, date_paragraph) + 1
        end = (
            paragraph_index(document, dates[index + 1])
            if index + 1 < len(dates)
            else paragraph_index(document, heading)
        )
        blocks.append(document.paragraphs[start:end])
    return blocks


def fill_startup_status(document, current: dict[str, str],
                        previous: dict[str, str]) -> int:
    """填写两日机组启停；与前一日本地一致时写「与前一日运行方式一致」。"""
    blocks = _startup_blocks(document)
    if len(blocks) < 2:
        return 0
    filled = 0
    for index, texts in ((0, previous), (1, current)):
        for plant in UNIT_STATUS_PLANTS:
            body = texts.get(plant)
            if not body:
                continue
            target = next(
                (paragraph for paragraph in blocks[index]
                 if paragraph.text.strip().startswith(f"{plant}厂：")),
                None,
            )
            if target is None:
                continue
            if index == 1 and previous.get(plant) == body:
                body = "与前一日运行方式一致。"
            replace_paragraph(target, f"{plant}厂：{body}")
            filled += 1
    return filled


def _node_price_range(workbook, sheet_names: tuple[str, ...]) -> tuple[float | None, float | None]:
    """按「实时最低价／实时最高价」标签跨厂取极值，避免写死 AF/AL 列号。"""
    lows: list[float] = []
    highs: list[float] = []
    for name in sheet_names:
        if name not in workbook.sheetnames:
            continue
        sheet = workbook[name]
        for row in range(1, min(sheet.max_row, 40) + 1):
            for column in range(1, sheet.max_column + 1):
                value = sheet.cell(row, column).value
                if not isinstance(value, str):
                    continue
                label = value.strip()
                if label not in ("实时最低价", "实时最高价"):
                    continue
                values = [
                    _cell_number(sheet, row, column + offset)
                    for offset in range(1, 5)
                ]
                numbers = [item for item in values if item is not None]
                if not numbers:
                    continue
                (lows if label == "实时最低价" else highs).extend(numbers)
    return (min(lows) if lows else None, max(highs) if highs else None)


class SpotDataNotReady(ValueError):
    """实时出清数据还没生成（典型是把报告日工作簿误当成 D-2 使用）。"""


def _load_spot_deviation(workbook) -> dict[str, Any] | None:
    if "分公司" not in workbook.sheetnames:
        return None
    sheet = workbook["分公司"]
    row = _locate_row(sheet, label="\u5206\u516c\u53f8\u706b\u7535")
    capacity = _cell_number(sheet, row, 2)
    realtime_energy = _cell_number(sheet, row, 7)
    deviation = _cell_number(sheet, row, 11)
    actual_deviation = _cell_number(sheet, row, 13)
    income = _cell_number(sheet, row, 14)
    # 实时尚未出清时 G/K/M/N 全为 0，照抄会输出「实时出清 0、偏差 0、增收 0」，
    # 判定为数据未出，交给上层保留模板原文并提示人工补数。
    if not any((realtime_energy, deviation, actual_deviation, income)):
        raise SpotDataNotReady(
            "\u300c\u5206\u516c\u53f8\u300d\u8868\u300c\u5206\u516c\u53f8\u706b\u7535\u300d\u884c\u7684\u5b9e\u65f6\u51fa\u6e05\u6570\u636e\u4e3a 0"
            "\uff08\u5b9e\u65f6\u7535\u91cf\u3001\u504f\u5dee\u3001\u5b9e\u9645\u504f\u5dee\u3001\u6536\u76ca\u5168\u4e3a\u7a7a\uff09"
            "\uff0c\u7591\u4f3c\u628a\u62a5\u544a\u65e5\u5f53\u5929\u7684\u5de5\u4f5c\u7c3f\u5f53\u6210\u4e86 D-2"
        )
    low, high = _node_price_range(workbook, ("\u6c55\u5934", "\u6d77\u95e8", "\u4e1c\u839e"))
    rate = None
    if capacity and realtime_energy is not None:
        rate = round(realtime_energy / (capacity * 24), 3) * 100
    return {
        "realtimeEnergy": realtime_energy,
        "deviation": deviation,
        "loadRate": rate,
        "minPrice": low,
        "maxPrice": high,
        "realtimePrice": _cell_number(sheet, row, 8),
        "dayAheadPrice": _cell_number(sheet, row, 6),
        "actualDeviation": actual_deviation,
        "income": income,
    }


def _round_half_up(value: float, digits: int = 0) -> float:
    factor = 10 ** digits
    return math.floor(abs(value) * factor + 0.5) / factor * (1 if value >= 0 else -1)


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{_round_half_up(value, 2):.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _fmt_amount(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(_round_half_up(value, 0)))


def build_spot_deviation_texts(spot: dict[str, Any]) -> tuple[str, str]:
    rate = spot.get("loadRate")
    rate_text = "—" if rate is None else f"{rate:.1f}"
    realtime = (
        f"南方分公司实时出清电量{_fmt_amount(spot.get('realtimeEnergy'))}万千瓦时，"
        f"偏差电量（较日前）{_fmt_price(spot.get('deviation'))}万千瓦时，"
        f"平均运行负荷率{rate_text}%，"
        f"最低节点电价{_fmt_price(spot.get('minPrice'))}元/MWh，"
        f"最高节点电价{_fmt_price(spot.get('maxPrice'))}元/MWh，"
        f"加权均价为{_fmt_price(spot.get('realtimePrice'))}元/MWh"
        f"（日前：{_fmt_price(spot.get('dayAheadPrice'))}元/MWh）。"
    )
    actual = (
        f"南方分公司火电机组实际上网偏差电量为{_fmt_amount(spot.get('actualDeviation'))}万千瓦时，"
        f"实现{_fmt_amount(spot.get('income'))}万元增收。"
    )
    return realtime, actual


def _label_key(cell) -> str:
    return re.sub(r"\s+", "", cell.text) if cell is not None else ""


def _document_row_index(table, label: str, sublabel: str | None = None) -> int | None:
    for index, row in enumerate(table.rows):
        cells = row.cells
        first = _label_key(cells[0]) if cells else ""
        second = _label_key(cells[1]) if len(cells) > 1 else ""
        if sublabel is None:
            if first == label:
                return index
        elif second == sublabel and (first in ("", label)):
            return index
    return None


def fill_daily_clearing_table(table, rows: list[tuple[str, str | None, dict[int, str]]]) -> int:
    if len(table.columns) != DAILY_CLEARING_COLUMNS:
        log("日清分表不是 24 列新版结构，跳过自动填数")
        return 0
    filled = 0
    for label, sublabel, values in rows:
        row_index = _document_row_index(table, label, sublabel)
        if row_index is None:
            log(f"日清分表未找到 {label}{sublabel or ''} 行，跳过")
            continue
        cells = table.rows[row_index].cells
        for column, text in values.items():
            if column <= len(cells):
                replace_cell_text(cells[column - 1], text)
        filled += 1
    return filled


def fill_spot_deviation_analysis(document, texts: tuple[str, str]) -> bool:
    targets = (("日前-实时：", texts[0]), ("实际-实时：", texts[1]))
    if not all(any(paragraph.text.strip() == label for paragraph in document.paragraphs)
               for label, _ in targets):
        log("模板不是「日前-实时／实际-实时」两段式，跳过现货偏差分析填数")
        return False
    for label, text in targets:
        label_paragraph = find_exact_paragraph(document, label)
        replace_paragraph(next_nonempty_paragraph(document, label_paragraph), text)
    return True


def copy_run_format(source, target) -> None:
    source_rpr = source._element.rPr
    if source_rpr is not None:
        target_rpr = target._element.rPr
        if target_rpr is not None:
            target._element.remove(target_rpr)
        target._element.insert(0, deepcopy(source_rpr))
    target.bold = source.bold
    target.italic = source.italic
    target.underline = source.underline
    target.font.name = source.font.name
    target.font.size = source.font.size

    # Word uses w:eastAsia for Chinese text. python-docx's font.name alone only
    # sets ascii/hAnsi in some cases, which causes visible font fallback.
    rpr = target._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    font_name = source.font.name or "\u4eff\u5b8b"
    rfonts.set(qn("w:eastAsia"), font_name)
    rfonts.set(qn("w:ascii"), font_name)
    rfonts.set(qn("w:hAnsi"), font_name)


def replace_paragraph(paragraph, text: str, *, keep_red: bool = False) -> None:
    template_run = paragraph.runs[0] if paragraph.runs else None
    for run_idx in range(len(paragraph.runs) - 1, -1, -1):
        paragraph._element.remove(paragraph.runs[run_idx]._element)
    run = paragraph.add_run(text)
    if template_run is not None:
        copy_run_format(template_run, run)
    if not keep_red:
        run.font.color.rgb = RGBColor(0, 0, 0)


def replace_cell_text(cell, text: str) -> None:
    paragraph = cell.paragraphs[0]
    replace_paragraph(paragraph, text)


def paragraph_index(document, paragraph) -> int:
    for index, candidate in enumerate(document.paragraphs):
        if candidate._element is paragraph._element:
            return index
    raise ValueError("段落不属于当前文档")


def find_paragraph(document, predicate, *, start: int = 0):
    for paragraph in document.paragraphs[start:]:
        if predicate(paragraph.text.strip()):
            return paragraph
    raise ValueError("模板中未找到所需段落")


def find_exact_paragraph(document, text: str, *, start: int = 0):
    return find_paragraph(document, lambda value: value == text, start=start)


def next_nonempty_paragraph(document, paragraph):
    start = paragraph_index(document, paragraph) + 1
    return find_paragraph(document, bool, start=start)


def startup_date_paragraphs(document) -> list:
    heading = find_exact_paragraph(document, "一、机组启停情况")
    end_heading = find_exact_paragraph(document, "二、分公司电量、电价完成情况")
    start = paragraph_index(document, heading) + 1
    end = paragraph_index(document, end_heading)
    date_pattern = re.compile(r"^(?:\d+月)?\d+日：$")
    return [
        paragraph
        for paragraph in document.paragraphs[start:end]
        if date_pattern.fullmatch(paragraph.text.strip())
    ]


def ensure_qingyuan_startup_paragraphs(document) -> None:
    dates = startup_date_paragraphs(document)
    if len(dates) < 2:
        raise ValueError("模板的机组启停情况中未找到两个日期段落")

    end_heading = find_exact_paragraph(document, "二、分公司电量、电价完成情况")
    for date_paragraph in reversed(dates[:2]):
        date_index = paragraph_index(document, date_paragraph)
        start = date_index + 1
        later_dates = [
            paragraph_index(document, candidate)
            for candidate in dates
            if paragraph_index(document, candidate) > date_index
        ]
        end = min(later_dates, default=paragraph_index(document, end_heading))
        block = document.paragraphs[start:end]
        if any(paragraph.text.strip().startswith("清远厂：") for paragraph in block):
            continue
        dongguan = next(
            (paragraph for paragraph in block if paragraph.text.strip().startswith("东莞厂：")),
            None,
        )
        if dongguan is None:
            raise ValueError("模板的机组启停情况中未找到东莞厂段落")
        new_element = deepcopy(dongguan._element)
        dongguan._element.addnext(new_element)
        replace_paragraph(Paragraph(new_element, dongguan._parent), "清远厂：")


def ensure_qingyuan_daily_clearing_row(table) -> None:
    def normalized_label(row) -> str:
        return re.sub(r"\s+", "", row.cells[0].text)

    if any(normalized_label(row) == "清远" for row in table.rows):
        return
    dongguan = next((row for row in table.rows if normalized_label(row) == "东莞"), None)
    if dongguan is None:
        raise ValueError("日清分表中未找到东莞行")
    new_element = deepcopy(dongguan._tr)
    dongguan._tr.addnext(new_element)
    new_row = next(row for row in table.rows if row._tr is new_element)
    if len(new_row.cells) != DAILY_CLEARING_COLUMNS:
        raise ValueError("日清分表列数与清远数据不匹配")
    for cell, value in zip(new_row.cells, QINGYUAN_DAILY_CLEARING_VALUES):
        replace_cell_text(cell, value)


def is_current_daily_clearing_table(table) -> bool:
    if len(table.columns) != DAILY_CLEARING_COLUMNS:
        return False
    header_text = "".join(cell.text.replace("\n", "") for row in table.rows[:2] for cell in row.cells)
    labels = {re.sub(r"\s+", "", row.cells[0].text) for row in table.rows}
    detail_labels = {re.sub(r"\s+", "", row.cells[1].text) for row in table.rows}
    return (
        all(header in header_text for header in DAILY_CLEARING_REQUIRED_HEADERS)
        and {"分公司火电", "汕头", "海门", "东莞", "清远", "分公司新能源", "海风"}.issubset(labels)
        and {"#1", "#2#3"}.issubset(detail_labels)
    )


def ensure_daily_clearing_table_structure(document, canonical_template_path: Path = LOCAL_TEMPLATE_PATH) -> None:
    if not document.tables:
        raise ValueError("模板中未找到日清分表")
    if is_current_daily_clearing_table(document.tables[0]):
        return

    canonical = Document(canonical_template_path)
    if not canonical.tables or not is_current_daily_clearing_table(canonical.tables[0]):
        raise ValueError("标准模板中的日清分表结构不完整")
    target = document.tables[0]._tbl
    target.getparent().replace(target, deepcopy(canonical.tables[0]._tbl))


def ensure_spot_deviation_analysis(document, canonical_template_path: Path = LOCAL_TEMPLATE_PATH) -> None:
    if any(paragraph.text.strip() == "3.现货偏差分析" for paragraph in document.paragraphs):
        return

    target_forecast = find_exact_paragraph(document, "五、短期市场预测")
    canonical = Document(canonical_template_path)
    source_heading = find_exact_paragraph(canonical, "3.现货偏差分析")
    source_forecast = find_exact_paragraph(canonical, "五、短期市场预测")
    source_start = paragraph_index(canonical, source_heading)
    source_end = paragraph_index(canonical, source_forecast)
    parent = target_forecast._element.getparent()
    insertion_index = parent.index(target_forecast._element)
    for paragraph in canonical.paragraphs[source_start:source_end]:
        parent.insert(insertion_index, deepcopy(paragraph._element))
        insertion_index += 1


def ensure_qingyuan_template_content(document, canonical_template_path: Path = LOCAL_TEMPLATE_PATH) -> None:
    ensure_qingyuan_startup_paragraphs(document)
    ensure_daily_clearing_table_structure(document, canonical_template_path)
    ensure_qingyuan_daily_clearing_row(document.tables[0])
    ensure_spot_deviation_analysis(document, canonical_template_path)


def replace_red_runs(paragraph, values: list[str]) -> None:
    value_iter = iter(values)
    for run in paragraph.runs:
        color = run.font.color.rgb if run.font.color and run.font.color.rgb else None
        if color and str(color).upper() == "FF0000":
            try:
                run.text = next(value_iter)
            except StopIteration:
                return


def clear_paragraph_red(paragraph) -> None:
    for run in paragraph.runs:
        color = run.font.color.rgb if run.font.color and run.font.color.rgb else None
        if color and str(color).upper() == "FF0000":
            run.font.color.rgb = RGBColor(0, 0, 0)


def clear_auto_section_red(document, *, spot_filled: bool) -> None:
    """已自动生成的小节标题取消标红。

    现货交易策略按接口数据自动生成，始终取消标红；现货偏差分析只有在 D-2 取数成功
    时才取消，取数失败保留红字，提示需要人工补数。
    """
    clear_paragraph_red(find_exact_paragraph(document, "\u4e09\u3001\u73b0\u8d27\u4ea4\u6613\u7b56\u7565"))
    if spot_filled:
        clear_paragraph_red(find_exact_paragraph(document, "3.\u73b0\u8d27\u504f\u5dee\u5206\u6790"))


def replace_leading_date(paragraph, date_text: str) -> None:
    text = paragraph.text
    updated = re.sub(r"^\d+月\d+日", date_label(date_text), text, count=1)
    if updated != text:
        replace_paragraph(paragraph, updated)


def get_company_energy(run_date: str) -> dict[str, Any]:
    rows = collect_detail_rows(run_date, load_config())
    totals: dict[str, float] = {}
    for row in rows:
        company = str(row.get("company") or "")
        label = None
        for raw, display in {
            "\u6c55\u5934\u7535\u5382": "\u6c55\u5934",
            "\u6d77\u95e8\u7535\u5382": "\u6d77\u95e8",
            "\u8c22\u5c97\u7535\u5382": "\u4e1c\u839e",
            "\u534e\u80fd\u5e7f\u4e1c\u6c55\u5934\u6d77\u4e0a\u98ce\u7535\u6709\u9650\u8d23\u4efb\u516c\u53f8": "\u6d77\u98ce",
            "\u534e\u80fd\uff08\u6c55\u5934\u91d1\u5e73\uff09\u65b0\u80fd\u6e90\u6709\u9650\u8d23\u4efb\u516c\u53f8": "\u9b80\u83b2",
            "\u534e\u80fd\uff08\u6f6e\u5dde\u6f6e\u5b89\uff09\u65b0\u80fd\u6e90\u6709\u9650\u8d23\u4efb\u516c\u53f8": "\u5f52\u6e56",
        }.items():
            if raw == company:
                label = display
                break
        if label is None:
            label = company
        totals[label] = totals.get(label, 0.0) + float(row.get("onlineEle") or 0.0)
    return {
        "total": round(sum(totals.values())),
        "companies": {name: round(totals.get(name, 0.0)) for name in ["\u6c55\u5934", "\u6d77\u95e8", "\u4e1c\u839e", "\u6d77\u98ce", "\u9b80\u83b2", "\u5f52\u6e56"]},
    }


def metric(subject: dict[str, Any], name: str) -> dict[str, Any]:
    return subject["comparison"][name]


def subject_by_code(payload: dict[str, Any], code: str) -> dict[str, Any]:
    for subject in payload["loadCompare"]["subjects"]:
        if str(subject.get("loadType")) == code:
            return subject
    raise KeyError(code)


def build_strategy_text(payload: dict[str, Any]) -> str:
    load = subject_by_code(payload, "1")
    a_power = subject_by_code(payload, "2")
    local = subject_by_code(payload, "3")
    price = next(row for row in payload["priceBoard"]["rows"] if row["dataType"] == "PRE")
    public_data = payload.get("operationPublicData") or {}
    capacity = (public_data.get("capacity") or {}).get("selected", {}).get("capacityWanKw")
    startup = (public_data.get("startupCount") or {}).get("selected", {}).get("maxStartupCount")
    startup_diff = (public_data.get("startupCount") or {}).get("difference")
    load_max = metric(load, "\u6700\u5927\u503c(MW)")
    load_min = metric(load, "\u6700\u5c0f\u503c(MW)")
    a_avg = metric(a_power, "\u5e73\u5747\u503c(MW)")
    local_avg = metric(local, "\u5e73\u5747\u503c(MW)")
    return (
        f"\u8fd0\u884c\u65e5\u7edf\u8c03\u8d1f\u8377\u9700\u6c42{load_trend_word(load_max['difference'])}\uff0c"
        f"\u5168\u5929\u6700\u5927{mw_to_10k_kw(load_max['selected'])}\u4e07\u5343\u74e6\uff0c"
        f"\u73af\u6bd4{load_change_text(load_max['difference'])}\uff0c"
        f"\u6700\u5c0f\u503c{mw_to_10k_kw(load_min['selected'])}\u4e07\u5343\u74e6\uff0c"
        f"\u73af\u6bd4{load_change_text(load_min['difference'])}\uff1b"
        f"A\u7c7b\u7535\u6e90\u51fa\u529b{change_word(a_avg['difference'])}\u3001"
        f"\u5730\u65b9\u7535\u6e90\u51fa\u529b{change_word(local_avg['difference'])}\u3002"
        f"\u68c0\u4fee\u5bb9\u91cf{fmt_number(capacity)}\u4e07\u5343\u74e6\u3002"
        f"\u5e02\u573a\u5f00\u673a\u53f0\u6570{fmt_number(startup)}\u53f0\uff0c"
        f"\u73af\u6bd4{startup_change_text(startup_diff)}\uff0c"
        f"\u5168\u7701\u65e5\u524d\u51fa\u6e05\u5747\u4ef7{fmt_number(price['selectedDailyAvgPrice'], 2)}\u5143/\u5146\u74e6\u65f6\uff0c"
        f"\u73af\u6bd4{delta_phrase(price['difference'], unit=UNIT_YUAN_PER_MWH, digits=2)}\u3002"
    )


def build_realtime_text(payload: dict[str, Any]) -> str:
    load = subject_by_code(payload, "1")
    b_space = subject_by_code(payload, "6")
    a_power = subject_by_code(payload, "2")
    local = subject_by_code(payload, "3")
    price = next(row for row in payload["priceBoard"]["rows"] if row["dataType"] == "RT")
    load_max = metric(load, "\u6700\u5927\u503c(MW)")
    load_min = metric(load, "\u6700\u5c0f\u503c(MW)")
    b_max = metric(b_space, "\u6700\u5927\u503c(MW)")
    b_min = metric(b_space, "\u6700\u5c0f\u503c(MW)")
    a_avg = metric(a_power, "\u5e73\u5747\u503c(MW)")
    local_avg = metric(local, "\u5e73\u5747\u503c(MW)")
    return (
        f"\u5b9e\u65f6\u7edf\u8c03\u8d1f\u8377\u9700\u6c42{load_trend_word(load_max['difference'])}\uff0c"
        f"\u5168\u5929\u6700\u5927\u503c{mw_to_10k_kw(load_max['selected'])}\u4e07\u5343\u74e6\uff0c"
        f"\u6700\u5c0f\u503c{mw_to_10k_kw(load_min['selected'])}\u4e07\u5343\u74e6\uff0c"
        f"A\u7c7b\u7535\u51fa\u529b{change_word(a_avg['difference'])}\u3001"
        f"\u5730\u65b9\u7535\u51fa\u529b{change_word(local_avg['difference'])}\uff0c"
        f"B\u7c7b\u7ade\u4ef7\u7a7a\u95f4\u5168\u5929\u6700\u5927\u503c{mw_to_10k_kw(b_max['selected'])}\u4e07\u5343\u74e6\uff0c"
        f"\u6700\u5c0f\u503c{mw_to_10k_kw(b_min['selected'])}\u4e07\u5343\u74e6\uff0c"
        f"\u5168\u5929\u5b9e\u65f6\u5747\u4ef7{fmt_number(price['selectedDailyAvgPrice'], 2)}\u5143/\u5146\u74e6\u65f6\u3002"
    )


def load_info_item(market_data: dict[str, Any], load_type: str, kind: str) -> dict[str, Any]:
    return market_data["loadInfo"]["byType"][load_type][kind]


def market_price_item(market_data: dict[str, Any], data_type: str) -> dict[str, Any]:
    return next(row for row in market_data["priceBoard"]["rows"] if row["dataType"] == data_type)


def build_day_ahead_market_text(payload: dict[str, Any]) -> str:
    market_data = payload["marketAnalysisData"]
    load = load_info_item(market_data, "1", "dayAhead")
    a_power = load_info_item(market_data, "2", "dayAhead")
    local = load_info_item(market_data, "3", "dayAhead")
    price = market_price_item(market_data, "PRE")
    public_data = market_data.get("operationPublicData") or {}
    capacity = (public_data.get("capacity") or {}).get("selected", {}).get("capacityWanKw")
    startup = (public_data.get("startupCount") or {}).get("selected", {}).get("maxStartupCount")
    startup_diff = (public_data.get("startupCount") or {}).get("difference")
    selected = load["selected"]
    return (
        f"\u8fd0\u884c\u65e5\u7edf\u8c03\u8d1f\u8377\u9700\u6c42{load_trend_word(load.get('maxDiff') or 0)}\uff0c"
        f"\u5168\u5929\u6700\u5927\u503c{fmt_mw_as_10k_kw(selected.get('max'))}\u4e07\u5343\u74e6\uff0c"
        f"\u73af\u6bd4{load_change_text(load.get('maxDiff') or 0)}\uff0c"
        f"\u6700\u5c0f\u503c{fmt_mw_as_10k_kw(selected.get('min'))}\u4e07\u5343\u74e6\uff0c"
        f"\u73af\u6bd4{load_change_text(load.get('minDiff') or 0)}\uff1b"
        f"A\u7c7b\u7535\u51fa\u529b{change_word(a_power.get('avgDiff') or 0)}\u3001"
        f"\u5730\u65b9\u7535\u51fa\u529b{change_word(local.get('avgDiff') or 0)}\uff0c"
        f"\u68c0\u4fee\u5bb9\u91cf{fmt_number(capacity)}\u4e07\u5343\u74e6\u3002"
        f"\u5e02\u573a\u5f00\u673a\u53f0\u6570{fmt_number(startup)}\u53f0\uff0c"
        f"\u73af\u6bd4{startup_change_text(startup_diff)}\uff0c"
        f"\u5168\u7701\u65e5\u524d\u51fa\u6e05\u5747\u4ef7{fmt_number(price['selectedDailyAvgPrice'], 2)}\u5143/\u5146\u74e6\u65f6\uff0c"
        f"\u73af\u6bd4{delta_phrase(price['difference'], unit=UNIT_YUAN_PER_MWH, digits=2)}\u3002"
    )


def build_actual_market_text(payload: dict[str, Any]) -> str:
    market_data = payload["marketAnalysisData"]
    load = load_info_item(market_data, "1", "actual")
    a_power = load_info_item(market_data, "2", "actual")
    local = load_info_item(market_data, "3", "actual")
    b_space = load_info_item(market_data, "6", "actual")
    price = market_price_item(market_data, "RT")
    selected = load["selected"]
    b_selected = b_space["selected"]
    return (
        f"\u5b9e\u65f6\u7edf\u8c03\u8d1f\u8377\u9700\u6c42{load_trend_word(load.get('maxDiff') or 0)}\uff0c"
        f"\u5168\u5929\u6700\u5927\u503c{fmt_mw_as_10k_kw(selected.get('max'))}\u4e07\u5343\u74e6\uff0c"
        f"\u6700\u5c0f\u503c{fmt_mw_as_10k_kw(selected.get('min'))}\u4e07\u5343\u74e6\uff0c"
        f"A\u7c7b\u7535\u51fa\u529b{change_word(a_power.get('avgDiff') or 0)}\u3001"
        f"\u5730\u65b9\u7535\u51fa\u529b{change_word(local.get('avgDiff') or 0)}\uff0c"
        f"B\u7c7b\u7ade\u4ef7\u7a7a\u95f4\u5168\u5929\u6700\u5927\u503c{fmt_mw_as_10k_kw(b_selected.get('max'))}\u4e07\u5343\u74e6\uff0c"
        f"\u6700\u5c0f\u503c{fmt_mw_as_10k_kw(b_selected.get('min'))}\u4e07\u5343\u74e6\uff0c"
        f"\u5168\u5929\u5b9e\u65f6\u5747\u4ef7{fmt_number(price['selectedDailyAvgPrice'], 2)}\u5143/\u5146\u74e6\u65f6\u3002"
    )


def build_energy_text(date_text: str, energy: dict[str, Any], kind: str) -> str:
    labels = [
        ("\u6c55\u5934", "\u6c55\u5934"),
        ("\u6d77\u95e8", "\u6d77\u95e8"),
        ("\u4e1c\u839e", "\u4e1c\u839e"),
        ("\u6d77\u98ce", "\u6d77\u4e0a\u98ce\u7535"),
        ("\u9b80\u83b2", "\u9b80\u83b2"),
        ("\u5f52\u6e56", "\u5f52\u6e56"),
    ]
    parts = [f"{label}{int(energy['companies'].get(key, 0))}\u4e07" for key, label in labels]
    if kind == "online":
        return (
            f"{date_label(date_text)}\u5206\u516c\u53f8\u4e0a\u7f51\u7535\u91cf{int(energy['total'])}\u4e07\u5343\u74e6\u65f6\uff0c"
            f"\u5176\u4e2d\uff0c{'、'.join(parts)}\u5343\u74e6\u65f6\uff1b"
            f"\u5206\u516c\u53f8\u65e5\u524d\u5747\u4ef7\u5f85\u8865\u5145\u5398/\u5343\u74e6\u65f6\uff0c"
            f"\u5176\u4e2d\u6c55\u5934\u5f85\u8865\u5145\u5398\u3001\u6d77\u95e8\u5f85\u8865\u5145\u5398\u3001\u4e1c\u839e\u5f85\u8865\u5145\u5398\u3001"
            f"\u6d77\u4e0a\u98ce\u7535\u5f85\u8865\u5145\u5398\u3001\u9b80\u83b2\u5f85\u8865\u5145\u5398\u3001\u5f52\u6e56\u5f85\u8865\u5145\u5398/\u5343\u74e6\u65f6\u3002"
        )
    return (
        f"{date_label(date_text)}\u5206\u516c\u53f8\u65e5\u524d\u51fa\u6e05\u7535\u91cf{int(energy['total'])}\u4e07\u5343\u74e6\u65f6\u3002"
        f"\u5176\u4e2d\uff0c{'、'.join(parts)}\u5343\u74e6\u65f6\uff1b"
        f"\u5206\u516c\u53f8\u65e5\u524d\u5747\u4ef7\u5f85\u8865\u5145\u5398/\u5343\u74e6\u65f6\uff0c"
        f"\u5176\u4e2d\u6c55\u5934\u5f85\u8865\u5145\u5398\u3001\u6d77\u95e8\u5f85\u8865\u5145\u5398\u3001\u4e1c\u839e\u5f85\u8865\u5145\u5398\u3001"
        f"\u6d77\u4e0a\u98ce\u7535\u5f85\u8865\u5145\u5398\u3001\u9b80\u83b2\u5f85\u8865\u5145\u5398\u3001\u5f52\u6e56\u5f85\u8865\u5145\u5398/\u5343\u74e6\u65f6\u3002"
    )


def ensure_qingyuan_online_energy_text(text: str) -> str:
    if "\u6e05\u8fdc" in text:
        return text
    return re.sub(
        r"(\u5176\u4e2d\uff0c.*?\u5343\u74e6\u65f6)(\uff1b\u5206\u516c\u53f8\u65e5\u524d\u5747\u4ef7)",
        lambda match: f"{match.group(1)}\u3001\u6e05\u8fdc0\u4e07\u5343\u74e6\u65f6{match.group(2)}",
        text,
        count=1,
    )


def build_day_ahead_clearing_text(date_text: str, summary: dict[str, Any]) -> str:
    companies = summary["companies"]
    shantou = companies["\u6c55\u5934"]
    haimen = companies["\u6d77\u95e8"]
    dongguan = companies["\u4e1c\u839e"]
    offshore = companies["\u6d77\u4e0a\u98ce\u7535"]
    tuolian = companies["\u9b80\u83b2"]
    guihu = companies["\u5f52\u6e56"]
    qingyuan = companies.get("\u6e05\u8fdc", {"energy": 0})
    return (
        f"{date_label(date_text)}\u5206\u516c\u53f8\u65e5\u524d\u51fa\u6e05\u7535\u91cf{fmt_number(summary['totalEnergy'])}\u4e07\u5343\u74e6\u65f6\u3002"
        f"\u5176\u4e2d\uff0c\u6c55\u5934{fmt_number(shantou['energy'])}\u4e07\u3001"
        f"\u6d77\u95e8{fmt_number(haimen['energy'])}\u4e07\u3001"
        f"\u4e1c\u839e{fmt_number(dongguan['energy'])}\u4e07\u3001"
        f"\u6d77\u4e0a\u98ce\u7535{fmt_number(offshore['energy'])}\u4e07\u3001"
        f"\u5149\u4f0f{fmt_number(tuolian['energy'] + guihu['energy'])}\u4e07\u5343\u74e6\u65f6\u3001"
        f"\u6e05\u8fdc\uff08\u8ba1\u5212\uff09{fmt_number(qingyuan['energy'])}\u4e07\u5343\u74e6\u65f6\uff1b"
        f"\u5206\u516c\u53f8\u65e5\u524d\u5747\u4ef7{fmt_number(summary['averagePrice'])}\u5398/\u5343\u74e6\u65f6\uff0c"
        f"\u5176\u4e2d\u6c55\u5934{fmt_number(shantou['price'])}\u5398\u3001"
        f"\u6d77\u95e8{fmt_number(haimen['price'])}\u5398\u3001"
        f"\u4e1c\u839e{fmt_number(dongguan['price'])}\u5398\u3001"
        f"\u6d77\u4e0a\u98ce\u7535{fmt_number(offshore['price'])}\u5398\u3001"
        f"\u9b80\u83b2{fmt_number(tuolian['price'])}\u5398\u3001"
        f"\u5f52\u6e56{fmt_number(guihu['price'])}\u5398/\u5343\u74e6\u65f6\u3002"
    )


def replace_online_price_text(paragraph, price_summary: dict[str, Any]) -> None:
    text = paragraph.text
    companies = price_summary["companies"]
    shantou = companies["\u6c55\u5934"]
    haimen = companies["\u6d77\u95e8"]
    dongguan = companies["\u4e1c\u839e"]
    offshore = companies["\u6d77\u4e0a\u98ce\u7535"]
    tuolian = companies["\u9b80\u83b2"]
    guihu = companies["\u5f52\u6e56"]
    price_text = (
        f"\u5206\u516c\u53f8\u65e5\u524d\u5747\u4ef7{fmt_number(price_summary['averagePrice'])}\u5398/\u5343\u74e6\u65f6\uff0c"
        f"\u5176\u4e2d\u6c55\u5934{fmt_number(shantou)}\u5398\u3001"
        f"\u6d77\u95e8{fmt_number(haimen)}\u5398\u3001"
        f"\u4e1c\u839e{fmt_number(dongguan)}\u5398\u3001"
        f"\u6d77\u4e0a\u98ce\u7535{fmt_number(offshore)}\u5398\u3001"
        f"\u9b80\u83b2{fmt_number(tuolian)}\u5398\u3001"
        f"\u5f52\u6e56{fmt_number(guihu)}\u5398/\u5343\u74e6\u65f6\u3002"
    )
    updated = re.sub(
        r"\u5206\u516c\u53f8\u65e5\u524d\u5747\u4ef7.*?\u5f52\u6e56.*?\u5398/\u5343\u74e6\u65f6\u3002",
        price_text,
        text,
        count=1,
    )
    if updated != text:
        replace_paragraph(paragraph, updated)


def fill_forecast_table(table, forecast: dict[str, Any]) -> None:
    rows = forecast["rows"]
    for idx, item in enumerate(rows, start=1):
        replace_cell_text(table.cell(0, idx), date_label(item["forecastDate"]))
        replace_cell_text(table.cell(1, idx), str(int(round((item["dailyTotal"] or 0) / 10))))
    for hour in range(24):
        for col_idx, item in enumerate(rows, start=1):
            value = item["hours"][hour]
            replace_cell_text(table.cell(hour + 2, col_idx), "" if value is None else str(int(round(value / 10))))


def generate(
    report_date: str,
    template_path: Path,
    output_dir: Path,
    *,
    online_workbook: Path | None = None,
    day_ahead_workbook: Path | None = None,
    daily_clearing_workbook: Path | None = None,
    prefer_previous_template: bool = True,
) -> tuple[Path, Path]:
    from fetch_daily_data import build_report_date_plan, run_daily_data

    plan = build_report_date_plan(report_date)
    actual_template_path = resolve_template_path(
        report_date,
        template_path,
        output_dir,
        prefer_previous_report=prefer_previous_template,
    )
    payload = run_daily_data(
        selected_date=plan["loadAndPriceSelectedDate"],
        compare_date=plan["loadAndPriceCompareDate"],
        subjects=LOAD_SUBJECT_CODES,
    )
    with clearing_input_cache():
        day_ahead_clearing = load_day_ahead_clearing_summary(plan["dayAheadDate"], day_ahead_workbook)
        online_prices = load_online_price_summary(plan["actualOnlineEnergyDate"], online_workbook)
        unit_status_error = None
        try:
            previous_unit_status = load_unit_status_texts(
                plan["actualOnlineEnergyDate"], online_workbook
            )
            current_unit_status = load_unit_status_texts(plan["dayAheadDate"], day_ahead_workbook)
        except Exception as exc:
            previous_unit_status, current_unit_status = {}, {}
            unit_status_error = str(exc)
            log(f"机组启停自动取数失败，已保留模板原文：{unit_status_error}")
    daily_clearing_error = None
    daily_filled_rows = 0
    spot_filled = False
    try:
        daily_clearing = load_daily_clearing_inputs(plan["dailyClearingDate"], daily_clearing_workbook)
    except Exception as exc:
        daily_clearing = None
        daily_clearing_error = str(exc)
        log(f"日清分表自动取数失败，已保留模板原值：{daily_clearing_error}")
    doc = Document(actual_template_path)
    ensure_qingyuan_template_content(doc)
    startup_dates = startup_date_paragraphs(doc)
    replace_paragraph(startup_dates[0], f"{date_label(plan['actualOnlineEnergyDate'])}：")
    replace_paragraph(startup_dates[1], f"{date_label(report_date)}：")
    startup_filled = 0
    if previous_unit_status or current_unit_status:
        startup_filled = fill_startup_status(doc, current_unit_status, previous_unit_status)

    recent_heading = find_exact_paragraph(doc, "1.近两日完成情况")
    online_paragraph = next_nonempty_paragraph(doc, recent_heading)
    day_ahead_paragraph = next_nonempty_paragraph(doc, online_paragraph)
    # P13 is left unchanged until the online energy table source is confirmed.
    replace_leading_date(online_paragraph, plan["actualOnlineEnergyDate"])
    replace_paragraph(online_paragraph, ensure_qingyuan_online_energy_text(online_paragraph.text))
    replace_online_price_text(online_paragraph, online_prices)
    clear_paragraph_red(online_paragraph)
    replace_paragraph(day_ahead_paragraph, build_day_ahead_clearing_text(plan["dayAheadDate"], day_ahead_clearing))

    strategy_heading = find_exact_paragraph(doc, "三、现货交易策略")
    replace_paragraph(next_nonempty_paragraph(doc, strategy_heading), build_strategy_text(payload))
    table_title = find_paragraph(doc, lambda text: text.startswith("表1"))
    replace_paragraph(table_title, f"表1  {date_label(plan['dailyClearingDate'])}分公司日清分情况")
    if daily_clearing is not None:
        daily_filled_rows = fill_daily_clearing_table(doc.tables[0], daily_clearing["rows"])
        if daily_clearing.get("spot"):
            spot_filled = fill_spot_deviation_analysis(
                doc, build_spot_deviation_texts(daily_clearing["spot"])
            )
    clear_auto_section_red(doc, spot_filled=spot_filled)
    day_ahead_heading = find_exact_paragraph(doc, "1.日前市场")
    replace_paragraph(next_nonempty_paragraph(doc, day_ahead_heading), build_day_ahead_market_text(payload))
    actual_heading = find_exact_paragraph(doc, "2.实时市场")
    replace_paragraph(next_nonempty_paragraph(doc, actual_heading), build_actual_market_text(payload))
    weather_paragraph = find_paragraph(
        doc,
        lambda text: text.startswith("未来三天") and "气温" in text,
    )
    weather_error = None
    try:
        replace_weather_prefix(weather_paragraph, report_date)
    except Exception as exc:
        weather_error = str(exc)
        clear_paragraph_red(weather_paragraph)
        log(f"天气自动更新失败，已保留模板原文：{weather_error}")

    if len(doc.tables) >= 2:
        fill_forecast_table(doc.tables[1], payload["rollingForecast"])

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / report_filename(report_date)
    try:
        doc.save(out_path)
    except PermissionError:
        dt = datetime.strptime(report_date, "%Y-%m-%d")
        out_path = output_dir / (
            f"\u6bcf\u65e5\u751f\u4ea7\u7ecf\u8425\u60c5\u51b5\u6c47\u62a5\uff08{dt.month}\u6708{dt.day}\u65e5\uff09_"
            f"{datetime.now().strftime('%H%M%S')}.docx"
        )
        doc.save(out_path)

    audit_path = output_dir / f"red_report_audit_{report_date}.json"
    audit = {
        "reportDate": report_date,
        "datePlan": plan,
        "template": str(actual_template_path),
        "templateFallback": str(template_path) if actual_template_path != template_path else None,
        "output": str(out_path),
        "onlineWorkbook": str(online_workbook) if online_workbook else None,
        "onlinePriceWorkbook": online_prices["workbook"],
        "dayAheadClearingWorkbook": day_ahead_clearing["workbook"],
        "qingyuanDayAheadSource": day_ahead_clearing["qingyuanSource"],
        "qingyuanDayAheadEnergy": day_ahead_clearing["companies"]["\u6e05\u8fdc"]["energy"],
        "dayAheadBaseTotalEnergy": day_ahead_clearing["baseTotalEnergy"],
        "dayAheadTotalEnergy": day_ahead_clearing["totalEnergy"],
        "dailyClearingWorkbook": str(daily_clearing_workbook) if daily_clearing_workbook else None,
        "dailyClearingSource": daily_clearing["workbook"] if daily_clearing else None,
        "dailyClearingRows": daily_filled_rows,
        "startupStatusParagraphs": startup_filled,
        "unitStatusSource": {
            "previous": plan["actualOnlineEnergyDate"],
            "current": plan["dayAheadDate"],
        },
        "unitStatusError": unit_status_error,
        "spotDeviationFilled": spot_filled,
        "spotDeviationSkipped": (daily_clearing or {}).get("spotSkipReason"),
        "dailyClearingError": daily_clearing_error,
        "weatherError": weather_error,
        "knownGaps": [
            "P13 上网电量来源尚未确认，电量暂保留模板原文；日前均价来自报告日出清情况 Excel 的快报（日前）工作表。",
            "P14 日前出清电量和日前价格来自报告日出清情况 Excel；火电取快报（日前）F/G，新能源按表头取 G/H，清远计划电量取基础信息!AA64+AC64 后除以10，并计入分公司总电量。",
            "P29 未来三天最高负荷来源尚未确认，暂不替换。",
            "日清分表取 D-2 日出清情况工作簿「快报」表；分公司现货偏差收益按各厂合计（汕头/东莞取现货偏差合计边际贡献，海门取三项偏差边际贡献之和）。",
            "现货偏差分析取 D-2 日出清情况工作簿「分公司」表，节点电价按各厂「实时最低价/实时最高价」跨厂取极值。",
        ],
    }
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path, audit_path


def main() -> int:
    parser = argparse.ArgumentParser(description="按红字模板生成试跑版报告")
    parser.add_argument("report_date", nargs="?", help="报告日期 YYYY-MM-DD")
    parser.add_argument("--template", default=str(TEMPLATE_PATH), help="红字模板路径")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="输出目录")
    parser.add_argument("--online-workbook", default="", help="前一日报表 Excel，预留给上网电量段落")
    parser.add_argument("--day-ahead-workbook", default="", help="报告日报表 Excel，用于日前出清电量和日前价格")
    parser.add_argument("--daily-clearing-workbook", default="", help="前两日报表 Excel，预留给日清分表")
    args = parser.parse_args()
    try:
        if not args.report_date:
            raise ValueError("请提供报告日期，例如：python generate_red_marked_report.py 2026-05-13")
        report_date = validate_date(args.report_date)
        template_was_provided = any(
            item == "--template" or item.startswith("--template=")
            for item in sys.argv[1:]
        )
        out_path, audit_path = generate(
            report_date,
            Path(args.template),
            Path(args.output_dir),
            online_workbook=Path(args.online_workbook) if args.online_workbook else None,
            day_ahead_workbook=Path(args.day_ahead_workbook) if args.day_ahead_workbook else None,
            daily_clearing_workbook=Path(args.daily_clearing_workbook) if args.daily_clearing_workbook else None,
            prefer_previous_template=not template_was_provided,
        )
        log(f"已生成: {out_path}")
        log(f"审计说明: {audit_path}")
        return 0
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:
        print(f"\n失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
