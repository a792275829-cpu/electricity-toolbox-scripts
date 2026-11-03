"""The shipped forecast seed must stay small while feeding the model unchanged."""
import ast
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packaging/tauri"))
WORKBOOK = ROOT / "广东电价预测/输出/广东电价数据总表.xlsx"
FEATURES = ROOT / "广东电价预测/gd_price_forecast/features.py"
DATA = ROOT / "广东电价预测/gd_price_forecast/data.py"


def _literal(path: Path, name: str):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name} 缺少 {name}")


@unittest.skipUnless(WORKBOOK.is_file(), "缺少预测总表样本")
class ForecastSeedTests(unittest.TestCase):
    def test_columns_match_the_model_definition(self):
        import forecast_seed

        self.assertEqual(forecast_seed.TARGET, _literal(DATA, "TARGET"))
        self.assertEqual(forecast_seed.FORECAST_COLUMNS, _literal(FEATURES, "FORECAST_COLUMNS"))
        self.assertEqual(forecast_seed.SEED_COLUMNS,
                         ("日期", "时刻", forecast_seed.TARGET) + forecast_seed.FORECAST_COLUMNS)

    def test_seed_keeps_one_year_of_model_columns_only(self):
        import forecast_seed
        from openpyxl import load_workbook

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "seed.xlsx"
            stats = forecast_seed.build(WORKBOOK, target)
            self.assertLess(stats["target_bytes"], stats["source_bytes"] * 0.2)
            self.assertLess(stats["rows"], 40000)
            book = load_workbook(target, read_only=True)
            try:
                rows = book["整合数据"].iter_rows(values_only=True)
                header = list(next(rows))
                self.assertEqual(header, list(forecast_seed.SEED_COLUMNS))
                body = list(rows)
            finally:
                book.close()
        self.assertEqual(len(body), stats["rows"])
        times = {row[1] for row in body}
        self.assertEqual(len(times), 96)
        import pandas as pd
        dates = pd.to_datetime(pd.Series([row[0] for row in body]))
        self.assertEqual(dates.nunique(), stats["rows"] // 96)
        self.assertGreaterEqual(dates.min(), dates.max() - pd.DateOffset(months=12))
