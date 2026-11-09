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
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph

SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in {"scripts", "\u811a\u672c"} else SCRIPT_DIR
if str(APP_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(APP_ROOT.parent))
from toolbox_shared.dates import normalize_date_input
from toolbox_shared.storage import data_path
from toolbox_shared.clearing_layout import brief_row_index, qingyuan_plan_values

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


def replace_leading_month_day(text: str, label: str) -> str:
    """把段首的「X月X日」换个日期，其余内容不动（不含年份）。"""
    return LEADING_MONTH_DAY_PATTERN.sub(label, text, count=1)


def day_number(date_text: str) -> str:
    return str(datetime.strptime(date_text, "%Y-%m-%d").day)


def report_filename(date_text: str) -> str:
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    return f"\u6bcf\u65e5\u751f\u4ea7\u7ecf\u8425\u60c5\u51b5\u6c47\u62a5\uff08{dt.month}\u6708{dt.day}\u65e5\uff09.docx"


def previous_report_path(report_date: str, output_dir: Path) -> Path:
    previous_day = datetime.strptime(report_date, "%Y-%m-%d").date() - timedelta(days=1)
    return output_dir / report_filename(previous_day.strftime("%Y-%m-%d"))


WORKBOOK_DATE_TARGETS = (
    ("online_workbook", "前一日工作簿（上网电量、前一日机组启停）", "actualOnlineEnergyDate"),
    ("day_ahead_workbook", "报告日工作簿（日前电量、报告日机组启停）", "dayAheadDate"),
    ("daily_clearing_workbook", "前两日工作簿（日清分表、现货偏差分析）", "dailyClearingDate"),
)

# 这些小节里只有首段由接口生成，其余段落或有其他数据源、或沿用上一份报告。
MANUAL_SECTION_SPECS = (
    ("三、现货交易策略", "首段由接口数据生成，三厂策略取自 D 日「快报（日前）」A39；其余段落沿用上一份报告，需人工核对。"),
    ("五、短期市场预测", "首段天气由接口更新，段内「预计统调最高负荷…」为手写；其后负荷与均价区间沿用上一份报告，需人工核对。"),
)

SECTION_STOP_PREFIXES = ("一、", "二、", "三、", "四、", "五、", "表1", "表2", "下表为")

PLANT_STRATEGY_LABELS = ("汕头厂", "海门厂", "东莞厂")
EAST_GUANGDONG_PREFIX = "粤东区域"
PLANT_STRATEGY_PATTERN = re.compile(
    r"^(?:\d+[.、]?)?\s*(?P<plant>" + "|".join(PLANT_STRATEGY_LABELS) + r")\s*[：:]\s*(?P<body>.*)$"
)
NEGATIVE_TEXT_PATTERN = re.compile(r"^-\d+(?:\.\d+)?$")
NEGATIVE_IN_TEXT_PATTERN = re.compile(r"(?<![0-9A-Za-z])-\d+(?:\.\d+)?")
LEADING_MONTH_DAY_PATTERN = re.compile(r"^\s*\d{1,2}\u6708\d{1,2}\u65e5")


def section_paragraphs(document, title: str) -> list:
    """按小节标题收集其下正文段落，遇到下一个标题停止。"""
    try:
        heading = find_exact_paragraph(document, title)
    except ValueError:
        return []
    paragraphs: list = []
    node = heading._p.getnext()
    while node is not None:
        if node.tag == qn("w:p"):
            paragraph = Paragraph(node, heading._parent)
            text = paragraph.text.strip()
            if text.startswith(SECTION_STOP_PREFIXES):
                break
            if text:
                paragraphs.append(paragraph)
        node = node.getnext()
    return paragraphs


def extract_plant_strategies(text: str | None) -> dict[str, str]:
    """把 A39 日前简析里的三厂策略拆出来，去掉行首序号与厂名前缀。

    段落以厂名开头，标题行（日前简析、1.运行日统调负荷…）与粤东线路信息不算。
    """
    strategies: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = PLANT_STRATEGY_PATTERN.match(line)
        if match:
            if current and buffer:
                strategies[current] = "".join(buffer).strip()
            current = match.group("plant")
            buffer = [match.group("body").strip()]
            continue
        if current:
            buffer.append(line)
    if current and buffer:
        strategies[current] = "".join(buffer).strip()
    return {plant: body for plant, body in strategies.items() if body}


