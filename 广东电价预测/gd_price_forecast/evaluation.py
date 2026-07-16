import numpy as np
import pandas as pd


def make_date_splits(
    identity: pd.DataFrame,
    holdout_days: int = 14,
    validation_days: int = 7,
    n_splits: int = 3,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray]:
    dates = pd.Index(sorted(pd.to_datetime(identity["日期"]).unique()))
    required = holdout_days + validation_days * n_splits + 1
    if len(dates) < required:
        raise ValueError(f"至少需要{required}个完整日期，当前只有{len(dates)}个")
    holdout_dates = dates[-holdout_days:]
    development_dates = dates[:-holdout_days]
    folds = []
    for split in range(n_splits, 0, -1):
        valid_end = len(development_dates) - validation_days * (split - 1)
        valid_start = valid_end - validation_days
        train_dates = development_dates[:valid_start]
        valid_dates = development_dates[valid_start:valid_end]
        train_idx = np.flatnonzero(identity["日期"].isin(train_dates).to_numpy())
        valid_idx = np.flatnonzero(identity["日期"].isin(valid_dates).to_numpy())
        folds.append((train_idx, valid_idx))
    holdout_idx = np.flatnonzero(identity["日期"].isin(holdout_dates).to_numpy())
    return folds, holdout_idx


def previous_day_baseline(y: pd.Series) -> pd.Series:
    return y.shift(96)


def seven_day_baseline(y: pd.Series) -> pd.Series:
    return y.shift(96 * 7)


def evaluate_predictions(
    y_true: np.ndarray | pd.Series,
    y_pred: np.ndarray | pd.Series,
    identity: pd.DataFrame,
) -> tuple[dict[str, float], pd.DataFrame, pd.DataFrame]:
    result = identity[["日期", "时刻"]].reset_index(drop=True).copy()
    result["absolute_error"] = np.abs(np.asarray(y_true) - np.asarray(y_pred))
    metrics = {"mae": float(result["absolute_error"].mean())}
    daily = result.groupby("日期", as_index=False)["absolute_error"].mean().rename(columns={"absolute_error": "mae"})
    by_slot = result.groupby("时刻", as_index=False)["absolute_error"].mean().rename(columns={"absolute_error": "mae"})
    return metrics, daily, by_slot
