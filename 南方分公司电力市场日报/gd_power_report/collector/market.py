from __future__ import annotations

import json
import time
from collections import Counter
from math import isfinite
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from gd_power_report.collector.auth import load_toolbox_auth_module
from gd_power_report.config import AppConfig


GENERATION_GROUPS = [
    f"{period}_CLEARANCE_{fuel}"
    for period in ("DAY_AHEAD", "REAL_TIME")
    for fuel in (
        "ALL",
        "COAL",
        "GAS",
        "WIND",
        "SOLAR",
        "NUCLEAR",
        "ENERGY_STORAGE",
        "PUMP_STORAGE",
    )
]

DAY_AHEAD_POWER_CARD_SPECS = [
    ("省内A类电源", "2"),
    ("地方电源出力", "3"),
    ("西电东送电力", "4"),
    ("B类竞价空间", "6"),
]
DAY_AHEAD_CHANGE_LOOKBACK_DAYS = 30
DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS = (
    "DAY_AHEAD_CLEARANCE_COAL",
    "DAY_AHEAD_CLEARANCE_GAS",
)
REALTIME_THERMAL_UNIT_COUNT_GROUPS = (
    "REAL_TIME_CLEARANCE_COAL",
    "REAL_TIME_CLEARANCE_GAS",
)


@dataclass(frozen=True)
class EndpointDefinition:
    topic: str
    method: str
    path: str
    parser: str


def load_endpoint_registry(path: str | Path) -> list[EndpointDefinition]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EndpointDefinition(**row) for row in rows]


def _json(response: Any, label: str) -> dict[str, Any]:
    if not response.ok:
        raise RuntimeError(f"{label}失败：HTTP {response.status}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}失败：接口返回格式不正确")
    if str(payload.get("retCode") or payload.get("code", "")).upper() not in {"T200", "200", ""}:
        raise RuntimeError(f"{label}失败：{payload.get('retMsg') or payload.get('message') or payload.get('msg') or payload.get('retCode')}")
    return payload


def _date_text(value: Any) -> str:
    return str(value or "")[:10]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None



def _period_for_date(periods: Any, target_date: date) -> dict[str, Any] | None:
    target = target_date.isoformat()
    candidates = [
        period
        for period in periods or []
        if _date_text(period.get("date")) == target
    ]
    candidates.sort(key=lambda period: str(period.get("versionDate") or ""), reverse=True)
    return candidates[0] if candidates else None


def _period_metric(period: dict[str, Any], field: str) -> float | None:
    if period is None:
        return None
    value = _number(period.get(field))
    if value is not None:
        return value
    values = [
        number
        for raw in period.get("loadVal") or []
        if (number := _number(raw)) is not None
    ]
    if not values:
        return None
    if field == "max":
        return max(values)
    if field == "min":
        return min(values)
    if field == "avg":
        return sum(values) / len(values)
    if field == "sum":
        # 96 点数据每点代表 15 分钟；MW·h 换算为亿千瓦时。
        return sum(values) * 0.25 / 100_000
    return None



def _missing_west_history_dates(
    payload: dict[str, Any],
    target_date: date,
) -> list[date]:
    items = (payload.get("data") or {}).get("dataNetLoadDTOList") or []
    series = next(
        (item for item in items if str(item.get("loadType") or "") == "4"),
        None,
    )
    available = {
        _date_text(period.get("date"))
        for period in (series.get("forecastPeriodList") if series else None) or []
        if _date_text(period.get("date"))
    }
    return [
        candidate
        for offset in range(1, DAY_AHEAD_CHANGE_LOOKBACK_DAYS + 1)
        if (candidate := target_date - timedelta(days=offset)).isoformat()
        not in available
    ]


