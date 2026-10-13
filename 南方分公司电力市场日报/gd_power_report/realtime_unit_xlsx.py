from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from gd_power_report.workbook_reader import load_workbook, reuse_workbook_inputs


EXPECTED_UNITS = {
    "汕头": {"1号机": "#1机组", "2号机": "#2机组", "3号机": "#3机组"},
    "海门": {"1号机": "#1机组", "2号机": "#2机组", "3号机": "#3机组", "4号机": "#4机组"},
    "东莞": {
        "#1、#2机组": "一套机组",
        "#3、#4机组": "二套机组",
        "#5、#6机组": "三套机组",
        "#7、#8机组": "四套机组",
    },
}

_METRICS = {
    "日前出清": "day_ahead_mwh",
    "实时出清": "realtime_mwh",
    "实际上网电量": "actual_mwh",
}


def _normal(value: Any) -> str:
    return "".join(str(value or "").split())


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(value.strip(), pattern).date()
            except ValueError:
                continue
    return None


def _hour_label(value: Any) -> str | None:
    if isinstance(value, time):
        return f"{value.hour}:00"
    text = str(value or "").strip()
    if not text:
        return None
    try:
        hour, minute = text.split(":", 1)
        if minute in {"0", "00"} and 0 <= int(hour) <= 23:
            return f"{int(hour)}:00"
    except (TypeError, ValueError):
        pass
    return text


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().startswith("#"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _realtime_date(workbook: Any, expected_date: date) -> date:
    if "新" not in workbook.sheetnames:
        if "基础信息" not in workbook.sheetnames:
            raise ValueError("基础信息!A2 日期不可用：缺少基础信息工作表")
        detected = _as_date(workbook["基础信息"]["A2"].value)
        if detected is None:
            raise ValueError("基础信息!A2 日期不可用")
        if detected != expected_date:
            raise ValueError(
                f"基础信息!A2 检测到日期{detected:%Y-%m-%d}，"
                f"应为{expected_date:%Y-%m-%d}"
            )
        return detected
    sheet = workbook["新"]
    detected = _as_date(sheet["A68"].value)
    if detected is None:
        for row in range(60, min(sheet.max_row, 100) + 1):
            candidate = _as_date(sheet.cell(row, 1).value)
            if candidate:
                detected = candidate
                break
    if detected is None:
        raise ValueError("无法识别新工作表的实时复盘日期")
    if detected != expected_date:
        raise ValueError(f"检测到实时复盘日期{detected:%Y-%m-%d}，应为{expected_date:%Y-%m-%d}")
    return detected


def _unit_blocks(sheet: Any, plant: str) -> dict[str, tuple[int, dict[str, int]]]:
    blocks: dict[str, tuple[int, dict[str, int]]] = {}
    for row in range(1, sheet.max_row):
        if _normal(sheet.cell(row, 1).value) != "时刻":
            continue
        columns: dict[str, int] = {}
        for column in range(1, sheet.max_column + 1):
            label = _normal(sheet.cell(row, column).value)
            if label in _METRICS:
                columns[_METRICS[label]] = column
        if len(columns) != len(_METRICS):
            continue
        title = None
        for title_row in range(row - 1, 0, -1):
            candidate = _normal(sheet.cell(title_row, 1).value)
            if candidate:
                title = candidate
                break
        if title is None:
            raise ValueError(f"{plant}机组块缺少机组名称")
        if "收益测算" in title:
            continue
        if title in blocks:
            raise ValueError(f"{plant}{title}存在重复机组块")
        blocks[title] = (row, columns)
    return blocks


def _parse_unit(sheet: Any, plant: str, source_unit: str, block: tuple[int, dict[str, int]]) -> list[dict[str, Any]]:
    header_row, columns = block
    hours: list[dict[str, Any]] = []
    seen: set[str] = set()
    for offset in range(24):
        expected_hour = f"{offset}:00"
        row = header_row + 2 + offset
        actual_hour = _hour_label(sheet.cell(row, 1).value)
        if actual_hour != expected_hour:
            if actual_hour in seen:
                problem = f"重复小时{actual_hour}"
            else:
                problem = f"缺少小时{expected_hour}（实际为{actual_hour or '空'}）"
            raise ValueError(f"{plant}{source_unit}{problem}")
        seen.add(actual_hour)
        item: dict[str, Any] = {"hour": expected_hour}
        for field, column in columns.items():
            item[field] = _number(sheet.cell(row, column).value)
        hours.append(item)
    for row in range(header_row + 26, sheet.max_row + 1):
        value = sheet.cell(row, 1).value
        if _normal(value) == "时刻":
            break
        extra_hour = _hour_label(value)
        if extra_hour and ":" in extra_hour:
            problem = f"重复小时{extra_hour}" if extra_hour in seen else f"额外小时{extra_hour}"
            raise ValueError(f"{plant}{source_unit}{problem}")
    return hours


@reuse_workbook_inputs
def parse_realtime_unit_workbook(path: str | Path, expected_date: date) -> dict[str, Any]:
    """Parse a current-workbook unit-level realtime recap into ordered MWh hours."""
    source = Path(path).expanduser().resolve()
    workbook = load_workbook(source, data_only=True, read_only=False)
    realtime_date = _realtime_date(workbook, expected_date)
    plants: list[dict[str, Any]] = []
    for plant, unit_map in EXPECTED_UNITS.items():
        if plant not in workbook.sheetnames:
            raise ValueError(f"实时机组 Excel 缺少电厂工作表：{plant}")
        sheet = workbook[plant]
        blocks = _unit_blocks(sheet, plant)
        units: list[dict[str, Any]] = []
        for source_unit, unit in unit_map.items():
            if source_unit not in blocks:
                raise ValueError(f"{plant}缺少机组块：{source_unit}")
            header_row, columns = blocks[source_unit]
            units.append({
                "unit": unit,
                "source_unit": source_unit,
                "hours": _parse_unit(sheet, plant, source_unit, (header_row, columns)),
            })
        unexpected = set(blocks) - set(unit_map)
        if unexpected:
            raise ValueError(f"{plant}存在未识别机组块：{sorted(unexpected)[0]}")
        plants.append({"plant": plant, "units": units})
    return {"realtime_date": realtime_date.isoformat(), "source_path": str(source), "plants": plants}
