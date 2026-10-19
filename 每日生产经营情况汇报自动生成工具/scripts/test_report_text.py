from __future__ import annotations

import unittest

from generate_red_marked_report import build_actual_market_text


class ReportTextTests(unittest.TestCase):
    def test_actual_market_text_accepts_missing_load_extrema(self) -> None:
        empty_summary = {
            "selected": {"max": None, "min": None, "avg": None, "sum": None},
            "compare": {"max": None, "min": None, "avg": None, "sum": None},
            "maxDiff": None,
            "minDiff": None,
            "avgDiff": None,
            "sumDiff": None,
        }
        payload = {
            "marketAnalysisData": {
                "loadInfo": {
                    "byType": {
                        code: {"actual": dict(empty_summary)}
                        for code in ("1", "2", "18", "6")
                    }
                },
                "priceBoard": {
                    "rows": [
                        {
                            "dataType": "RT",
                            "selectedDailyAvgPrice": None,
                            "difference": None,
                        }
                    ]
                },
            }
        }

        text = build_actual_market_text(payload)

        self.assertIn("全天最大值待补充万千瓦", text)
        self.assertIn("最小值待补充万千瓦", text)
        self.assertIn("全天实时均价待补充元/兆瓦时", text)


if __name__ == "__main__":
    unittest.main()
