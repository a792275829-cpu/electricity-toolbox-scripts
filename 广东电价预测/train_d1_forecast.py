import argparse
from copy import deepcopy
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from gd_price_forecast.data import TARGET, load_model_data, split_labeled_prediction_dates
from gd_price_forecast.evaluation import make_date_splits
from gd_price_forecast.features import build_features
from gd_price_forecast.modeling import fit_model, predict_model, run_experiments
from gd_price_forecast.reporting import write_d1_price_workbook
from gd_price_forecast.paths import DEFAULT_MASTER_WORKBOOK as DEFAULT_INPUT, DEFAULT_OUTPUT_DIR, resolve_paths
from gd_price_forecast.experiments import MODEL_CONFIG, training_indices
from gd_price_forecast.future_inputs import (
    attach_forecast_snapshot, fetch_forecast_snapshot, validate_forecast_snapshot,
)

SCRIPT_DIR = Path(__file__).resolve().parent

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
    feature_options: dict[str, Any] | None = None,
    prepared_features: tuple[pd.DataFrame, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    working = frame.copy()
    dates = prediction_dates or sorted(
        working.loc[working[TARGET].isna(), "日期"].unique()
    )
    outputs = []
    for day in dates:
        if len(dates) == 1 and prepared_features is not None:
            X, identity = prepared_features
        else:
            X, _, identity = build_features(working, **(feature_options or {}))
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
    blend_weight: float | None = None,
    forecast_days: int = 1,
    training_months: int | None = None,
    as_of: str | None = None,
    use_future_inputs: bool = True,
    future_snapshot: pd.DataFrame | None = None,
    snapshot_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if forecast_days != 1:
        raise ValueError("本模块只输出 D+1 日前电价，预测天数必须为 1")
    input_path, output_dir = Path(input_path).expanduser().resolve(), Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = load_model_data(input_path)
    if as_of is not None:
        source = source.loc[source['日期'].le(pd.Timestamp(as_of).normalize())].copy()
    labeled_source, _ = split_labeled_prediction_dates(source)
    if labeled_source.empty:
        raise ValueError('没有完整96点日前电价标签')
    if as_of is not None and labeled_source['日期'].max() != pd.Timestamp(as_of).normalize():
        raise ValueError(f'数据过期或标签未完整发布：要求截至{as_of}，最新完整日前电价为{labeled_source["日期"].max().date()}')
    data_end = labeled_source['日期'].max()
    source = source.loc[source['日期'].le(data_end)].copy().reset_index(drop=True)
    target_day = data_end + pd.Timedelta(days=1)
    frame = append_prediction_days(source, forecast_days)
    prediction_dates = [target_day]
    fallback_reason = '未提供经过发布时间核验的完整预测日供需'
    snapshot_error_type = None
    verified_snapshot = None
    if use_future_inputs:
        try:
            if future_snapshot is None and data_end.date() == datetime.now(ZoneInfo('Asia/Shanghai')).date():
                capture = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S%f')
                future_snapshot, snapshot_receipt = fetch_forecast_snapshot(
                    str(target_day.date()), output_dir / '预测日快照' / capture
                )
            verified_snapshot = validate_forecast_snapshot(future_snapshot, snapshot_receipt, str(target_day.date()))
        except Exception as exc:
            snapshot_error_type = type(exc).__name__
            fallback_reason = str(exc) if isinstance(exc, ValueError) else f'预测日供需抓取失败（{snapshot_error_type}）'
    else:
        fallback_reason = '已禁用预测日供需抓取'
    published = verified_snapshot is not None
    if published:
        frame = attach_forecast_snapshot(frame, verified_snapshot, target_day)
        feature_options = dict(exogenous_mode='published', holidays=True, supply=True)
        default_months, default_weight = 12, 1.0
    else:
        feature_options = dict(exogenous_mode='lagged')
        default_months, default_weight = 3, .25
    months = default_months if training_months in (None, 0) else training_months
    weight = default_weight if blend_weight is None else blend_weight
    if months <= 0:
        raise ValueError('训练窗口月数必须大于0，或用0自动选择')
    if not 0 <= weight <= 1:
        raise ValueError('模型集成权重必须在0至1之间')
    strategy = f'当日供需_{months}月' if published else f'历史供需_{months}月_{weight:.0%}模型{1-weight:.0%}D-1'
    print(f'采用方案：{strategy}；训练窗口={months}个月；模型权重={weight:.0%}', flush=True)
    if not published:
        print(f'历史信息回退原因：{fallback_reason}', flush=True)
    X_all, y_all, identity_all = build_features(frame, **feature_options)
    labeled = y_all.notna()
    X = X_all.loc[labeled].reset_index(drop=True)
    y = y_all.loc[labeled].reset_index(drop=True)
    identity = identity_all.loc[labeled].reset_index(drop=True)
    folds, holdout = make_date_splits(identity, holdout_days, validation_days, n_splits)
    if configs:
        windowed_folds = []
        for _, valid in folds:
            before = identity.iloc[valid]['日期'].min()
            windowed_folds.append((training_indices(identity, y, before, months), valid))
        experiments = run_experiments(X, y, windowed_folds, configs)
        best_config = experiments.iloc[0]['config']
    else:
        best_config = deepcopy(MODEL_CONFIG)

    holdout_start = identity.iloc[holdout]['日期'].min()
    train = training_indices(identity, y, holdout_start, months)
    holdout_model = fit_model(X.iloc[train], y.iloc[train], best_config)
    model_pred = predict_model(holdout_model, X.iloc[holdout])
    baseline = X.iloc[holdout]["price_lag_1d"].to_numpy(dtype=float)
    blended = np.where(np.isfinite(baseline), weight * model_pred + (1 - weight) * baseline, model_pred)
    holdout_y = y.iloc[holdout].to_numpy()
    mae = float(mean_absolute_error(holdout_y, blended))

    final_train = training_indices(identity, y, target_day, months)
    final_model = fit_model(X.iloc[final_train], y.iloc[final_train], best_config)
    future = _recursive_prediction(
        frame, final_model, weight, prediction_dates=prediction_dates, feature_options=feature_options,
        prepared_features=(X_all, identity_all),
    )
    if len(future) != 96 or not np.isfinite(future['预测日前电价']).all():
        raise ValueError('预测结果必须包含96个有限电价值')
    summary = {
        "mae": mae,
        'strategy': strategy,
        'training_months': months,
        'model_weight': weight,
        'model_config': best_config,
        'as_of': str(data_end.date()),
        'target_date': str(target_day.date()),
        'holdout_start': str(holdout_start.date()),
        'holdout_end': str(identity.iloc[holdout]['日期'].max().date()),
        'holdout_points': len(holdout),
        'historical_availability_verified': False,
        'mae_scope': '当日供需条件回测，历史发布时间未归档' if published else '历史信息回测，历史供需可能含修订',
        'snapshot_cutoff': snapshot_receipt['cutoff'] if published else None,
        'snapshot_publications': snapshot_receipt['publications'] if published else [],
        'fallback_reason': None if published else fallback_reason,
        'snapshot_error_type': snapshot_error_type,
    }
    operational_path = output_dir / "D+1日前电价.xlsx"
    write_d1_price_workbook(operational_path, future)
    (output_dir / "MAE.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="广东 D+1 日前电价预测")
    parser.add_argument("--config", type=Path, help="模块配置文件")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--forecast-days", type=int, choices=[1], default=1, help="固定输出 D+1")
    parser.add_argument("--training-months", type=int, default=0, help='0自动选择：当日供需12个月，历史回退3个月')
    parser.add_argument('--as-of', help='预测基准日D，默认上海时区当天；预测D+1')
    parser.add_argument('--compare-algorithms', action='store_true', help='执行开发期选型、独立留出期及区间实验')
    parser.add_argument('--fetch-future-inputs', action='store_true', help='实验时抓取并核验D+1供需曲线发布时间')
    parser.add_argument('--historical-only', action='store_true', help='只使用历史信息，禁用预测日供需抓取')
    args = parser.parse_args()
    paths = resolve_paths(config_path=args.config)
    as_of = args.as_of or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if args.compare_algorithms:
        from gd_price_forecast.experiments import run_comparison
        output=args.output_dir or paths['predictionOutputDir'] / '算法实验'
        snapshot=None
        if args.fetch_future_inputs:
            if as_of != datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():
                raise ValueError('实时快照只适用于当天基准日；历史回测必须使用历史归档快照')
            snapshot,_=fetch_forecast_snapshot(str((pd.Timestamp(as_of)+pd.Timedelta(days=1)).date()),output/'预测日快照')
        print(json.dumps(run_comparison(args.input or paths['masterWorkbook'],
                                       output,as_of=as_of,future_snapshot=snapshot), ensure_ascii=False, indent=2))
        return
    print(
        json.dumps(
            run_pipeline(
                args.input or paths["masterWorkbook"],
                args.output_dir or paths["predictionOutputDir"],
                forecast_days=args.forecast_days,
                training_months=args.training_months,
                as_of=as_of,
                use_future_inputs=not args.historical_only,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