def resolve_day_ahead_workbook(workbook_path: Path | None,
                               date_text: str | None) -> Path | None:
    """显式路径优先；没有传路径时按报告日到下载目录自动定位 D 日工作簿。"""
    if workbook_path:
        return Path(workbook_path)
    if date_text:
        try:
            return find_clearing_workbook(date_text)
        except Exception as exc:
            log(f"按日期未找到 D 日出清情况工作簿（{date_text}）：{exc}")
    return None


def load_plant_strategies(workbook_path: Path | None, *,
                          date_text: str | None = None) -> dict[str, str]:
    """读取 D 日「快报（日前）」的日前简析（A39），解析三厂策略。

    未显式传工作簿时按报告日自动定位（与其他取数函数一致）；以前这里直接
    return {}，导致命令行不传 --day-ahead-workbook 时明明 A39 有内容却报「找不到」。
    """
    source = resolve_day_ahead_workbook(workbook_path, date_text)
    if source is None:
        return {}
    try:
        workbook = load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        log(f"三厂策略：打不开工作簿 {source.name}：{exc}")
        return {}
    text: str | None = None
    try:
        sheet_name = next(
            (name for name in workbook.sheetnames if name.replace(" ", "") == "快报(日前)"),
            None,
        )
        if sheet_name is None:
            log(f"三厂策略：{source.name} 中没有「快报（日前）」工作表，保留模板原文")
            return {}
        sheet = workbook[sheet_name]
        for row in sheet.iter_rows(min_row=39, max_row=39, min_col=1, max_col=1, values_only=True):
            value = row[0] if row else None
            text = str(value) if isinstance(value, str) else None
            break
        if not text or "日前简析" not in text:
            # 行号漂移时回退为按内容查找。
            text = None
            for row in sheet.iter_rows(min_row=1, max_row=80, min_col=1, max_col=1, values_only=True):
                value = row[0] if row else None
                if isinstance(value, str) and "日前简析" in value:
                    text = value
                    break
        if not text:
            log(f"三厂策略：{source.name}「快报（日前）」中未找到日前简析（A39），保留模板原文")
    finally:
        workbook.close()
    return extract_plant_strategies(text)


def fill_plant_strategies(document, strategies: dict[str, str]) -> list[str]:
    """用 A39 的三厂策略替换报告对应段落，返回实际替换的厂名。"""
    if not strategies:
        return []
    paragraphs = section_paragraphs(document, "三、现货交易策略")
    filled: list[str] = []
    for plant in PLANT_STRATEGY_LABELS:
        body = strategies.get(plant)
        if not body:
            continue
        target = next(
            (item for item in paragraphs if item.text.strip().startswith(f"{plant}：")), None
        )
        if target is None:
            continue
        replace_paragraph(target, f"{plant}：{body}")
        filled.append(plant)
    return filled


def remove_east_guangdong_section(document) -> bool:
    """删除「粤东区域：500kV线路…」段落——不是每日固定内容。"""
    for paragraph in section_paragraphs(document, "三、现货交易策略"):
        if paragraph.text.strip().startswith(EAST_GUANGDONG_PREFIX):
            element = paragraph._p
            element.getparent().remove(element)
            return True
    return False


TABLE_PAGE_BREAK_ANCHORS = ("四、日清分情况", "下表为未来五天海风功率预测：")


def apply_table_page_breaks(document) -> list[str]:
    """让两个表格所在小节从新页开始，保证「标题＋表格」不被分页拆开。"""
    applied: list[str] = []
    for text in TABLE_PAGE_BREAK_ANCHORS:
        try:
            paragraph = find_exact_paragraph(document, text)
        except ValueError:
            continue
        paragraph.paragraph_format.page_break_before = True
        applied.append(text)
    return applied


def collect_manual_sections(document, *, filled_plants: set[str] | None = None) -> list[dict[str, str]]:
    """按小节结构列出仍需人工维护的段落，避免上一份报告的文字被当成当日内容。"""
    skip_prefixes = {f"{plant}：" for plant in (filled_plants or set())}
    sections: list[dict[str, str]] = []
    for title, note in MANUAL_SECTION_SPECS:
        paragraphs = section_paragraphs(document, title)
        for paragraph in paragraphs[1:]:  # 首段由接口生成
            text = paragraph.text.strip()
            if any(text.startswith(prefix) for prefix in skip_prefixes):
                continue
            sections.append({"section": title, "text": text, "note": note})
    return sections


