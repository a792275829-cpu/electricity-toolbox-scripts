from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "广东电价预测"


def load_script(name: str, filename: str):
    path = PROJECT / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class GuangdongDataUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.update = load_script("test_guangdong_update", "update_gdfire_data.py")

    def test_modeling_frame_drops_actual_and_realtime_leakage(self) -> None:
        frame = pd.DataFrame(
            {
                "日期": pd.to_datetime(["2026-07-11"]),
                "时刻": ["00:00"],
                "全省日前平均电价": [350.0],
                "日前统调负荷(MW)": [100000.0],
                "全省实时平均电价": [360.0],
                "实际统调负荷(MW)": [101000.0],
                "全空字段": [np.nan],
                "来源文件": ["接口"],
            }
        )

        result = self.update.build_modeling_frame(frame)

        self.assertIn("全省日前平均电价", result)
        self.assertIn("日前统调负荷(MW)", result)
        self.assertNotIn("全省实时平均电价", result)
        self.assertNotIn("实际统调负荷(MW)", result)
        self.assertNotIn("全空字段", result)
        self.assertNotIn("来源文件", result)

    def test_missing_dates_finds_internal_gap_and_incomplete_day(self) -> None:
        rows = []
        for day, slots in (("2026-07-09", 96), ("2026-07-11", 95)):
            for slot in range(slots):
                rows.append(
                    {
                        "日期": day,
                        "时刻": f"{slot // 4:02d}:{slot % 4 * 15:02d}",
                    }
                )

        result = self.update.missing_dates(pd.DataFrame(rows), "2026-07-11")

        self.assertEqual(
            [pd.Timestamp("2026-07-10").date(), pd.Timestamp("2026-07-11").date()],
            result,
        )

    def test_config_falls_back_to_shared_account_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.json"
            original = self.update.SHARED_CONFIG_PATH
            shared = Path(folder) / "shared.json"
            shared.write_text('{"username":"u","password":"p"}', encoding="utf-8")
            self.update.SHARED_CONFIG_PATH = shared
            try:
                result = self.update.load_config(missing)
            finally:
                self.update.SHARED_CONFIG_PATH = original
        self.assertEqual("u", result["username"])


