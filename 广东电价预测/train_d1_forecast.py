import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from gd_price_forecast.data import TARGET, load_model_data
from gd_price_forecast.evaluation import make_date_splits
from gd_price_forecast.features import build_features
from gd_price_forecast.modeling import fit_model, predict_model, run_experiments
from gd_price_forecast.reporting import write_d1_price_workbook

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "输出" / "广东电价数据总表.xlsx"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "输出" / "D+1预测"
OBSOLETE_OUTPUTS = (
    "模型评估详情.xlsx",
    "模型评估指标.json",
    "模型实验结果.csv",
    "D+1日前电价模型.joblib",
    "D+1日前电价预测结果.xlsx",
)

DEFAULT_CONFIGS = [
    {"name": "lgb_l20_m50", "kind": "lightgbm", "params": {"n_estimators": 700, "learning_rate": 0.02, "num_leaves": 20, "min_child_samples": 50, "colsample_bytree": 0.8, "reg_lambda": 3.0, "reg_alpha": 1.0}},
    {"name": "lgb_l15_m20", "kind": "lightgbm", "params": {"n_estimators": 700, "learning_rate": 0.02, "num_leaves": 15, "min_child_samples": 20, "colsample_bytree": 0.8, "reg_lambda": 3.0, "reg_alpha": 1.0}},
    {"name": "xgb_d3", "kind": "xgboost", "params": {"n_estimators": 700, "learning_rate": 0.025, "max_depth": 3, "min_child_weight": 30, "subsample": 0.9, "colsample_bytree": 0.8, "reg_lambda": 8, "reg_alpha": 1}},
    {"name": "hist_l15", "kind": "hist_gradient_boosting", "params": {"max_iter": 400, "learning_rate": 0.04, "max_leaf_nodes": 15, "min_samples_leaf": 40, "l2_regularization": 3}},
]


def available_default_configs() -> list[dict[str, Any]]:
    available = []
    for config in DEFAULT_CONFIGS:
        if config["kind"] == "lightgbm":
            try:
                import lightgbm  # noqa: F401
            except ImportError:
                continue
        if config["kind"] == "xgboost":
            try:
                import xgboost  # noqa: F401
            except ImportError:
                continue
        available.append(config)
    return available


def append_prediction_days(frame: pd.DataFrame, days: int) -> pd.DataFrame:
    if days <= 0:
        return frame
    last_date = frame["日期"].max()
    times = frame.loc[frame["日期"].eq(last_date), "时刻"].tolist()
    if len(times) != 96:
        raise ValueError("最后一个日期必须包含 96 个时点")
    additions = []
    for offset in range(1, days + 1):
        part = pd.DataFrame({"日期": last_date + pd.Timedelta(days=offset), "时刻": times})
        part[TARGET] = np.nan
        additions.append(part.reindex(columns=frame.columns))
    return pd.concat([frame, *additions], ignore_index=True)


def slice_training_window(frame: pd.DataFrame, months: int) -> pd.DataFrame:
    if months <= 0:
        raise ValueError("训练窗口月数必须大于 0")
    latest_date = frame["日期"].max()
    cutoff = latest_date - pd.DateOffset(months=months)
    return frame.loc[frame["日期"].ge(cutoff)].copy().reset_index(drop=True)


def _recursive_prediction(
    frame: pd.DataFrame,
    model,
    blend_weight: float,
    prediction_dates: list[pd.Timestamp] | None = None,
) -> pd.DataFrame:
    working = frame.copy()
    dates = prediction_dates or sorted(
        working.loc[working[TARGET].isna(), "日期"].unique()
    )
    outputs = []
    for day in dates:
        X, _, identity = build_features(working)
        rows = identity["日期"].eq(day)
        model_prediction = predict_model(model, X.loc[rows])
        baseline = X.loc[rows, "price_lag_1d"].to_numpy(dtype=float)
        blended = np.where(np.isfinite(baseline), blend_weight * model_prediction + (1 - blend_weight) * baseline, model_prediction)
        working.loc[working["日期"].eq(day), TARGET] = blended
        part = identity.loc[rows].copy()
        part["模型预测"] = model_prediction
        part["D-1基线"] = baseline
        part["预测日前电价"] = blended
        part["预测方式"] = np.where(np.isfinite(baseline), "模型与D-1集成", "模型递推")
        outputs.append(part)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame(columns=["日期", "时刻", "预测日前电价"])


def run_pipeline(
    input_path: Path | str,
    output_dir: Path | str,
    holdout_days: int = 14,
    validation_days: int = 7,
    n_splits: int = 3,
    configs: list[dict[str, Any]] | None = None,
    blend_weight: float = 0.7,
    forecast_days: int = 1,
    training_months: int = 3,
) -> dict[str, Any]:
    input_path, output_dir = Path(input_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = slice_training_window(load_model_data(input_path), training_months)
    data_end = frame["日期"].max()
    frame = append_prediction_days(frame, forecast_days)
    prediction_dates = [
        data_end + pd.Timedelta(days=offset)
        for offset in range(1, forecast_days + 1)
    ]
    X_all, y_all, identity_all = build_features(frame)
    labeled = y_all.notna()
    X = X_all.loc[labeled].reset_index(drop=True)
    y = y_all.loc[labeled].reset_index(drop=True)
    identity = identity_all.loc[labeled].reset_index(drop=True)
    folds, holdout = make_date_splits(identity, holdout_days, validation_days, n_splits)
    experiments = run_experiments(X, y, folds, configs or available_default_configs())
    best_config = experiments.iloc[0]["config"]

    train = np.setdiff1d(np.arange(len(y)), holdout)
    holdout_model = fit_model(X.iloc[train], y.iloc[train], best_config)
    model_pred = predict_model(holdout_model, X.iloc[holdout])
    baseline = X.iloc[holdout]["price_lag_1d"].to_numpy(dtype=float)
    blended = blend_weight * model_pred + (1 - blend_weight) * baseline
    holdout_y = y.iloc[holdout].to_numpy()
    mae = float(mean_absolute_error(holdout_y, blended))

    final_model = fit_model(X, y, best_config)
    future = _recursive_prediction(
        frame, final_model, blend_weight, prediction_dates=prediction_dates
    )
    summary = {
        "mae": mae,
    }
    for filename in OBSOLETE_OUTPUTS:
        (output_dir / filename).unlink(missing_ok=True)
    operational_path = output_dir / "D+1日前电价.xlsx"
    write_d1_price_workbook(operational_path, future)
    (output_dir / "MAE.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="广东 D+1 日前电价预测")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--forecast-days", type=int, default=1)
    parser.add_argument("--training-months", type=int, default=3)
    args = parser.parse_args()
    print(
        json.dumps(
            run_pipeline(
                args.input,
                args.output_dir,
                forecast_days=args.forecast_days,
                training_months=args.training_months,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