def read_workbook_run_date(workbook_path: Path | None) -> str | None:
    """读取「基础信息」A2 的运行日；读不到时返回 None。"""
    if not workbook_path:
        return None
    try:
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    except Exception:
        return None
    value: Any = None
    try:
        sheet_name = next(
            (name for name in workbook.sheetnames if name.strip() == "\u57fa\u7840\u4fe1\u606f"),
            None,
        )
        if sheet_name is None:
            return None
        for row in workbook[sheet_name].iter_rows(
            min_row=2, max_row=2, min_col=1, max_col=1, values_only=True
        ):
            value = row[0] if row else None
            break
    finally:
        workbook.close()
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None
    return None


def check_workbook_dates(plan: dict[str, str], **workbooks: Path | None) -> list[dict[str, str]]:
    """核对三份工作簿的运行日与日期口径；不符只报警，不中断生成。"""
    mismatches: list[dict[str, str]] = []
    for key, label, plan_key in WORKBOOK_DATE_TARGETS:
        path = workbooks.get(key)
        if not path:
            continue
        expected = plan[plan_key]
        actual = read_workbook_run_date(Path(path))
        if actual == expected:
            continue
        mismatches.append(
            {
                "workbook": key,
                "label": label,
                "file": str(path),
                "expectedDate": expected,
                "actualDate": actual or "\u8bfb\u4e0d\u5230\u8fd0\u884c\u65e5",
            }
        )
    return mismatches


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
    date_pattern = re.compile(r"(?<!\d)(\d{4})\.(\d{1,2})\.(\d{1,2})(?!\d)")
    candidates = [
        path
        for path in workbook_dir.glob("*.xlsx")
        if not path.name.startswith("~$")
        and any(tuple(map(int, match.groups())) == (dt.year, dt.month, dt.day)
                for match in date_pattern.finditer(path.stem))
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
        values, label_cells = {}, []
        for row_number, row in enumerate(workbook[sheet_name].iter_rows(min_col=1, max_col=8, values_only=True), 1):
            label_cells.append(row[:2])
            for column in (6, 7, 8):
                if row[column - 1] is not None:
                    values[(row_number, column)] = row[column - 1]
        basic = None
        unit_rows: list[tuple] = []
        if "基础信息" in workbook.sheetnames:
            basic = list(workbook["基础信息"].iter_rows(min_col=1, max_col=30, values_only=True))
            unit_rows = [row[:7] for row in basic]
        result = (sheet_name, values, brief_row_index(label_cells), basic, unit_rows)
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
    qingyuan_raw_values = qingyuan_plan_values(basic) if basic is not None else {}
    if basic is not None:
        missing_qingyuan_cells = [cell for cell, number in qingyuan_raw_values.items() if number is None]
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
        "company": _clearing_label_row(label_rows, ("分公司火电",), sheet_name),
        "\u6c55\u5934": _clearing_label_row(label_rows, ("汕头",), sheet_name),
        "\u6d77\u95e8": _clearing_label_row(label_rows, ("海门",), sheet_name),
        "\u4e1c\u839e": _clearing_label_row(label_rows, ("东莞",), sheet_name),
        "\u6d77\u4e0a\u98ce\u7535": _clearing_label_row(label_rows, ("\u6c55\u5934\u6d77\u98ce", "\u6d77\u4e0a\u98ce\u7535"), sheet_name),
        "\u9b80\u83b2": _clearing_label_row(label_rows, ("\u9b80\u83b2\u5149\u4f0f", "\u9b80\u83b2"), sheet_name),
        "\u5f52\u6e56": _clearing_label_row(label_rows, ("\u5f52\u6e56\u5149\u4f0f", "\u5f52\u6e56"), sheet_name),
    }
    base_total_energy = parse_float(value(rows["company"], 6))
    # 分公司火电合计的 F 列只含火电三厂，新能源独立区块的 G 列为电量，
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
        "averagePrice": parse_float(value(_clearing_label_row(label_rows, ("分公司火电",), sheet_name), 7)),
        "companies": {
            "\u6c55\u5934": parse_float(value(_clearing_label_row(label_rows, ("汕头",), sheet_name), 7)),
            "\u6d77\u95e8": parse_float(value(_clearing_label_row(label_rows, ("海门",), sheet_name), 7)),
            "\u4e1c\u839e": parse_float(value(_clearing_label_row(label_rows, ("东莞",), sheet_name), 7)),
            "\u6d77\u4e0a\u98ce\u7535": parse_float(value(renewable_rows["\u6d77\u4e0a\u98ce\u7535"], 8)),
            "\u9b80\u83b2": parse_float(value(renewable_rows["\u9b80\u83b2"], 8)),
            "\u5f52\u6e56": parse_float(value(renewable_rows["\u5f52\u6e56"], 8)),
        },
    }


