from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

TOOL_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOL_DIR.parent
for entry in (str(ROOT), str(TOOL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from gd_price_forecast.evaluation import (  # noqa: E402
    evaluate_predictions, make_date_splits, previous_day_baseline, seven_day_baseline,
)


def identity(days: int, *, slots: int = 2) -> pd.DataFrame:
    rows = []
    for offset in range(days):
        stamp = pd.Timestamp("2026-01-01") + pd.Timedelta(days=offset)
        for slot in range(slots):
            rows.append({"日期": stamp, "时刻": f"{slot * 15 // 60:02d}:{slot * 15 % 60:02d}"})
    return pd.DataFrame(rows)


class SplitTests(unittest.TestCase):
    def test_folds_and_holdout_sizes(self):
        frame = identity(40)
        folds, holdout = make_date_splits(frame)
        self.assertEqual(len(folds), 3)
        self.assertEqual([len(train) for train, _ in folds], [10, 24, 38])
        self.assertEqual([len(valid) for _, valid in folds], [14, 14, 14])
        self.assertEqual(len(holdout), 28)

    def test_folds_move_forward_and_never_touch_holdout(self):
        frame = identity(40)
        folds, holdout = make_date_splits(frame)
        holdout_dates = set(frame.iloc[holdout]["日期"])
        for train, valid in folds:
            self.assertTrue(set(frame.iloc[valid]["日期"]).isdisjoint(holdout_dates))
            self.assertTrue(max(frame.iloc[train]["日期"]) < min(frame.iloc[valid]["日期"]))

    def test_too_few_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "至少需要36个完整日期，当前只有30个"):
            make_date_splits(identity(30))

    def test_custom_windows(self):
        folds, holdout = make_date_splits(identity(20), holdout_days=5, validation_days=5, n_splits=2)
        self.assertEqual(len(folds), 2)
        self.assertEqual([len(valid) for _, valid in folds], [10, 10])
        self.assertEqual(len(holdout), 10)


class BaselineTests(unittest.TestCase):
    def test_previous_day_uses_96_points(self):
        shifted = previous_day_baseline(pd.Series(range(200)))
        self.assertTrue(shifted.iloc[:96].isna().all())
        self.assertEqual(shifted.iloc[96], 0)

    def test_seven_day_uses_672_points(self):
        shifted = seven_day_baseline(pd.Series(range(800)))
        self.assertTrue(shifted.iloc[:672].isna().all())
        self.assertEqual(shifted.iloc[672], 0)


class EvaluateTests(unittest.TestCase):
    def test_metrics_and_groupings(self):
        frame = identity(3)
        truth = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        prediction = np.array([1.0, 0.0, 3.0, 2.0, 5.0, 8.0])
        metrics, daily, by_slot = evaluate_predictions(truth, prediction, frame)
        self.assertAlmostEqual(metrics["mae"], 1.0)
        self.assertEqual(len(daily), 3)
        self.assertEqual(list(daily.columns), ["日期", "mae"])
        self.assertEqual(list(by_slot.columns), ["时刻", "mae"])
        self.assertEqual(daily["mae"].tolist(), [1.0, 1.0, 1.0])
        self.assertEqual(by_slot["mae"].tolist(), [0.0, 2.0])

    def test_perfect_prediction_has_zero_error(self):
        frame = identity(2)
        values = np.arange(4.0)
        metrics, _, _ = evaluate_predictions(values, values, frame)
        self.assertEqual(metrics["mae"], 0.0)


if __name__ == "__main__":
    unittest.main()