@unittest.skipUnless(
    importlib.util.find_spec("joblib") and importlib.util.find_spec("sklearn"),
    "forecast dependencies are installed in the toolbox virtual environment",
)
class GuangdongForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(PROJECT))
        cls.forecast = load_script("test_guangdong_forecast", "train_d1_forecast.py")

    @classmethod
    def tearDownClass(cls) -> None:
        if sys.path and sys.path[0] == str(PROJECT):
            sys.path.pop(0)

    def test_append_prediction_day_keeps_96_time_slots_and_blank_target(self) -> None:
        times = pd.date_range("2026-07-11", periods=96, freq="15min").strftime("%H:%M")
        frame = pd.DataFrame(
            {
                "日期": pd.Timestamp("2026-07-11"),
                "时刻": times,
                "全省日前平均电价": np.arange(96, dtype=float),
                "日前统调负荷(MW)": np.arange(96, dtype=float) + 100000,
            }
        )

        result = self.forecast.append_prediction_days(frame, 1)
        future = result[result["日期"].eq(pd.Timestamp("2026-07-12"))]

        self.assertEqual(96, len(future))
        self.assertEqual(96, future["时刻"].nunique())
        self.assertTrue(future["全省日前平均电价"].isna().all())

    def test_training_window_keeps_only_recent_three_months(self) -> None:
        dates = pd.date_range("2025-01-01", "2026-07-11", freq="D")
        frame = pd.DataFrame({"日期": dates, "时刻": "00:00", "全省日前平均电价": 1.0})

        result = self.forecast.slice_training_window(frame, 3)

        self.assertEqual(pd.Timestamp("2026-04-11"), result["日期"].min())
        self.assertEqual(pd.Timestamp("2026-07-11"), result["日期"].max())

    def test_sparse_exogenous_field_is_not_used_as_a_feature(self) -> None:
        from gd_price_forecast.features import build_features

        dates = pd.date_range("2026-06-01", periods=10, freq="D")
        rows = []
        for day in dates:
            for slot in range(96):
                rows.append(
                    {
                        "日期": day,
                        "时刻": f"{slot // 4:02d}:{(slot % 4) * 15:02d}",
                        "全省日前平均电价": float(slot),
                        "完整字段": float(slot + 1),
                        "仅一天字段": float(slot) if day == dates[-1] else np.nan,
                    }
                )
        features, _, _ = build_features(pd.DataFrame(rows))

        self.assertIn("完整字段", features)
        self.assertNotIn("仅一天字段", features)

    def test_master_workbook_loader_drops_realtime_and_actual_columns(self) -> None:
        from gd_price_forecast.data import load_model_data

        times = pd.date_range("2026-07-11", periods=96, freq="15min").strftime("%H:%M")
        frame = pd.DataFrame(
            {
                "日期": pd.Timestamp("2026-07-11"),
                "时刻": times,
                "全省日前平均电价": 300.0,
                "日前统调负荷(MW)": 100000.0,
                "实际统调负荷(MW)": 101000.0,
                "全省实时平均电价": 310.0,
            }
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "master.xlsx"
            frame.to_excel(path, index=False, sheet_name="整合数据")
            result = load_model_data(path)

        self.assertIn("日前统调负荷(MW)", result)
        self.assertNotIn("实际统调负荷(MW)", result)
        self.assertNotIn("全省实时平均电价", result)

    def test_d1_output_is_one_sheet_with_96_price_points(self) -> None:
        from gd_price_forecast.reporting import write_d1_price_workbook

        times = pd.date_range("2026-07-13", periods=96, freq="15min")
        prediction = pd.DataFrame(
            {
                "日期": times.normalize(),
                "时刻": times.strftime("%H:%M"),
                "模型预测": np.arange(96),
                "D-1基线": np.arange(96),
                "预测日前电价": np.arange(96, dtype=float) + 300,
            }
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "D+1日前电价.xlsx"
            write_d1_price_workbook(path, prediction)
            with pd.ExcelFile(path) as workbook:
                sheet_names = workbook.sheet_names
            result = pd.read_excel(path, sheet_name="D+1日前电价")

        self.assertEqual(["D+1日前电价"], sheet_names)
        self.assertEqual(["日期", "时刻", "日前电价"], list(result.columns))
        self.assertEqual(96, len(result))
        self.assertEqual(96, result["时刻"].nunique())

    def test_recursive_prediction_can_exclude_historical_missing_dates(self) -> None:
        import unittest.mock as mock

        times = pd.date_range("2026-07-12", periods=96, freq="15min")
        old_missing = pd.DataFrame(
            {
                "日期": pd.Timestamp("2026-07-01"),
                "时刻": times.strftime("%H:%M"),
                "全省日前平均电价": np.nan,
            }
        )
        d1 = old_missing.copy()
        d1["日期"] = pd.Timestamp("2026-07-13")
        frame = pd.concat([old_missing, d1], ignore_index=True)
        identity = frame[["日期", "时刻"]].copy()
        features = pd.DataFrame({"price_lag_1d": 300.0}, index=frame.index)
        target = frame["全省日前平均电价"]

        with mock.patch.object(
            self.forecast, "build_features", return_value=(features, target, identity)
        ), mock.patch.object(
            self.forecast, "predict_model", return_value=np.full(96, 310.0)
        ):
            result = self.forecast._recursive_prediction(
                frame,
                model=object(),
                blend_weight=0.7,
                prediction_dates=[pd.Timestamp("2026-07-13")],
            )

        self.assertEqual([pd.Timestamp("2026-07-13")], list(result["日期"].unique()))


if __name__ == "__main__":
    unittest.main()