def load_online_energy_summary(date_text: str, workbook_path: Path | None = None) -> dict[str, Any]:
    """D-1 上网电量：读「快报」表。火电三厂与清远在 J 列，新能源区在 K 列。

    清远取“分公司合计 － 火电三厂”，避免命中清远的套机组行。
    """
    if workbook_path is None:
        workbook_path = find_clearing_workbook(date_text)
    numbers: dict[tuple[int, int], float] = {}
    label_cells: list[tuple] = []
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        sheet_name = next((name for name in workbook.sheetnames if name.strip() == "快报"), None)
        if sheet_name is None:
            raise KeyError(f"{workbook_path} 中未找到快报工作表")
        for row_number, row in enumerate(workbook[sheet_name].iter_rows(min_col=1, max_col=11, values_only=True), 1):
            label_cells.append(row[:2])
            for column in (10, 11):
                cell = row[column - 1]
                if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    numbers[(row_number, column)] = float(cell)
    finally:
        workbook.close()

    labels = brief_row_index(label_cells)
    def value(row: int, column: int) -> float:
        return numbers.get((row, column), 0.0)

    def label_row(wanted: tuple[str, ...]) -> int:
        for name in wanted:
            if name in labels:
                return labels[name]
        raise KeyError(f"{sheet_name} 中未找到行：{'/'.join(wanted)}")

    shantou = value(label_row(("汕头",)), 10)
    haimen = value(label_row(("海门",)), 10)
    dongguan = value(label_row(("东莞",)), 10)
    total_fire = value(label_row(("分公司火电",)), 10)
    offshore = value(label_row(("\u6c55\u5934\u6d77\u98ce", "\u6d77\u4e0a\u98ce\u7535")), 11)
    tuolian = value(label_row(("\u9b80\u83b2\u5149\u4f0f", "\u9b80\u83b2")), 11)
    guihu = value(label_row(("\u5f52\u6e56\u5149\u4f0f", "\u5f52\u6e56")), 11)
    qingyuan = total_fire - (shantou + haimen + dongguan)
    renewable = offshore + tuolian + guihu
    return {
        "workbook": str(workbook_path),
        "sheet": sheet_name,
        "companies": {
            "\u6c55\u5934": shantou,
            "\u6d77\u95e8": haimen,
            "\u4e1c\u839e": dongguan,
            "\u6d77\u4e0a\u98ce\u7535": offshore,
            "\u5149\u4f0f": tuolian + guihu,
            "\u6e05\u8fdc": qingyuan,
        },
        "renewableEnergy": renewable,
        "total": total_fire + renewable,
    }


def build_online_energy_text(date_text: str, energy: dict[str, Any], price_summary: dict[str, Any]) -> str:
    """上网电量句：分项取「快报」J/K 列，日前均价取「快报（日前）」。"""
    c = energy["companies"]
    shantou, haimen, dongguan = c["汕头"], c["海门"], c["东莞"]
    offshore, solar, qingyuan = c["海上风电"], c["光伏"], c["清远"]
    return (
        f"{date_label(date_text)}分公司上网电量{fmt_number(energy['total'])}万千瓦时，其中，"
        f"汕头{fmt_number(shantou)}万、"
        f"海门{fmt_number(haimen)}万、"
        f"东莞{fmt_number(dongguan)}万、"
        f"海上风电{fmt_number(offshore)}万、"
        f"光伏{fmt_number(solar)}万千瓦时、"
        f"清远{fmt_number(qingyuan)}万千瓦时；"
        + build_online_price_text(price_summary)
    )


