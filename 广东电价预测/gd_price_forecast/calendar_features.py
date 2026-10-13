"""Official 2024–2026 holiday stages; reuse the toolbox's 2026 workday sets."""
from functools import lru_cache
import importlib.util
from pathlib import Path

import pandas as pd

CALENDAR_SOURCES = {
    2024: "https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202310/t20231025_3286455.html",
    2025: "https://www.bjsjs.gov.cn/ywdt/sjsdt/202502/t20250206_476202_sjs.shtml",
    2026: "https://www.beijing.gov.cn/zhengce/zhengcefagui/202511/t20251104_4258873.html",
}
HOLIDAY_PERIODS = [
    (1, "2023-12-30", "2024-01-01"), (2, "2024-02-10", "2024-02-17"),
    (3, "2024-04-04", "2024-04-06"), (4, "2024-05-01", "2024-05-05"),
    (5, "2024-06-08", "2024-06-10"), (6, "2024-09-15", "2024-09-17"),
    (7, "2024-10-01", "2024-10-07"), (1, "2025-01-01", "2025-01-01"),
    (2, "2025-01-28", "2025-02-04"), (3, "2025-04-04", "2025-04-06"),
    (4, "2025-05-01", "2025-05-05"), (5, "2025-05-31", "2025-06-02"),
    (7, "2025-10-01", "2025-10-08"), (1, "2026-01-01", "2026-01-03"),
    (2, "2026-02-15", "2026-02-23"), (3, "2026-04-04", "2026-04-06"),
    (4, "2026-05-01", "2026-05-05"), (5, "2026-06-19", "2026-06-21"),
    (6, "2026-09-25", "2026-09-27"), (7, "2026-10-01", "2026-10-07"),
]


@lru_cache(maxsize=1)
def _calendar():
    periods = [(kind, pd.Timestamp(start), pd.Timestamp(end)) for kind, start, end in HOLIDAY_PERIODS]
    holidays = {d for _, start, end in periods for d in pd.date_range(start, end)}
    adjusted = set(pd.to_datetime([
        "2024-02-04", "2024-02-18", "2024-04-07", "2024-04-28", "2024-05-11",
        "2024-09-14", "2024-09-29", "2024-10-12", "2025-01-26", "2025-02-08",
        "2025-04-27", "2025-09-28", "2025-10-11",
    ]))
    path = Path(__file__).resolve().parents[2] / "南方分公司电力市场日报/gd_power_report/workday_calendar.py"
    spec = importlib.util.spec_from_file_location("gd_price_official_2026", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    official = set(pd.Timestamp(d) for d in module.HOLIDAYS_2026)
    assert {d for d in holidays if d.year == 2026} == official
    adjusted.update(pd.Timestamp(d) for d in module.ADJUSTED_WORKDAYS_2026)
    return periods, holidays, adjusted


def calendar_features(dates: pd.Series) -> pd.DataFrame:
    periods, holidays, adjusted = _calendar()
    def workday(d):
        return d in adjusted or (d.weekday() < 5 and d not in holidays)
    rows = {}
    for day in pd.to_datetime(dates).dt.normalize().unique():
        day = pd.Timestamp(day)
        known = day.year in CALENDAR_SOURCES
        row = dict(calendar_known=int(known), is_workday=int(workday(day)) if known else float('nan'),
                   is_adjusted_workday=int(day in adjusted), holiday_kind=0, holiday_length=0,
                   holiday_day=0, holiday_days_remaining=0, is_holiday_last=0,
                   post_holiday_workday=0, days_until_holiday=29, days_after_holiday=29)
        for kind, start, end in periods:
            if start <= day <= end:
                row.update(holiday_kind=kind, holiday_length=(end-start).days+1,
                           holiday_day=(day-start).days+1, holiday_days_remaining=(end-day).days,
                           is_holiday_last=int(day == end))
            if start > day:
                row['days_until_holiday'] = min(row['days_until_holiday'], (start-day).days)
            if end < day:
                elapsed = (day-end).days
                row['days_after_holiday'] = min(row['days_after_holiday'], elapsed)
                if elapsed <= 10 and workday(day):
                    count = sum(workday(d) for d in pd.date_range(end+pd.Timedelta(days=1), day))
                    if count <= 5:
                        row['post_holiday_workday'] = count
        rows[day] = row
    lookup = pd.DataFrame.from_dict(rows, orient='index')
    return lookup.reindex(pd.to_datetime(dates).dt.normalize()).set_axis(dates.index)
