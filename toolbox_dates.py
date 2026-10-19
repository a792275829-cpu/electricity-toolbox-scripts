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