def build_online_price_text(price_summary: dict[str, Any]) -> str:
    """D-1 日前均价独立于上网电量是否已填报。"""
    p = price_summary["companies"]
    return (
        f"分公司日前均价{fmt_number(price_summary['averagePrice'])}厘/千瓦时，其中"
        f"汕头{fmt_number(p['汕头'])}厘、"
        f"海门{fmt_number(p['海门'])}厘、"
        f"东莞{fmt_number(p['东莞'])}厘、"
        f"海上风电{fmt_number(p['海上风电'])}厘、"
        f"鮀莲{fmt_number(p['鮀莲'])}厘、"
        f"归湖{fmt_number(p['归湖'])}厘/千瓦时。"
    )


def fill_online_completion(paragraph, date_text: str, energy: dict[str, Any] | None,
                           price_summary: dict[str, Any]) -> None:
    """Keep the template energy text for manual entry and update prices separately."""
    if energy and energy["total"] > 0:
        replace_paragraph(paragraph, build_online_energy_text(date_text, energy, price_summary))
        return
    boundary = paragraph.text.find("分公司日前均价")
    if boundary < 0:
        raise ValueError("近两日完成情况段落缺少「分公司日前均价」，无法保留模板电量并单独更新均价")
    remaining = boundary
    template_run = paragraph.runs[0] if paragraph.runs else None
    for run in list(paragraph.runs):
        length = len(run.text)
        if remaining <= 0:
            paragraph._element.remove(run._element)
        else:
            if length > remaining:
                run.text = run.text[:remaining]
            remaining -= length
    prices = paragraph.add_run(build_online_price_text(price_summary))
    if template_run is not None:
        copy_run_format(template_run, prices)
    prices.font.color.rgb = RGBColor(0, 0, 0)


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
UNIT_STATUS_WORDS = {
    "运行": "运行",
    "备用": "备用",
    "停备": "备用",
    "停机": "备用",
    "应急备用": "应急备用",  # 保留原词，但排序按「备用」处理
    "供热": "供热",
    "检修": "检修",
}
DONGGUAN_STATUS_WORDS = {"运行": "调峰", "备用": "备用", "停备": "备用", "供热": "供热", "检修": "检修"}
STATUS_RANK_ALIASES = {"停备": "备用", "停机": "备用", "应急备用": "备用"}
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
    normalized = STATUS_RANK_ALIASES.get(status, status)
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


def _suite_order(key: str) -> int:
    """套号排序键：#1,2 → 1；无法解析时排到最后。"""
    head = key.split(",")[0].strip()
    return int(head) if head.isdigit() else 999


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

    if plant == "东莞":
        # 东莞按套号顺序逐套列出（一套→四套），不按状态聚合，
        # 否则会出现「二套调峰，一、四套备用，三套检修」这种跳序写法。
        clauses = [
            f"{_unit_label(plant, key)}套机组{DONGGUAN_STATUS_WORDS.get(status, status)}"
            for key, status in sorted(units, key=lambda item: _suite_order(item[0]))
        ]
        return "，".join(clauses) + "。"

    # 按「输出用词」聚合：备用/停备/停机都写「备用」，不能因为输入不同就拆成两句。
    grouped: dict[str, list[str]] = {}
    ranks: dict[str, int] = {}
    for key, status in units:
        word = UNIT_STATUS_WORDS.get(status, status)
        grouped.setdefault(word, []).append(_unit_label(plant, key))
        ranks[word] = min(ranks.get(word, _status_rank(status)), _status_rank(status))

    ordered = sorted(grouped.items(), key=lambda item: ranks[item[0]])
    clauses = [f"{'、'.join(labels)}机组{word}" for word, labels in ordered]
    return "；".join(clauses) + "。"


def load_unit_status_texts(date_text: str,
                           workbook_path: Path | None = None) -> dict[str, str]:
    if workbook_path is None:
        workbook_path = find_clearing_workbook(date_text)
    rest = _read_clearing_values(workbook_path)
    unit_rows = rest[-1]
    parsed = _parse_unit_status(unit_rows)
    texts: dict[str, str] = {}
    for plant, units in parsed.items():
        # 各厂（含东莞）统一以基础信息 C 列的机组状态为准；
        # 东莞「约束情况」里的「运行日运行方式」常与 C 列不一致（如写「一、二、四套调峰」），不采用。
        text = build_unit_status_text(plant, units)
        if text:
            texts[plant] = text
    return texts


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
                replace_cell_text(cells[column - 1], text, highlight_negative=True)
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
        # 现货偏差分析两段正文不标红负数（只有日清分表与市场分析段标红）。
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


