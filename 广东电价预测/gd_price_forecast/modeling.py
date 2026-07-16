from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline


def _estimator(config: dict[str, Any]):
    kind = config["kind"]
    params = dict(config.get("params", {}))
    if kind == "lightgbm":
        from lightgbm import LGBMRegressor

        return LGBMRegressor(objective="regression_l1", random_state=42, n_jobs=-1, verbosity=-1, **params)
    if kind == "xgboost":
        from xgboost import XGBRegressor

        return XGBRegressor(objective="reg:absoluteerror", random_state=42, n_jobs=-1, **params)
    if kind == "hist_gradient_boosting":
        return HistGradientBoostingRegressor(loss="absolute_error", random_state=42, **params)
    if kind == "extra_trees":
        return ExtraTreesRegressor(criterion="absolute_error", random_state=42, n_jobs=-1, **params)
    if kind == "random_forest":
        return RandomForestRegressor(criterion="absolute_error", random_state=42, n_jobs=-1, **params)
    raise ValueError(f"不支持的模型: {kind}")


def fit_model(X: pd.DataFrame, y: pd.Series, config: dict[str, Any]) -> Pipeline:
    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("model", _estimator(config)),
    ])
    pipeline.set_output(transform="pandas")
    pipeline.fit(X, y)
    return pipeline


def predict_model(model: Pipeline, X: pd.DataFrame) -> np.ndarray:
    return np.asarray(model.predict(X), dtype=float)


def run_experiments(
    X: pd.DataFrame,
    y: pd.Series,
    folds: list[tuple[np.ndarray, np.ndarray]],
    configs: list[dict[str, Any]],
) -> pd.DataFrame:
    rows = []
    for config in configs:
        fold_maes = []
        for train_idx, valid_idx in folds:
            model = fit_model(X.iloc[train_idx], y.iloc[train_idx], config)
            prediction = predict_model(model, X.iloc[valid_idx])
            fold_maes.append(float(mean_absolute_error(y.iloc[valid_idx], prediction)))
        rows.append({
            "name": config["name"],
            "kind": config["kind"],
            "mean_mae": float(np.mean(fold_maes)),
            "std_mae": float(np.std(fold_maes)),
            "fold_mae": fold_maes,
            "config": config,
        })
    return pd.DataFrame(rows).sort_values(["mean_mae", "std_mae"]).reset_index(drop=True)
