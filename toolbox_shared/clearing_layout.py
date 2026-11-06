"""Label-based positions in the four- and six-unit Haimen clearing templates."""
from __future__ import annotations

import re


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