def replace_cell_text(cell, text: str, *, highlight_negative: bool = False) -> None:
    paragraph = cell.paragraphs[0]
    replace_paragraph(paragraph, text)
    if highlight_negative and is_negative_text(text):
        for run in paragraph.runs:
            run.font.color.rgb = RGBColor(0xFF, 0, 0)


def is_negative_text(text: str) -> bool:
    """判断表格文本是否为负值；「-0」「/」不算。"""
    value = (text or "").strip()
    if not NEGATIVE_TEXT_PATTERN.match(value):
        return False
    return float(value) < 0


def highlight_negative_numbers(paragraph) -> int:
    """把段落正文里的负数（-50、-111.5）标红，其余文字保持黑色。

    只认“非数字、非字母后面紧跟负号”的写法，因此「0-1点」这类区间与日期不会被误标。
    """
    text = paragraph.text
    matches = list(NEGATIVE_IN_TEXT_PATTERN.finditer(text))
    if not matches:
        return 0
    template_run = paragraph.runs[0] if paragraph.runs else None
    for index in range(len(paragraph.runs) - 1, -1, -1):
        paragraph._element.remove(paragraph.runs[index]._element)

    def add_segment(segment: str, red: bool) -> None:
        if not segment:
            return
        run = paragraph.add_run(segment)
        if template_run is not None:
            copy_run_format(template_run, run)
        run.font.color.rgb = RGBColor(0xFF, 0, 0) if red else RGBColor(0, 0, 0)

    position = 0
    for match in matches:
        add_segment(text[position:match.start()], red=False)
        add_segment(match.group(0), red=True)
        position = match.end()
    add_segment(text[position:], red=False)
    return len(matches)


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


def fill_forecast_table(table, forecast: dict[str, Any]) -> None:
    rows = forecast["rows"]
    for idx, item in enumerate(rows, start=1):
        replace_cell_text(table.cell(0, idx), date_label(item["forecastDate"]))
        replace_cell_text(table.cell(1, idx), str(int(round((item["dailyTotal"] or 0) / 10))))
    for hour in range(24):
        for col_idx, item in enumerate(rows, start=1):
            value = item["hours"][hour]
            replace_cell_text(table.cell(hour + 2, col_idx), "" if value is None else str(int(round(value / 10))))


FORECAST_TABLE_FONT_SIZE = 10  # pt
FORECAST_TABLE_ROW_HEIGHT = Cm(0.5)
FORECAST_TABLE_HEADING_ALLOWANCE_CM = 2.0  # 标题段 + 页脚/安全余量


def _disable_paragraph_grid(paragraph, font_size_pt: float) -> None:
    """关掉「对齐到网格」并把段落标记字号一起缩小。

    模板的节里有 <w:docGrid w:type="lines" w:linePitch="312"/>，Word 会把表格里
    每一行文字吸附到文档网格上，10pt 字实际占两格（约 31pt），26 行会撑到两页。
    只改字号和行高没有用，必须显式写 <w:snapToGrid w:val="0"/>；段落标记的
    rPr/sz 也要同步，否则 Word 仍按 12pt 段落标记计算行高。
    """
    properties = paragraph._p.get_or_add_pPr()
    snap = properties.find(qn("w:snapToGrid"))
    if snap is None:
        snap = OxmlElement("w:snapToGrid")
        properties.insert_element_before(
            snap, "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
            "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
            "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle",
            "w:rPr", "w:sectPr", "w:pPrChange",
        )
    snap.set(qn("w:val"), "0")
    mark = properties.find(qn("w:rPr"))
    if mark is None:
        mark = OxmlElement("w:rPr")
        properties.insert_element_before(mark, "w:sectPr", "w:pPrChange")
    size = mark.get_or_add_sz()
    size.val = Pt(font_size_pt)


def _compact_table_cell_margins(table) -> None:
    """表格单元格上下边距设为 0，避免默认样式把每行顶到 1cm 以上。"""
    properties = table._tbl.tblPr
    margins = properties.find(qn("w:tblCellMar"))
    if margins is None:
        margins = OxmlElement("w:tblCellMar")
        properties.insert_element_before(
            margins, "w:tblLook", "w:tblCaption", "w:tblDescription", "w:tblPrChange"
        )
    for side, width in (("top", 0), ("bottom", 0), ("left", 60), ("right", 60)):
        element = margins.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            margins.append(element)
        element.set(qn("w:w"), str(width))
        element.set(qn("w:type"), "dxa")


