"""Trim the shipped forecast seed to recent history and the columns the model reads.

A fresh install only needs enough history for the widest selectable training
window plus the ten day-ahead columns ``build_features`` consumes; shipping the
full multi-year archive mostly duplicates what the grid API can refetch.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SEED_MONTHS = 12
# Kept in sync with gd_price_forecast: TARGET plus FORECAST_COLUMNS.
TARGET = "全省日前平均电价"
FORECAST_COLUMNS = (
    "日前统调负荷(MW)", "日前A类电源出力(MW)", "日前地方电源出力(MW)",
    "日前西电东送电力(MW)", "日前粤港联络线(MW)", "日前B类竞价空间(MW)",
    "日前发电总出力(MW)", "日前光伏出力(MW)", "日前风电出力(MW)",
    "日前水电(含抽)蓄总出力(MW)",
)
SEED_COLUMNS = ("日期", "时刻", TARGET) + FORECAST_COLUMNS
REQUIRED_COLUMNS = ("日期", "时刻", TARGET)
WORKSPACE = Path(__file__).resolve().parents[2]
MODULE_DIR = WORKSPACE / "广东电价预测"


def _values_match(expected, actual) -> bool:
    if pd.isna(expected) or actual is None:
        return bool(pd.isna(expected)) and actual is None
    if isinstance(expected, pd.Timestamp):
        moment = expected.to_pydatetime()
        if actual == moment:
            return True
        # Columns without a date number format keep the raw Excel serial.
        serial = (moment - datetime(1899, 12, 30)).total_seconds() / 86400
        return isinstance(actual, (int, float)) and math.isclose(
            float(actual), serial, rel_tol=0, abs_tol=1e-9)
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return math.isclose(float(expected), float(actual), rel_tol=1e-12, abs_tol=1e-12)
    return str(actual) == str(expected)


def _verify(frame: pd.DataFrame, target: Path) -> None:
    from openpyxl import load_workbook

    book = load_workbook(target, read_only=True)
    try:
        rows = book["整合数据"].iter_rows(values_only=True)
        header = list(next(rows))
        body = list(rows)
    finally:
        book.close()
    if header != list(frame.columns):
        raise RuntimeError("裁剪后的预测总表表头回读不一致")
    if len(body) != len(frame):
        raise RuntimeError("裁剪后的预测总表行数回读不一致")
    for index in {0, len(frame) // 2, len(frame) - 1}:
        for position, (column, expected) in enumerate(
                zip(frame.columns, frame.iloc[index].tolist())):
            if not _values_match(expected, body[index][position]):
                raise RuntimeError(f"裁剪后的预测总表数值回读不一致：{column}，第{index + 2}行")


def build(source: Path, target: Path, *, months: int = SEED_MONTHS) -> dict:
    """Write a trimmed copy of ``source`` and verify it by reading the result back."""
    frame = pd.read_excel(source, sheet_name="整合数据", engine="openpyxl")
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"预测总表缺少必要字段：{missing}")
    frame["日期"] = pd.to_datetime(frame["日期"])
    columns = [column for column in SEED_COLUMNS if column in frame.columns]
    cutoff = frame["日期"].max() - pd.DateOffset(months=months)
    trimmed = frame.loc[frame["日期"].ge(cutoff), columns].reset_index(drop=True)
    if trimmed.empty:
        raise ValueError("裁剪后预测总表没有数据行")
    for entry in (str(WORKSPACE), str(MODULE_DIR)):
        sys.path.insert(0, entry)
    try:
        # Private writer: the public wrapper's strict format re-read rejects the
        # empty leading cells of columns like 日前发电总出力(MW).
        from update_gdfire_data import _write_large_xlsx
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_large_xlsx(trimmed, target, "整合数据", format_source=source)
    finally:
        sys.path.pop(0)
        sys.path.pop(0)
    _verify(trimmed, target)
    return {"rows": len(trimmed), "columns": len(trimmed.columns), "months": months,
            "first_date": str(trimmed["日期"].min().date()),
            "last_date": str(trimmed["日期"].max().date()),
            "source_bytes": source.stat().st_size, "target_bytes": target.stat().st_size}
