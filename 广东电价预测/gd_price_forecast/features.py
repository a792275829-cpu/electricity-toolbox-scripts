import numpy as np
import pandas as pd

from .data import TARGET

TARGET_LAG_DAYS = (1, 2, 3, 7, 14)
METADATA_COLUMNS = {"日期", "时刻", TARGET, "来源文件", "来源记录数"}
MIN_EXOGENOUS_OBSERVATIONS = 96 * 7


def build_features(
    frame: pd.DataFrame, target: str = TARGET
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
        X[f"price_lag_{days}d"] = y.shift(96 * days)
    X["price_lag_1d_minus_7d"] = X["price_lag_1d"] - X["price_lag_7d"]
    X["previous_day_price_ramp"] = X["price_lag_1d"].groupby(ordered["日期"], sort=False).diff()

    daily = pd.DataFrame({"日期": ordered["日期"], "target": y}).groupby("日期")["target"].agg(
        ["mean", "std", "min", "max"]
    ).shift(1)
    X["previous_day_price_mean"] = ordered["日期"].map(daily["mean"])
    X["previous_day_price_std"] = ordered["日期"].map(daily["std"])
    X["previous_day_price_range"] = ordered["日期"].map(daily["max"] - daily["min"])

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

    for column in ordered.columns:
        if column in METADATA_COLUMNS:
            continue
        numeric = pd.to_numeric(ordered[column], errors="coerce")
        if numeric.notna().sum() >= MIN_EXOGENOUS_OBSERVATIONS:
            X[column] = numeric
            if numeric.isna().any():
                X[f"{column}__missing"] = numeric.isna().astype("int8")

    return X, y, identity
