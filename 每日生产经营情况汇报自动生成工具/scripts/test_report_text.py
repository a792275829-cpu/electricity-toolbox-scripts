from __future__ import annotations

import unittest

from generate_red_marked_report import (
    build_actual_market_text, build_day_ahead_market_text,
    build_realtime_text, build_strategy_text, LOAD_SUBJECT_CODES,
)


class ReportTextTests(unittest.TestCase):
    def test_word_fetches_the_local_power_code_required_by_its_text(self) -> None:
        self.assertEqual(LOAD_SUBJECT_CODES, ["1", "6", "2", "3"])

    def test_local_power_uses_code_3_in_every_report_section(self) -> None:
        # Local power rises while total generation falls: confusing 3 and 18
        # would reverse the reported trend even though both codes exist.
        subjects = []
        by_type = {}
        for code in ("1", "2", "3", "6", "18"):
            difference = -10 if code == "18" else 10
            metric = {"selected": 100, "compare": 100 - difference,
                      "difference": difference}
            subjects.append({"loadType": code, "comparison": {
                name: dict(metric) for name in ("最大值(MW)", "最小值(MW)", "平均值(MW)")}})
            summary = {"selected": {"max": 100, "min": 10},
                       "maxDiff": difference, "minDiff": difference,
                       "avgDiff": difference}
            by_type[code] = {"dayAhead": summary, "actual": summary}
        prices = {"rows": [{"dataType": kind, "selectedDailyAvgPrice": 100,
                            "difference": 10} for kind in ("PRE", "RT")]}
        payload = {"loadCompare": {"subjects": subjects}, "priceBoard": prices,
                   "marketAnalysisData": {"loadInfo": {"byType": by_type},
                                          "priceBoard": prices}}
        for builder, label in ((build_strategy_text, "地方电源出力"),
                               (build_realtime_text, "地方电出力"),
                               (build_day_ahead_market_text, "地方电出力"),
                               (build_actual_market_text, "地方电出力")):
            with self.subTest(section=builder.__name__):
                text = builder(payload)
                self.assertIn(label + "增加", text)
                self.assertNotIn(label + "减少", text)

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
                        for code in ("1", "2", "3", "6")
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
