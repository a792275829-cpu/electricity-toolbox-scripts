"""Daily unit calculations for the retained report layout."""

from __future__ import annotations

import math
from typing import Any


_ENERGY_FIELDS = ("day_ahead_mwh", "realtime_mwh", "actual_mwh")
_TOLERANCE_RULE = "容差取0.02万千瓦时与实时出清电量3%的较大值"


def _has_missing_hourly_value(hours: list[dict[str, Any]]) -> bool:
    return len(hours) != 24 or any(
        hour.get(field) is None
        for hour in hours
        for field in _ENERGY_FIELDS
    )


def classify_unit_status(
    hours: list[dict[str, Any]],
    realtime: float | None,
    actual: float | None,
) -> tuple[str, str]:
    """Classify a unit using the prescribed priority order and thresholds."""
    if _has_missing_hourly_value(hours) or realtime is None or actual is None:
        return "数据不足", "小时级日前、实时或实际电量存在缺失，无法完成完整日研判。"
    if realtime >= 0.05 and actual <= 0.01:
        return "中标未启机", "实时出清已有中标电量，但实际发电量接近零。"

    previously_positive = False
    consecutive_low = 0
    for hour in hours:
        actual_mwh = hour["actual_mwh"]
        realtime_mwh = hour["realtime_mwh"]
        if previously_positive and actual_mwh <= 0.1 and realtime_mwh > 0.1:
            consecutive_low += 1
            if consecutive_low >= 2:
                return "疑似故障调停", "疑似故障调停，需结合运行记录确认。"
        else:
            consecutive_low = 0
        if actual_mwh > 0:
            previously_positive = True

    if realtime <= 0.01 and actual <= 0.01:
        return "未中标/停备", "实时出清和实际发电量均接近零，机组未中标或处于停备状态。"
    difference = actual - realtime
    tolerance = max(0.02, realtime * 0.03)
    if difference > tolerance and not math.isclose(difference, tolerance, rel_tol=1e-12, abs_tol=1e-12):
        return "超发", f"实际高于实时且超容差；{_TOLERANCE_RULE}。"
    if difference < -tolerance and not math.isclose(difference, -tolerance, rel_tol=1e-12, abs_tol=1e-12):
        return "欠发", f"实际低于实时且超容差；{_TOLERANCE_RULE}。"
    return "跟踪出力", f"实际与实时偏差在容差内；{_TOLERANCE_RULE}。"


def _daily_total(hours: list[dict[str, Any]], field: str) -> float | None:
    if len(hours) != 24 or any(hour.get(field) is None for hour in hours):
        return None
    return round(sum(float(hour[field]) for hour in hours) / 10, 10)


def summarize_realtime_units(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize Task 1's ordered MWh hours as daily 万千瓦时 unit rows."""
    rows: list[dict[str, Any]] = []
    for plant in parsed.get("plants", []):
        for unit in plant.get("units", []):
            hours = list(unit.get("hours", []))
            day_ahead = _daily_total(hours, "day_ahead_mwh")
            realtime = _daily_total(hours, "realtime_mwh")
            actual = _daily_total(hours, "actual_mwh")
            difference = round(actual - realtime, 10) if actual is not None and realtime is not None else None
            difference_rate = difference / realtime if difference is not None and realtime else None
            tolerance = round(max(0.02, realtime * 0.03), 10) if realtime is not None else None
            status, explanation = classify_unit_status(hours, realtime, actual)
            rows.append({
                "plant": plant["plant"],
                "unit": unit["unit"],
                "day_ahead": day_ahead,
                "realtime": realtime,
                "actual": actual,
                "difference": difference,
                "difference_rate": difference_rate,
                "tolerance": tolerance,
                "status": status,
                "explanation": explanation,
            })
    return rows
