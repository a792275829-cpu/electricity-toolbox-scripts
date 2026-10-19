import numpy as np
import pandas as pd

from .data import TARGET
from .calendar_features import calendar_features

TARGET_LAG_DAYS = (1, 2, 3, 7, 14)
METADATA_COLUMNS = {"日期", "时刻", TARGET, "来源文件", "来源记录数"}
MIN_EXOGENOUS_OBSERVATIONS = 96 * 7
WEATHER_COLUMNS = ("温度", "湿度", "降雨量", "降雨", "雨", "风速", "风")
FORECAST_COLUMNS = (
    "日前统调负荷(MW)", "日前A类电源出力(MW)", "日前地方电源出力(MW)",
    "日前西电东送电力(MW)", "日前粤港联络线(MW)", "日前B类竞价空间(MW)",
    "日前发电总出力(MW)", "日前光伏出力(MW)", "日前风电出力(MW)",
    "日前水电(含抽)蓄总出力(MW)",
)


def _date_lag(values, dates, slots, days):
    """Calendar-key join: a missing date must not turn D-2 into D-1."""
    index = pd.MultiIndex.from_arrays([dates, slots])
    lookup = pd.Series(values.to_numpy(), index=index)
    keys = pd.MultiIndex.from_arrays([dates - pd.Timedelta(days=days), slots])
    return pd.Series(lookup.reindex(keys).to_numpy(), index=values.index)


def build_features(
    frame: pd.DataFrame, target: str = TARGET, *, exogenous_mode: str = "legacy",
    holidays: bool = False, supply: bool = False, weather: bool = False,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    ordered = frame.sort_values(["日期", "时刻"]).reset_index(drop=True).copy()
    ordered["日期"] = pd.to_datetime(ordered["日期"]).dt.normalize()
    identity = ordered[["日期", "时刻"]].copy()
    y = pd.to_numeric(ordered[target], errors="coerce").rename(target)

    slot = ordered["时刻"].str.slice(0, 2).astype(int) * 4 + ordered["时刻"].str.slice(3, 5).astype(int) // 15
    X = pd.DataFrame(index=ordered.index)
    X["slot"] = slot
    X["weekday"] = ordered["日期"].dt.weekday
    X["month"] = ordered["日期"].dt.month
    X["day_of_year"] = ordered["日期"].dt.dayofyear
    X["is_weekend"] = (X["weekday"] >= 5).astype("int8")
    X["slot_sin"] = np.sin(2 * np.pi * slot / 96)
    X["slot_cos"] = np.cos(2 * np.pi * slot / 96)

    for days in TARGET_LAG_DAYS:
        X[f"price_lag_{days}d"] = _date_lag(y, ordered["日期"], ordered["时刻"], days)
    X["price_lag_1d_minus_7d"] = X["price_lag_1d"] - X["price_lag_7d"]
    X["previous_day_price_ramp"] = X["price_lag_1d"].groupby(ordered["日期"], sort=False).diff()

    daily = pd.DataFrame({"日期": ordered["日期"], "target": y}).groupby("日期")["target"].agg(
        ["mean", "std", "min", "max"]
    )
    previous_dates = ordered["日期"] - pd.Timedelta(days=1)
    X["previous_day_price_mean"] = previous_dates.map(daily["mean"])
    X["previous_day_price_std"] = previous_dates.map(daily["std"])
    X["previous_day_price_range"] = previous_dates.map(daily["max"] - daily["min"])

    same_slot = pd.DataFrame({"时刻": ordered["时刻"], "target": y})
    for window in (3, 7, 14):
        shifted = same_slot.groupby("时刻", sort=False)["target"].shift(1)
        grouped = shifted.groupby(same_slot["时刻"], sort=False)
        X[f"price_same_slot_mean_{window}d"] = grouped.transform(
            lambda s: s.rolling(window, min_periods=2).mean()
        )
        X[f"price_same_slot_median_{window}d"] = grouped.transform(
            lambda s: s.rolling(window, min_periods=2).median()
        )
        X[f"price_same_slot_std_{window}d"] = grouped.transform(
            lambda s: s.rolling(window, min_periods=2).std()
        )

    if exogenous_mode not in {"legacy", "none", "lagged", "published"}:
        raise ValueError(f"未知外生数据方式: {exogenous_mode}")
    if holidays:
        X = pd.concat([X, calendar_features(ordered['日期'])], axis=1)
    raw = pd.DataFrame(index=ordered.index)
    columns = ordered.columns if exogenous_mode == 'legacy' else FORECAST_COLUMNS
    for column in columns:
        if column in METADATA_COLUMNS:
            continue
        if column not in ordered or exogenous_mode == 'none':
            continue
        numeric = pd.to_numeric(ordered[column], errors="coerce")
        # Explicit schemas stay stable across training/future windows; empty fields
        # are handled by the fitted imputer. Legacy retains the old threshold.
        if exogenous_mode != 'legacy' or numeric.notna().sum() >= MIN_EXOGENOUS_OBSERVATIONS:
            raw[column] = numeric
    if supply and exogenous_mode != 'none':
        load = raw.get('日前统调负荷(MW)')
        wind, solar = raw.get('日前风电出力(MW)'), raw.get('日前光伏出力(MW)')
        if load is not None and wind is not None and solar is not None:
            renew = wind + solar
            raw['风光出力合计参考MW'] = renew
            raw['风光占统调负荷参考比'] = renew / load.where(load > 0)
            raw['负荷扣风光参考MW'] = load - renew
            for name in ('日前统调负荷(MW)', '风光出力合计参考MW', '负荷扣风光参考MW'):
                raw[name + '_时点变化'] = raw[name].groupby(ordered['日期']).diff()
        if load is not None and '日前B类竞价空间(MW)' in raw:
            raw['竞价空间占统调负荷参考比'] = raw['日前B类竞价空间(MW)'] / load.where(load > 0)
            raw['竞价空间_时点变化'] = raw['日前B类竞价空间(MW)'].groupby(ordered['日期']).diff()
    for column in raw:
        days_list = (1, 7) if exogenous_mode == 'lagged' else (0,)
        for days in days_list:
            name = f'{column}__lag_{days}d' if days else column
            X[name] = _date_lag(raw[column], ordered['日期'], ordered['时刻'], days) if days else raw[column]
            X[name + '__missing'] = X[name].isna().astype('int8')
    if weather:
        for column in WEATHER_COLUMNS:
            if column in ordered:
                numeric = pd.to_numeric(ordered[column], errors='coerce')
                X[column + '__weather_lag_1d'] = _date_lag(numeric, ordered['日期'], ordered['时刻'], 1)
                X[column + '__weather_missing'] = X[column + '__weather_lag_1d'].isna().astype('int8')

    return X.replace([np.inf, -np.inf], np.nan), y, identity
