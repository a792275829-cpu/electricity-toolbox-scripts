"""Shared user-date parsing; service requests always receive ISO dates."""

from __future__ import annotations

import re
import unicodedata
from datetime import date


DATE_INPUT_HINT = "点击日期框选择，或粘贴日期"
DATE_INPUT_ERROR = "请输入有效年月日，例如 2026-5-3 或 2026年5月3日。"


def parse_date_input(value: str) -> date:
    text = unicodedata.normalize("NFKC", value).strip()
    match = re.fullmatch(r"([0-9]{4})([-/.])([0-9]{1,2})\2([0-9]{1,2})", text)
    if match:
        parts = match.group(1), match.group(3), match.group(4)
    else:
        match = re.fullmatch(r"([0-9]{4})年\s*([0-9]{1,2})月\s*([0-9]{1,2})日", text)
        if match is None:
            match = re.fullmatch(r"([0-9]{4})([0-9]{2})([0-9]{2})", text)
        if match is None:
            raise ValueError(DATE_INPUT_ERROR)
        parts = match.groups()
    try:
        return date(*(int(part) for part in parts))
    except ValueError as exc:
        raise ValueError(DATE_INPUT_ERROR) from exc


def normalize_date_input(value: str) -> str:
    return parse_date_input(value).isoformat()


def select_dated_record(records, target: date, *, label: str):
    """Require one explicitly dated source record; never infer a missing date."""
    matching = []
    for record in records if isinstance(records, list) else ():
        if not isinstance(record, dict):
            continue
        try:
            source_date = date.fromisoformat(str(record.get("date") or "")[:10])
        except ValueError:
            continue
        if source_date == target:
            matching.append(record)
    if not matching:
        raise RuntimeError(f"{target.isoformat()} {label}没有返回对应日期数据。")
    if len(matching) != 1:
        raise RuntimeError(f"{target.isoformat()} {label}返回重复日期记录，无法唯一确定数据。")
    return matching[0]