def compact_forecast_table(table, *, available_height_cm: float | None = None) -> int:
    """收紧海风预测表，使 26 行连同标题稳定落在一页 A4 内。

    available_height_cm 传入版心高度时会按行数反算行高上限，防止模板换成
    更小的纸张或更大边距后表格重新跨页。
    """
    count = len(table.rows)
    row_height = FORECAST_TABLE_ROW_HEIGHT
    if available_height_cm is not None and count:
        budget_cm = (available_height_cm - FORECAST_TABLE_HEADING_ALLOWANCE_CM) / count
        if budget_cm > 0:
            row_height = min(row_height, Cm(max(0.32, budget_cm)))
    _compact_table_cell_margins(table)
    for row in table.rows:
        row.height = row_height
        row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
        row_properties = row._tr.get_or_add_trPr()
        if row_properties.find(qn("w:cantSplit")) is None:
            row_properties.insert_element_before(
                OxmlElement("w:cantSplit"), "w:trHeight", "w:tblHeader",
                "w:tblCellSpacing", "w:jc", "w:hidden", "w:ins", "w:del", "w:trPrChange",
            )
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                paragraph_format = paragraph.paragraph_format
                paragraph_format.space_before = Pt(0)
                paragraph_format.space_after = Pt(0)
                paragraph_format.line_spacing = 1.0
                _disable_paragraph_grid(paragraph, FORECAST_TABLE_FONT_SIZE)
                for run in paragraph.runs:
                    run.font.size = Pt(FORECAST_TABLE_FONT_SIZE)
    return count