def _merge_load_payload(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    target_data = target.setdefault("data", {})
    target_items = target_data.setdefault("dataNetLoadDTOList", [])
    incoming_items = (incoming.get("data") or {}).get("dataNetLoadDTOList") or []
    by_type = {str(item.get("loadType") or ""): item for item in target_items}
    for incoming_item in incoming_items:
        load_type = str(incoming_item.get("loadType") or "")
        target_item = by_type.get(load_type)
        if target_item is None:
            target_item = dict(incoming_item)
            target_item["forecastPeriodList"] = list(
                incoming_item.get("forecastPeriodList") or []
            )
            target_item["actualPeriodList"] = list(
                incoming_item.get("actualPeriodList") or []
            )
            target_items.append(target_item)
            by_type[load_type] = target_item
            continue
        for key in ("forecastPeriodList", "actualPeriodList"):
            target_item.setdefault(key, []).extend(incoming_item.get(key) or [])


def _thermal_unit_series(
    payload: dict[str, Any],
    groups: tuple[str, str],
    data_type: str,
) -> list[list[float]] | None:
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return None
    series: list[list[float]] = []
    for group in groups:
        row = next(
            (
                item
                for item in rows
                if str(item.get("dataType") or "") == data_type
                and str(item.get("dataItemGroup") or "") == group
            ),
            None,
        )
        values = [_number(value) for value in (row.get("unitCount") if row else None) or []]
        if len(values) != 24 or any(value is None or not isfinite(value) or value < 0 or not value.is_integer() for value in values):
            return None
        series.append([float(value) for value in values if value is not None])
    return series


def _retry_pause(attempt: int) -> None:
    time.sleep(attempt)


def _fetch_thermal_payload(context, base_url, target, groups, data_type, label, progress, *, allow_partial=False):
    """Retry transient business/transport errors; require dated complete count series."""
    first_error = None
    last_error = None
    for attempt in range(1, 4):
        response = None
        try:
            response = context.request.post(
                f"{base_url}/gdfire/api/spot/clear/timeType/data/list",
                data={"provinceAreaId": "044", "spotClearEleGroupList": list(groups),
                      "startDate": target, "endDate": target}, timeout=60000,
            )
            payload = _json(response, label)
            if allow_partial:
                matching = [row for row in (payload.get("data") or []) if _date_text(row.get("date")) == target]
                scoped = {**payload, "data": matching}
                if any(_thermal_unit_series(scoped, (group,), data_type) is not None for group in groups):
                    return scoped
            if _thermal_unit_series(payload, groups, data_type) is None:
                raise RuntimeError(f"{label}缺少完整有效的24小时机组台数")
            for group in groups:
                row = next(r for r in payload['data'] if str(r.get('dataType')) == data_type and r.get('dataItemGroup') == group)
                if _date_text(row.get('date')) != target:
                    raise RuntimeError(f"{label}返回日期与请求日期不一致")
            return payload
        except Exception as exc:
            if first_error is None:
                first_error = exc
            last_error = exc
            if attempt < 3:
                progress(f"{target} {label}读取失败：{exc}；正在重试（{attempt}/2）")
                _retry_pause(attempt)
        finally:
            if response is not None:
                response.dispose()
    message = f"{target} {label}（分时分类型出清接口）重试后仍无法取得有效数据；首次异常：{first_error}"
    if str(last_error) != str(first_error):
        message += f"；最后异常：{last_error}"
    raise RuntimeError(message) from first_error


def _thermal_unit_counts(
    payload: dict[str, Any],
    groups: tuple[str, str],
    data_type: str,
) -> list[float] | None:
    series = _thermal_unit_series(payload, groups, data_type)
    return [sum(values) for values in zip(*series)] if series else None


def _thermal_count_breakdown(payload, groups, data_type, *, allow_partial=False):
    """Use coal mode (first occurrence on ties) and the 19:00 gas count."""
    result = {"basis": "coal_mode_gas_1900", "coal_basis": "mode", "gas_basis": "19:00", "tie_breaker": "earliest_occurrence",
              "coal": None, "gas": None}
    series = _thermal_unit_series(payload, groups, data_type)
    if series is None and not allow_partial:
        return result
    for index, fuel in enumerate(("coal", "gas")):
        one = _thermal_unit_series(payload, (groups[index],), data_type) if series is None else None
        values = series[index] if series is not None else one[0] if one else None
        if values is None:
            continue
        if fuel == "gas":
            result[fuel] = values[19]
            result["gas_hour_index"] = 19
            continue
        counts = Counter(values)
        frequency = max(counts.values())
        candidates = [value for value in counts if counts[value] == frequency]
        selected = candidates[0]
        result[fuel] = selected
        result[f"{fuel}_mode_frequency"] = frequency
        result[f"{fuel}_mode_candidates"] = candidates
        result[f"{fuel}_mode_hours"] = [i for i, value in enumerate(values) if value == selected]
    return result



def _compare_thermal_breakdowns(current, comparison):
    result = dict(current)
    for fuel in ("coal", "gas"):
        value, baseline = current.get(fuel), comparison.get(fuel)
        result[f"{fuel}_comparison"] = baseline
        result[f"{fuel}_delta"] = value - baseline if value is not None and baseline is not None else None
        for field in ("mode_frequency", "mode_candidates", "mode_hours") if fuel == "coal" else ("hour_index",):
            result[f"{fuel}_comparison_{field}"] = comparison.get(f"{fuel}_{field}")
    return result


def _thermal_summary_card(current, baseline, label):
    value = sum(current[f] for f in ("coal", "gas")) if all(current.get(f) is not None for f in ("coal", "gas")) else None
    comparison = sum(baseline[f] for f in ("coal", "gas")) if all(baseline.get(f) is not None for f in ("coal", "gas")) else None
    card = {"label": label, "value": value, "unit": "台",
            "value_basis": "coal_mode_plus_gas_1900",
            "delta": value - comparison if value is not None and comparison is not None else None,
            "pending": value is None,
            "source": "清能系统现货分时分类型出清电量",
            "thermal_breakdown": _compare_thermal_breakdowns(current, baseline)}
    if value is not None and comparison is None:
        card["comparison_note"] = "同日日前对比待更新" if label == "在线机组数" else "前一日日前对比待更新"
    return card


def parse_day_ahead_thermal_unit_count_card(current_payload, previous_payload):
    """Compare coal modes and 19:00 gas counts with the prior day's day-ahead values."""
    return _thermal_summary_card(
        _thermal_count_breakdown(current_payload, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS, "1"),
        _thermal_count_breakdown(previous_payload, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS, "1"),
        "市场开机",
    )


def parse_realtime_thermal_unit_count_card(realtime_payload, day_ahead_payload):
    """Compare realtime coal mode and 19:00 gas count with same-day day-ahead values."""
    return _thermal_summary_card(
        _thermal_count_breakdown(realtime_payload, REALTIME_THERMAL_UNIT_COUNT_GROUPS, "2"),
        _thermal_count_breakdown(day_ahead_payload, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS, "1"),
        "在线机组数",
    )


def _day_ahead_power_maximum_card(
    items: list[dict[str, Any]],
    label: str,
    load_type: str,
    target_date: date,
) -> dict[str, Any]:
    series = next(
        (
            item
            for item in items
            if str(item.get("loadType") or "") == load_type
        ),
        None,
    )
    periods = series.get("forecastPeriodList") if series else None
    current = _period_for_date(periods, target_date)
    previous_date = target_date - timedelta(days=1)
    previous = _period_for_date(periods, previous_date)
    current_mw = _period_metric(current, "max") if current else None
    previous_mw = _period_metric(previous, "max") if previous else None
    comparison_date = previous_date if previous_mw is not None else None
    comparison_note = None
    if previous_mw is None:
        comparison_note = "前一日对比待更新"
    value = current_mw / 10 if current_mw is not None else None
    previous_value = previous_mw / 10 if previous_mw is not None else None
    card = {
        "label": label,
        "value": value,
        "unit": "万千瓦",
        "delta": (
            value - previous_value
            if value is not None and previous_value is not None
            else None
        ),
        "pending": value is None,
        "comparison_value": previous_value,
    }
    if comparison_date is not None:
        card["comparison_date"] = comparison_date.isoformat()
    if comparison_note is not None:
        card["comparison_note"] = comparison_note
    return card



def _realtime_power_maximum_card(
    items: list[dict[str, Any]],
    label: str,
    load_type: str,
    target_date: date,
) -> dict[str, Any]:
    series = next(
        (
            item
            for item in items
            if str(item.get("loadType") or "") == load_type
        ),
        None,
    )
    day_ahead = _period_for_date(
        series.get("forecastPeriodList") if series else None,
        target_date,
    )
    actual = _period_for_date(
        series.get("actualPeriodList") if series else None,
        target_date,
    )
    actual_mw = _period_metric(actual, "max") if actual else None
    day_ahead_mw = _period_metric(day_ahead, "max") if day_ahead else None
    value = actual_mw / 10 if actual_mw is not None else None
    comparison = day_ahead_mw / 10 if day_ahead_mw is not None else None
    card = {
        "label": label,
        "value": value,
        "unit": "万千瓦",
        "delta": (
            value - comparison
            if value is not None and comparison is not None
            else None
        ),
        "pending": value is None,
        "comparison_value": comparison,
    }
    if load_type == "4":
        card["comparison_value"] = comparison
        card["comparison_date"] = target_date.isoformat()
        if value is not None and comparison is None:
            card["comparison_note"] = "同日日前对比待更新"
    return card



def parse_realtime_market_comparison(
    payload: dict[str, Any],
    target_date: date,
    *, allow_partial: bool = False,
) -> list[dict[str, Any]]:
    """Build actual-load cards compared with the same day's day-ahead values."""
    items = (payload.get("data") or {}).get("dataNetLoadDTOList") or []
    load = next((item for item in items if str(item.get("loadType") or "") == "1"), None)
    if not load and not allow_partial:
        raise RuntimeError("负荷接口未返回统调负荷（loadType=1）")
    load = load or {}
    day_ahead = _period_for_date(load.get("forecastPeriodList"), target_date)
    actual = _period_for_date(load.get("actualPeriodList"), target_date)
    if (not day_ahead or not actual) and not allow_partial:
        raise RuntimeError(f"{target_date.isoformat()} 缺少同日日前或实际统调负荷数据")

    specs = [
        ("统调实际负荷峰值", "max", "万千瓦", 10.0),
        ("统调实际负荷谷值", "min", "万千瓦", 10.0),
    ]
    cards: list[dict[str, Any]] = []
    for label, field, unit, divisor in specs:
        actual_value = _period_metric(actual, field)
        day_ahead_value = _period_metric(day_ahead, field)
        value = actual_value / divisor if actual_value is not None else None
        comparison = day_ahead_value / divisor if day_ahead_value is not None else None
        cards.append(
            {
                "label": label,
                "value": value,
                "unit": unit,
                "delta": (
                    value - comparison
                    if value is not None and comparison is not None
                    else None
                ),
                "pending": value is None,
                "comparison_value": comparison,
            }
        )
    cards.extend(
        _realtime_power_maximum_card(items, label, load_type, target_date)
        for label, load_type in DAY_AHEAD_POWER_CARD_SPECS
    )
    return cards



def parse_market_board_status(
    payload: dict[str, Any],
    target_date: date,
) -> list[dict[str, Any]]:
    """Use same-day day-ahead maintenance for the realtime card by user convention."""
    actual = _market_board_row(payload, target_date, data_type="2")
    day_ahead = _market_board_row(payload, target_date)
    online_units = _number((actual or {}).get("onlineUnit"))
    maintenance = _maintenance_comparison_card(day_ahead, day_ahead, target_date)
    maintenance["value_basis"] = "same_day_ahead"
    return [
        {
            "label": "在线机组数",
            "value": online_units,
            "unit": "台",
            "delta": None,
            "pending": online_units is None,
        },
        maintenance,
    ]


def _market_board_row(
    payload: dict[str, Any],
    target_date: date,
    *,
    data_type: str = "1",
) -> dict[str, Any] | None:
    target = target_date.isoformat()
    rows = (payload.get("data") or {}).get("dataApplyConfigDOList") or []
    return next(
        (
            item
            for item in rows
            if _date_text(item.get("date")) == target
            and str(item.get("dataType") or "") == data_type
            # The API emits unsaved rows with id="" and zero capacity placeholders.
            and (
                item.get("id") != ""
                or item.get("createTime")
                or item.get("updateTime")
            )
        ),
        None,
    )


def _maintenance_comparison_card(
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    comparison_date: date,
    *,
    realtime: bool = False,
) -> dict[str, Any]:
    current_mw = _number((current or {}).get("overHaulCapacity"))
    baseline_mw = _number((baseline or {}).get("overHaulCapacity"))
    value = current_mw / 10 if current_mw is not None else None
    comparison = baseline_mw / 10 if baseline_mw is not None else None
    card = {
        "label": "检修容量",
        "value": value,
        "unit": "万千瓦",
        "delta": value - comparison if value is not None and comparison is not None else None,
        "pending": value is None,
        "comparison_value": comparison,
        "comparison_date": comparison_date.isoformat(),
        "source": "清能系统市场行情看板",
    }
    if value is not None and comparison is None:
        card["comparison_note"] = "同日日前对比待更新" if realtime else "前一日日前对比待更新"
    if value is None:
        card["pending_reason"] = "清能系统未提供有效实时检修记录" if realtime else "清能系统未提供有效日前检修记录"
    return card


def parse_day_ahead_market_cards(
    load_payload: dict[str, Any],
    market_payload: dict[str, Any],
    target_date: date,
    *, allow_partial: bool = False,
) -> list[dict[str, Any]]:
    """Build report-day day-ahead cards compared with the prior day's day-ahead."""
    previous_date = target_date - timedelta(days=1)
    items = (load_payload.get("data") or {}).get("dataNetLoadDTOList") or []
    load = next((item for item in items if str(item.get("loadType") or "") == "1"), None)
    if not load and not allow_partial:
        raise RuntimeError("负荷接口未返回统调负荷（loadType=1）")
    load = load or {}
    current = _period_for_date(load.get("forecastPeriodList"), target_date)
    previous = _period_for_date(load.get("forecastPeriodList"), previous_date)
    if (not current or not previous) and not allow_partial:
        raise RuntimeError(
            f"缺少 {target_date.isoformat()} 或 {previous_date.isoformat()} 的日前统调负荷数据"
        )

    current_board = _market_board_row(market_payload, target_date)
    previous_board = _market_board_row(market_payload, previous_date)

    def comparison_card(label: str, field: str) -> dict[str, Any]:
        value_mw = _period_metric(current, field)
        previous_mw = _period_metric(previous, field)
        value = value_mw / 10 if value_mw is not None else None
        previous_value = previous_mw / 10 if previous_mw is not None else None
        return {
            "label": label,
            "value": value,
            "unit": "万千瓦",
            "delta": (
                value - previous_value
                if value is not None and previous_value is not None
                else None
            ),
            "pending": value is None,
            "comparison_value": previous_value,
        }

    current_online = _number((current_board or {}).get("onlineUnit"))
    previous_online = _number((previous_board or {}).get("onlineUnit"))
    cards = [
        comparison_card("统调负荷峰值", "max"),
        comparison_card("统调负荷谷值", "min"),
        {
            "label": "市场开机",
            "value": current_online,
            "unit": "台",
            "delta": (
                current_online - previous_online
                if current_online is not None and previous_online is not None
                else None
            ),
            "pending": current_online is None,
        },
        _maintenance_comparison_card(current_board, previous_board, previous_date),
    ]
    cards.extend(
        _day_ahead_power_maximum_card(items, label, load_type, target_date)
        for label, load_type in DAY_AHEAD_POWER_CARD_SPECS
    )
    return cards



def collect_realtime_market_comparison(
    config: AppConfig,
    target_date: date,
    progress=lambda _message: None,
    *,
    day_ahead_date: date | None = None,
    username: str | None = None,
    password: str | None = None,
    allow_partial_market: bool = False,
) -> dict[str, Any]:
    """Fetch one day's 96-point day-ahead and actual system-load summaries."""
    if allow_partial_market:
        return _collect_partial_market_comparison(
            config, target_date, progress, day_ahead_date=day_ahead_date,
            username=username, password=password,
        )
    legacy = load_toolbox_auth_module(config.workspace, config.state_dir)
    target = target_date.isoformat()
    sources: dict[str, Any] = {}
    errors: dict[str, str] = {}
    realtime_cards: list[dict[str, Any]] = []
    day_ahead_cards: list[dict[str, Any]] = []
    with legacy.AuthStateLock(), sync_playwright() as playwright:
        legacy_config = legacy.load_config()
        if username is not None and username.strip():
            legacy_config.username = username.strip()
        if password is not None and password:
            legacy_config.password = password
        context = legacy.launch_context(playwright, headless=legacy_config.headless)
        try:
            legacy.ensure_login(context, legacy_config)
            try:
                progress(f"正在读取 {target} 同日日前与实际统调负荷...")
                payload = _json(
                    context.request.get(
                        f"{legacy.BASE_URL}/gdfire/api/data/net/load",
                        params={
                            "startDate": target,
                            "endDate": target,
                            "provinceAreaId": "044",
                            "timeSegment": 2,
                        },
                        timeout=60000,
                    ),
                    "realtime_load_comparison",
                )
                progress(f"正在读取 {target} 在线机组数与检修容量...")
                market_payload = _json(
                    context.request.get(
                        f"{legacy.BASE_URL}/gdfire/api/spot/market/info",
                        params={
                            "provinceAreaId": "044",
                            "timeSegment": 2,
                            "startDate": target,
                            "endDate": target,
                        },
                        timeout=60000,
                    ),
                    "market_board_status",
                )
                realtime_thermal_card = {
                    "label": "在线机组数",
                    "value": None,
                    "unit": "台",
                    "delta": None,
                    "pending": True,
                    "source": "清能系统现货分时分类型出清电量",
                }
                try:
                    realtime_thermal_payload = _fetch_thermal_payload(
                        context, legacy.BASE_URL, target, REALTIME_THERMAL_UNIT_COUNT_GROUPS,
                        "2", "实时机组台数", progress,
                    )
                    realtime_day_ahead_thermal_payload = _fetch_thermal_payload(
                        context, legacy.BASE_URL, target, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS,
                        "1", "同日日前机组台数", progress,
                    )
                    sources["realtime_thermal_unit_count"] = realtime_thermal_payload
                    sources["realtime_day_ahead_thermal_unit_count"] = (
                        realtime_day_ahead_thermal_payload
                    )
                    realtime_thermal_card = parse_realtime_thermal_unit_count_card(
                        realtime_thermal_payload,
                        realtime_day_ahead_thermal_payload,
                    )
                    if isinstance(realtime_thermal_card.get("value"), (int, float)):
                        progress(
                            "实时火电机组开机台数："
                            f"{realtime_thermal_card['value']:g}台"
                        )
                except Exception as exc:
                    errors["realtime_thermal_unit_count"] = f"{target} 实时/同日日前机组台数：{exc}"
                    progress(f"实时火电机组开机台数读取失败：{exc}")
                sources["realtime_load_comparison"] = payload
                sources["market_board_status"] = market_payload
                realtime_cards = parse_realtime_market_comparison(payload, target_date)
                realtime_cards[2:2] = parse_market_board_status(
                    market_payload,
                    target_date,
                )
                realtime_cards = [
                    realtime_thermal_card
                    if card.get("label") == "在线机组数"
                    else card
                    for card in realtime_cards
                ]
            except Exception as exc:
                errors["market_realtime"] = f"{target} 实时负荷/市场看板：{exc}"

            if day_ahead_date is not None:
                previous_day = day_ahead_date - timedelta(days=1)
                day_start = (
                    day_ahead_date - timedelta(days=DAY_AHEAD_CHANGE_LOOKBACK_DAYS)
                ).isoformat()
                day_end = day_ahead_date.isoformat()
                try:
                    progress(f"正在读取 {day_start} 至 {day_end} 日前市场运行数据...")
                    day_load_payload = _json(
                        context.request.get(
                            f"{legacy.BASE_URL}/gdfire/api/data/net/load",
                            params={
                                "startDate": day_start,
                                "endDate": day_end,
                                "provinceAreaId": "044",
                                "timeSegment": 2,
                            },
                            timeout=60000,
                        ),
                        "day_ahead_load_comparison",
                    )
                    day_market_payload = _json(
                        context.request.get(
                            f"{legacy.BASE_URL}/gdfire/api/spot/market/info",
                            params={
                                "provinceAreaId": "044",
                                "timeSegment": 2,
                                "startDate": day_start,
                                "endDate": day_end,
                            },
                            timeout=60000,
                        ),
                        "day_ahead_market_board",
                    )
                    missing_west_dates = _missing_west_history_dates(
                        day_load_payload,
                        day_ahead_date,
                    )
                    for missing_date in missing_west_dates:
                        try:
                            backfill_payload = _json(
                                context.request.get(
                                    f"{legacy.BASE_URL}/gdfire/api/data/net/load",
                                    params={
                                        "startDate": missing_date.isoformat(),
                                        "endDate": missing_date.isoformat(),
                                        "provinceAreaId": "044",
                                        "timeSegment": 2,
                                    },
                                    timeout=60000,
                                ),
                                "west_to_east_history_backfill",
                            )
                        except Exception as exc:
                            progress(
                                "西电东送历史回溯中断："
                                f"{missing_date.isoformat()}：{exc}"
                            )
                            break
                        _merge_load_payload(day_load_payload, backfill_payload)
                        west_card = _day_ahead_power_maximum_card(
                            (day_load_payload.get("data") or {}).get(
                                "dataNetLoadDTOList"
                            )
                            or [],
                            "西电东送电力",
                            "4",
                            day_ahead_date,
                        )
                        if west_card.get("comparison_date"):
                            progress(
                                "西电东送最大出力对比日："
                                f"{west_card['comparison_date']}"
                            )
                            break
                    thermal_card = {
                        "label": "市场开机",
                        "value": None,
                        "unit": "台",
                        "delta": None,
                        "pending": True,
                        "source": "清能系统现货分时分类型出清电量",
                    }
                    try:
                        thermal_payloads: dict[str, dict[str, Any]] = {}
                        for label, thermal_date in (
                            ("current", day_ahead_date),
                            ("previous", previous_day),
                        ):
                            day_text = thermal_date.isoformat()
                            thermal_payloads[label] = _fetch_thermal_payload(
                                context, legacy.BASE_URL, day_text, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS,
                                "1", "当日日前机组台数" if label == "current" else "前一日日前机组台数", progress,
                            )
                            sources[f"thermal_unit_count_{label}"] = thermal_payloads[label]
                        thermal_card = parse_day_ahead_thermal_unit_count_card(
                            thermal_payloads["current"],
                            thermal_payloads["previous"],
                        )
                        progress(
                            "火电机组开机台数："
                            f"{thermal_card.get('value'):g}台"
                            if isinstance(thermal_card.get("value"), (int, float))
                            else "火电机组开机台数待更新。"
                        )
                    except Exception as exc:
                        errors["thermal_unit_count"] = f"{day_ahead_date} 与 {previous_day} 日前机组台数：{exc}"
                        progress(f"火电机组开机台数读取失败：{exc}")
                    sources["day_ahead_load_comparison"] = day_load_payload
                    sources["day_ahead_market_board"] = day_market_payload
                    day_ahead_cards = parse_day_ahead_market_cards(
                        day_load_payload,
                        day_market_payload,
                        day_ahead_date,
                    )
                    day_ahead_cards = [
                        thermal_card if card.get("label") == "市场开机" else card
                        for card in day_ahead_cards
                    ]
                except Exception as exc:
                    errors["market_day_ahead"] = f"{day_ahead_date} 与 {previous_day} 日前负荷/市场看板：{exc}"
        finally:
            context.close()
    return {
        "analysis_date": target,
        "sources": sources,
        "market_realtime_cards": realtime_cards,
        "market_day_ahead_cards": day_ahead_cards,
        "errors": errors,
    }



def _collect_partial_thermal_card(context, base_url, specs, label, sources, errors, progress):
    breakdowns = []
    for key, target, groups, dtype in specs:
        payload = {'data': []}
        try:
            payload = _fetch_thermal_payload(context, base_url, target, groups, dtype, label, progress, allow_partial=True)
            sources[key] = payload
        except Exception as exc:
            errors[key] = str(exc)
        breakdowns.append(_thermal_count_breakdown(payload, groups, dtype, allow_partial=True))
    return _thermal_summary_card(breakdowns[0], breakdowns[1], label)



def _collect_partial_market_comparison(
    config, target_date, progress=lambda _message: None, *,
    day_ahead_date=None, username=None, password=None,
):
    """Read endpoints independently while retaining this tool's login lock."""
    legacy = load_toolbox_auth_module(config.workspace, config.state_dir)
    sources, errors = {}, {}
    target = target_date.isoformat()
    with legacy.AuthStateLock(), sync_playwright() as playwright:
        login = legacy.load_config()
        if username and username.strip():
            login.username = username.strip()
        if password:
            login.password = password
        context = legacy.launch_context(playwright, headless=login.headless)
        try:
            legacy.ensure_login(context, login)

            def get_source(name, path, start, end):
                response = None
                try:
                    response = context.request.get(
                        legacy.BASE_URL + path,
                        params={"provinceAreaId": "044", "timeSegment": 2,
                                "startDate": start, "endDate": end},
                        timeout=60000,
                    )
                    payload = _json(response, name)
                    sources[name] = payload
                    return payload
                except Exception as exc:
                    errors[name] = f"{start} 至 {end} {name}：{exc}"
                    progress(f"网站数据提示：{errors[name]}；继续读取其他指标。")
                    return {}
                finally:
                    if response is not None:
                        response.dispose()

            progress(f"正在分别读取 {target} 实时负荷、市场看板及机组台数...")
            rt_load = get_source("realtime_load_comparison", "/gdfire/api/data/net/load", target, target)
            rt_board = get_source("market_board_status", "/gdfire/api/spot/market/info", target, target)
            rt_thermal = _collect_partial_thermal_card(context, legacy.BASE_URL, [
                ("realtime_thermal_unit_count", target, REALTIME_THERMAL_UNIT_COUNT_GROUPS, "2"),
                ("realtime_day_ahead_thermal_unit_count", target, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS, "1"),
            ], "在线机组数", sources, errors, progress)
            rt_cards = parse_realtime_market_comparison(rt_load, target_date, allow_partial=True)
            rt_cards[2:2] = parse_market_board_status(rt_board, target_date)
            rt_cards = [rt_thermal if card.get("label") == "在线机组数" else card for card in rt_cards]
            da_cards = []
            if day_ahead_date is not None:
                current = day_ahead_date.isoformat()
                previous = (day_ahead_date - timedelta(days=1)).isoformat()
                start = (day_ahead_date - timedelta(days=DAY_AHEAD_CHANGE_LOOKBACK_DAYS)).isoformat()
                progress(f"正在分别读取 {current} 日前负荷、市场看板及机组台数...")
                da_load = get_source("day_ahead_load_comparison", "/gdfire/api/data/net/load", start, current)
                da_board = get_source("day_ahead_market_board", "/gdfire/api/spot/market/info", start, current)
                da_thermal = _collect_partial_thermal_card(context, legacy.BASE_URL, [
                    ("thermal_unit_count_current", current, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS, "1"),
                    ("thermal_unit_count_previous", previous, DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS, "1"),
                ], "市场开机", sources, errors, progress)
                da_cards = parse_day_ahead_market_cards(da_load, da_board, day_ahead_date, allow_partial=True)
                da_cards = [da_thermal if card.get("label") == "市场开机" else card for card in da_cards]
        finally:
            context.close()
    return {
        "analysis_date": target, "sources": sources, "errors": errors,
        "market_day_ahead_cards": da_cards, "market_realtime_cards": rt_cards,
    }
