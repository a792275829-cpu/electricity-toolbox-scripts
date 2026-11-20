"""Label-based positions in the four- and six-unit Haimen clearing templates."""
from __future__ import annotations

import math
import re
from datetime import time
from unicodedata import normalize


def _label(value):
    return re.sub(r"\s+", "", value) if isinstance(value, str) else ""


def brief_row_index(rows):
    """Index the thermal/renewable summary and units from columns A/B."""
    result = {}
    renewable = False
    plant = None
    for number, row in enumerate(rows, 1):
        label = _label(row[0]) if row else ""
        unit = _label(row[1]) if len(row) > 1 else ""
        if label.startswith(("备注", "日前简析", "实时简析")) or label == "时刻":
            break
        if label == "新能源":
            renewable, plant = True, None
            continue
        if label == "分公司":
            key = "分公司新能源" if renewable else "分公司火电"
        elif not renewable and label in {"汕头", "海门", "东莞", "清远"}:
            plant = label
            key = label
        elif not renewable and not label and plant and re.fullmatch(r"#\d+机组|[一二三四]套机组", unit):
            key = plant + unit
        elif label in {"汕头海风", "海上风电", "鮀莲光伏", "鮀莲", "归湖光伏", "归湖"}:
            key = label
        else:
            continue
        if key in result:
            raise ValueError(f"快报汇总区存在重复标签：{key}")
        result[key] = number
    return result


def qingyuan_plan_values(rows):
    """Read AA/AC at the total below 清远上网电量 and 计划发电量 headers."""
    section = header = False
    for number, row in enumerate(rows, 1):
        if len(row) < 29:
            continue
        label = _label(row[25])
        if label.startswith("清远上网电量"):
            section = True
        elif section and label == "时刻" and all(_label(row[c]) == "计划发电量" for c in (26, 28)):
            header = True
        elif header and label == "合计":
            return {f"AA{number}": row[26], f"AC{number}": row[28]}
    raise KeyError("基础信息中未找到清远上网电量区的计划发电量合计行")


DAY_AHEAD_TRANSITION_PLANTS = ("汕头", "海门")


def unit_day_ahead_transitions(rows):
    """Find single start/stop transitions in the plant's hourly clearing summary."""
    for index in range(1, len(rows)):
        unit_row, header = rows[index - 1:index + 1]
        if not header or _label(header[0]) != "时刻":
            continue
        columns = {}
        duplicates = set()
        for column, (unit, metric) in enumerate(zip(unit_row, header)):
            label = normalize("NFKC", _label(unit))
            match = re.fullmatch(r"(\d+)号机(?:组)?|#(\d+)(?:机组)?", label)
            if match is None or not _label(metric).startswith(("日前电量", "日前出清电量")):
                continue
            key = str(int(match[1] or match[2]))
            if key in columns:
                duplicates.add(key)
            columns[key] = column
        if not columns:
            continue
        hours = rows[index + 1:index + 25]
        if len(hours) != 24 or any(not _matches_hour(row[0], hour) for hour, row in enumerate(hours)):
            return {}
        result = {}
        for key, column in columns.items():
            if key in duplicates:
                continue
            amounts = [row[column] for row in hours]
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in amounts):
                continue
            running = [value > 0 for value in amounts]
            if sum(before != after for before, after in zip(running, running[1:])) == 1:
                result[key] = "运行转备用" if running[0] else "备用转运行"
        return result
    return {}


def _matches_hour(value, hour):
    if isinstance(value, time):
        return value == time(hour)
    if isinstance(value, str):
        return value.strip() in (f"{hour}:00", f"{hour:02d}:00",
                                 f"{hour}:00:00", f"{hour:02d}:00:00")
    return False
