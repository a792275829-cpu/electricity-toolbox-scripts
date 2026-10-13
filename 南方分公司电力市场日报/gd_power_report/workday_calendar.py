"""Offline Chinese date types for report titles; only 2026 is configured."""

from __future__ import annotations

from datetime import date, timedelta


WEEKDAYS = "一二三四五六日"


def _date_range(start: date, end: date) -> set[date]:
    return {
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    }


# 国务院办公厅国办发明电〔2025〕7号：2026年部分节假日安排。
HOLIDAYS_2026 = set().union(
    _date_range(date(2026, 1, 1), date(2026, 1, 3)),
    _date_range(date(2026, 2, 15), date(2026, 2, 23)),
    _date_range(date(2026, 4, 4), date(2026, 4, 6)),
    _date_range(date(2026, 5, 1), date(2026, 5, 5)),
    _date_range(date(2026, 6, 19), date(2026, 6, 21)),
    _date_range(date(2026, 9, 25), date(2026, 9, 27)),
    _date_range(date(2026, 10, 1), date(2026, 10, 7)),
)
ADJUSTED_WORKDAYS_2026 = {
    date(2026, 1, 4),
    date(2026, 2, 14),
    date(2026, 2, 28),
    date(2026, 5, 9),
    date(2026, 9, 20),
    date(2026, 10, 10),
}


def _date_type(value: date) -> str | None:
    """Return a confirmed date type, or None when the year's calendar is missing."""
    if value.year != 2026:
        return None
    if value in ADJUSTED_WORKDAYS_2026:
        return "调休工作日（补班）"
    if value in HOLIDAYS_2026:
        return "节假日"
    if value.weekday() >= 5:
        return "周末休息日"
    return "工作日"


def is_workday(value: date) -> bool:
    """Keep the legacy boolean API and its weekday fallback for unconfigured years.

    The fallback is not an official holiday determination. Report titles use
    format_date_status instead so missing calendars are explicitly marked.
    """
    status = _date_type(value)
    if status is None:
        return value.weekday() < 5
    return status in {"工作日", "调休工作日（补班）"}


def format_date_status(value: date) -> str:
    status = _date_type(value) or "日历待更新"
    return (
        f"{value.month}月{value.day}日，"
        f"周{WEEKDAYS[value.weekday()]}，{status}"
    )
