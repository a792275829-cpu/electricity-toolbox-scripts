from __future__ import annotations

import unittest

from generate_red_marked_report import (
    build_actual_market_text, build_day_ahead_market_text,
    build_realtime_text, build_strategy_text, delta_phrase, load_change_text,
    load_trend_word, startup_change_text, UNIT_YUAN_PER_MWH, LOAD_SUBJECT_CODES,
)


class ReportTextTests(unittest.TestCase):
    def test_load_change_text_uses_flat_when_delta_rounds_to_zero(self) -> None:
        # 人工报告写「环比持平」，不写「环比上升0万千瓦」。
        self.assertEqual(load_change_text(0), "持平")
        self.assertEqual(load_change_text(4.9), "持平")
        self.assertEqual(load_change_text(-4.9), "持平")
        self.assertEqual(load_change_text(1000), "上升100万千瓦")
        self.assertEqual(load_change_text(-1980), "下降198万千瓦")

    def test_load_trend_word_has_no_number(self) -> None:
        # 句首「统调负荷需求上升，全天最大…」只带方向，不带数值。
        self.assertEqual(load_trend_word(0), "持平")
        self.assertEqual(load_trend_word(4), "持平")
        self.assertEqual(load_trend_word(1000), "上升")
        self.assertEqual(load_trend_word(-1980), "下降")

    def test_price_and_startup_deltas_use_flat_when_shown_as_zero(self) -> None:
        # 均价保留两位小数、台数取整，展示值为 0 时同样写「持平」。
        self.assertEqual(delta_phrase(0, unit=UNIT_YUAN_PER_MWH, digits=2), "持平")
        self.assertEqual(delta_phrase(0.004, unit=UNIT_YUAN_PER_MWH, digits=2), "持平")
        self.assertEqual(delta_phrase(-0.004, unit=UNIT_YUAN_PER_MWH, digits=2), "持平")
        self.assertEqual(delta_phrase(11.35, unit=UNIT_YUAN_PER_MWH, digits=2), "上升11.35元/兆瓦时")
        self.assertEqual(delta_phrase(-5.59, unit=UNIT_YUAN_PER_MWH, digits=2), "下降5.59元/兆瓦时")
        self.assertEqual(startup_change_text(0), "持平")
        self.assertEqual(startup_change_text(0.4), "持平")
        self.assertEqual(startup_change_text(2), "增加2台")
        self.assertEqual(startup_change_text(-3), "减少3台")

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
            by_type[code] = {"dayAhead": summary, "actual": summary, "actualVsDayAhead": summary}
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

    def test_actual_output_trends_compare_same_day_forecast(self):
        from fetch_daily_data import build_load_info_comparison
        selected = {"date": "2026-11-10", "byType": {
            code: {"actual": {"max": 124510, "min": 81950, "avg": avg},
                   "dayAhead": {"avg": forecast}}
            for code, avg, forecast in (("1", 100, 110), ("6", 50, 60),
                                         ("2", 400, 500), ("3", 13349.65, 9126.06))}}
        previous = {"date": "2026-11-09", "byType": {
            code: {"actual": {"max": 130000, "avg": avg}, "dayAhead": {"avg": 1}}
            for code, avg in (("1", 110), ("6", 60), ("2", 300), ("3", 15000))}}
        comparison = build_load_info_comparison(selected, previous)
        payload = {"marketAnalysisData": {"loadInfo": comparison, "priceBoard": {
            "rows": [{"dataType": "RT", "selectedDailyAvgPrice": 100}]}}}
        text = build_actual_market_text(payload)
        self.assertIn("A类电出力减少、地方电出力增加", text)
        import tempfile
        from pathlib import Path
        from docx import Document
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "realtime-market.docx"
            document = Document()
            document.add_paragraph(text)
            document.save(target)
            self.assertEqual(Document(target).paragraphs[0].text, text)
        self.assertIn("实时统调负荷需求下降，全天最大值12451万千瓦，最小值8195万千瓦", text)
        for code in ("2", "3"):
            # Missing same-day forecasts must never fall back to previous actuals.
            del selected["byType"][code]["dayAhead"]
        payload["marketAnalysisData"]["loadInfo"] = build_load_info_comparison(selected, previous)
        text = build_actual_market_text(payload)
        self.assertIn("A类电出力待补充、地方电出力待补充", text)

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