def generate(
    report_date: str,
    template_path: Path,
    output_dir: Path,
    *,
    online_workbook: Path | None = None,
    day_ahead_workbook: Path | None = None,
    daily_clearing_workbook: Path | None = None,
    prefer_previous_template: bool = True,
    save_data_summary: bool = False,
) -> tuple[Path, Path]:
    from fetch_daily_data import build_report_date_plan, run_daily_data, parse_subjects, save_output, print_result
    from toolbox_shared.morning_prices import apply_brief_prices

    plan = build_report_date_plan(report_date)
    day_ahead_workbook = resolve_day_ahead_workbook(day_ahead_workbook, plan["dayAheadDate"])
    workbook_date_warnings = check_workbook_dates(
        plan,
        online_workbook=online_workbook,
        day_ahead_workbook=day_ahead_workbook,
        daily_clearing_workbook=daily_clearing_workbook,
    )
    for warning in workbook_date_warnings:
        log(
            "\u5de5\u4f5c\u7c3f\u65e5\u671f\u4e0e\u53e3\u5f84\u4e0d\u7b26\uff1a"
            f"{warning['label']} \u5e94\u4e3a {warning['expectedDate']}\uff0c"
            f"\u5b9e\u9645\u8fd0\u884c\u65e5 {warning['actualDate']}"
            f"\uff08{Path(warning['file']).name}\uff09"
        )
    actual_template_path = resolve_template_path(
        report_date,
        template_path,
        output_dir,
        prefer_previous_report=prefer_previous_template,
    )
    payload = run_daily_data(
        selected_date=plan["loadAndPriceSelectedDate"],
        compare_date=plan["loadAndPriceCompareDate"],
        subjects=parse_subjects("all") if save_data_summary else LOAD_SUBJECT_CODES,
    )
    market_price_sources = apply_brief_prices(
        payload, day_ahead_workbook=day_ahead_workbook,
        daily_clearing_workbook=daily_clearing_workbook, log=log,
    )
    if save_data_summary:
        summary_path = save_output(payload, output_dir)
        print_result(payload, summary_path)
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
    online_energy_error = None
    try:
        online_energy = load_online_energy_summary(plan["actualOnlineEnergyDate"], online_workbook)
    except Exception as exc:
        online_energy = None
        online_energy_error = str(exc)
    if not online_energy or online_energy["total"] <= 0:
        if online_energy_error is None:
            online_energy_error = (
                f"{date_label(plan['actualOnlineEnergyDate'])} 的上网电量为 0（尚未出数），"
                "上网电量保留模板供人工填写，日前均价独立更新"
            )
        log(f"上网电量未取数，保留模板电量并独立更新日前均价：{online_energy_error}")
    fill_online_completion(online_paragraph, plan["actualOnlineEnergyDate"], online_energy, online_prices)
    replace_paragraph(day_ahead_paragraph, build_day_ahead_clearing_text(plan["dayAheadDate"], day_ahead_clearing))

    strategy_heading = find_exact_paragraph(doc, "三、现货交易策略")
    replace_paragraph(next_nonempty_paragraph(doc, strategy_heading), build_strategy_text(payload))
    # 粤东 500kV 线路信息不是每日固定内容，模板里带的那段直接删掉。
    remove_east_guangdong_section(doc)
    plant_strategy_source = day_ahead_workbook
    try:
        plant_strategies = load_plant_strategies(plant_strategy_source)
    except Exception as exc:
        plant_strategies = {}
        log(f"三厂策略取数失败，已保留模板原文：{exc}")
    filled_plants = fill_plant_strategies(doc, plant_strategies)
    missing_plants = [p for p in PLANT_STRATEGY_LABELS if p not in filled_plants]
    if missing_plants:
        log("以下厂策略未从 D 日「快报（日前）」A39 解析出，保留模板原文：" + "、".join(missing_plants))
    table_title = find_paragraph(doc, lambda text: text.startswith("表1"))
    replace_paragraph(table_title, f"表1  {date_label(plan['dailyClearingDate'])}分公司日清分情况")
    # 两个表格各自从新页开始，避免标题与表格被分页切断。
    paginated = apply_table_page_breaks(doc)
    if daily_clearing is not None:
        daily_filled_rows = fill_daily_clearing_table(doc.tables[0], daily_clearing["rows"])
        if daily_clearing.get("spot"):
            spot_filled = fill_spot_deviation_analysis(
                doc, build_spot_deviation_texts(daily_clearing["spot"])
            )
    clear_auto_section_red(doc, spot_filled=spot_filled)
    day_ahead_heading = find_exact_paragraph(doc, "1.日前市场")
    day_ahead_paragraph = next_nonempty_paragraph(doc, day_ahead_heading)
    replace_paragraph(day_ahead_paragraph, build_day_ahead_market_text(payload))
    highlight_negative_numbers(day_ahead_paragraph)
    actual_heading = find_exact_paragraph(doc, "2.实时市场")
    actual_paragraph = next_nonempty_paragraph(doc, actual_heading)
    replace_paragraph(actual_paragraph, build_actual_market_text(payload))
    highlight_negative_numbers(actual_paragraph)
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
        section = doc.sections[0]
        compact_forecast_table(
            doc.tables[1],
            available_height_cm=(
                section.page_height.cm - section.top_margin.cm - section.bottom_margin.cm
            ),
        )

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
    manual_sections = collect_manual_sections(doc, filled_plants=set(filled_plants))
    if manual_sections:
        names = sorted({item["section"] for item in manual_sections})
        log(f"以下小节共 {len(manual_sections)} 段沿用上一份报告，需人工核对：{'、'.join(names)}")
    audit = {
        "reportDate": report_date,
        "marketPriceSources": market_price_sources,
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
        "workbookDateWarnings": workbook_date_warnings,
        "manualSections": manual_sections,
        "tablePageBreaks": paginated,
        "plantStrategiesFilled": filled_plants,
        "plantStrategiesMissing": missing_plants,
        "plantStrategiesSource": str(plant_strategy_source) if plant_strategy_source else None,
        "onlineEnergyError": online_energy_error,
        "dailyClearingError": daily_clearing_error,
        "weatherError": weather_error,
        "knownGaps": [
            "上网电量取 D-1 出清情况工作簿「快报」表：火电三厂与清远取 J 列，新能源取 K 列，总数为 J7＋新能源合计；尚未出数或读取失败时保留模板电量原文和格式供人工填写，只将后半句日前均价独立更新为黑字。",
            "P14 日前出清电量和日前价格来自报告日出清情况 Excel；火电按厂名定位取快报（日前）F/G，新能源取 G/H，清远计划电量按基础信息清远上网电量区的合计行取 AA+AC 后除以10，并计入分公司总电量。",
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
    parser.add_argument("--save-data-summary", action="store_true", help="共用同次网站抓取结果保存数据汇总 Excel")
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
            save_data_summary=args.save_data_summary,
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
