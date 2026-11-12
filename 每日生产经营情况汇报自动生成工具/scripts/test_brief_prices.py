from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from toolbox_shared.morning_prices import (apply_brief_prices, read_brief_prices,
    apply_day_ahead_market_deltas, read_day_ahead_market_deltas)


class BriefPriceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "clearing.xlsx"

    def write(self, text, *, sheet="快报  (日前)", row=41):
        book = Workbook()
        book.active.title = "基础信息"
        book.active["A2"] = datetime(2026, 11, 10)
        book.create_sheet(sheet).cell(row, 1, text)
        book.save(self.path)
        book.close()
        return self.path

    def test_real_day_ahead_wording_and_decrease(self):
        self.write("日前简析：全省日前出清均价334.57元/兆瓦时，环比下降15.99元/兆瓦时。")
        price = read_brief_prices(self.path, day_ahead=True)["PRE"]
        self.assertEqual((price["price"], price["difference"], price["cell"]), (334.57, -15.99, "A41"))

    def test_shifted_realtime_and_negative_price(self):
        self.write("实时简析：全天实时均价-50元/MWh。", sheet="快报", row=42)
        price = read_brief_prices(self.path, day_ahead=False)["RT"]
        self.assertEqual((price["price"], price["difference"], price["cell"]), (-50, None, "A42"))

    def test_explicit_realtime_clearing_phrase(self):
        self.write("实时简析：实时出清均价471.34厘/kWh，环比上涨2.50厘/kWh。", sheet="快报")
        self.assertEqual(read_brief_prices(self.path, day_ahead=False)["RT"]["difference"], 2.5)

    def test_zero_and_flat_are_values(self):
        self.write("日前简析：全省日前出清均价0元/兆瓦时，环比持平。", sheet="快报（日前）")
        self.assertEqual(read_brief_prices(self.path, day_ahead=True)["PRE"]["difference"], 0)

    def test_missing_and_plant_average_are_not_province_price(self):
        self.write("日前简析：汕头日前出清均价100元/兆瓦时。")
        with self.assertRaises(ValueError):
            read_brief_prices(self.path, day_ahead=True)

    def test_mismatched_run_date_is_rejected(self):
        self.write("日前简析：全省日前出清均价334.57元/兆瓦时。")
        with self.assertRaisesRegex(ValueError, "运行日"):
            read_brief_prices(self.path, day_ahead=True, expected_date="2026-11-09")

    def payload(self):
        board = {"selectedDate": "2026-11-10", "rows": [dict(dataType=kind, typeName=name,
            selectedDate="2026-11-10", selectedDailyAvgPrice=352.1543,
            compareDailyAvgPrice=382.5292, difference=-30.3749,
            selectedPointCount=96, comparePointCount=96)
            for kind, name in (("PRE", "日前"), ("RT", "实时"))]}
        return {"priceBoard": board, "marketAnalysisData": {"priceBoard": deepcopy(board)}}

    def test_override_uses_excel_delta_and_records_source_without_changing_other_rows(self):
        self.write("日前简析：全省日前出清均价334.57元/兆瓦时，环比下降15.99元/兆瓦时。")
        payload = self.payload()
        realtime = deepcopy(payload["priceBoard"]["rows"][1])
        sources = apply_brief_prices(payload, day_ahead_workbook=self.path, daily_clearing_workbook=None, log=lambda _: None)
        row = payload["priceBoard"]["rows"][0]
        self.assertEqual((row["selectedDailyAvgPrice"], row["compareDailyAvgPrice"], row["difference"]), (334.57, 350.56, -15.99))
        self.assertEqual(payload["priceBoard"]["rows"][1], realtime)
        self.assertEqual(sources[0]["website"]["selectedDailyAvgPrice"], 352.1543)
        self.assertIsNone(row["selectedPointCount"])

    def test_realtime_without_delta_does_not_reuse_website_comparison(self):
        self.write("实时简析：全天实时均价471.34元/兆瓦时。", sheet="快报", row=42)
        payload = self.payload()
        old_pre = deepcopy(payload["marketAnalysisData"]["priceBoard"]["rows"][0])
        apply_brief_prices(payload, day_ahead_workbook=None, daily_clearing_workbook=self.path, log=lambda _: None)
        row = payload["marketAnalysisData"]["priceBoard"]["rows"][1]
        self.assertEqual(row["selectedDailyAvgPrice"], 471.34)
        self.assertIsNone(row["difference"])
        self.assertIsNone(row["compareDailyAvgPrice"])
        self.assertEqual(payload["marketAnalysisData"]["priceBoard"]["rows"][0], old_pre)

    def test_failed_workbook_keeps_website_row(self):
        payload = self.payload()
        before = deepcopy(payload)
        sources = apply_brief_prices(payload, day_ahead_workbook=self.path, daily_clearing_workbook=None, log=lambda _: None)
        self.assertEqual(sources, [])
        self.assertEqual(payload, before)


    def test_daily_a41_deltas_override_website_but_preserve_current_values(self):
        self.write("日前简析：市场开机台数451台，环比增加7台，全省日前出清均价384.33元/兆瓦时，环比上涨3.25元/兆瓦时。")
        payload = self.payload()
        market = payload["marketAnalysisData"]
        market["operationPublicData"] = {"startupCount": {
            "selected": {"maxStartupCount": 451}, "difference": 23}}
        current_price = market["priceBoard"]["rows"][0]["selectedDailyAvgPrice"]
        outer = deepcopy(payload["priceBoard"])
        realtime = deepcopy(market["priceBoard"]["rows"][1])
        source = apply_day_ahead_market_deltas(payload, workbook=self.path, log=lambda _: None)
        self.assertEqual(market["operationPublicData"]["startupCount"]["difference"], 7)
        self.assertEqual(market["priceBoard"]["rows"][0]["difference"], 3.25)
        self.assertEqual(market["priceBoard"]["rows"][0]["selectedDailyAvgPrice"], current_price)
        self.assertEqual(payload["priceBoard"], outer)
        self.assertEqual(market["priceBoard"]["rows"][1], realtime)
        self.assertEqual((source["date"], source["cell"]), ("2026-11-10", "A41"))
        from generate_red_marked_report import build_day_ahead_market_text
        from docx import Document
        market["loadInfo"] = {"byType": {code: {"dayAhead": {
            "selected": {"max": 128000, "min": 92000}, "maxDiff": -7000,
            "minDiff": -1000, "avgDiff": 10}} for code in ("1", "2", "3")}}
        text = build_day_ahead_market_text(payload)
        self.assertIn("市场开机台数451台，环比增加7台", text)
        self.assertIn("环比上升3.25元/兆瓦时", text)
        output = Path(self.tmp.name) / "market.docx"
        document = Document()
        document.add_paragraph(text)
        document.save(output)
        self.assertEqual(Document(output).paragraphs[0].text, text)

    def test_a41_decreases_and_flat(self):
        for wording, expected in (("减少8台", -8), ("增加0台", 0), ("持平", 0)):
            with self.subTest(wording=wording):
                self.write(f"市场开机台数451台，环比{wording}。全省日前出清均价384.33元/兆瓦时，环比下跌21.64元/兆瓦时。")
                data = read_day_ahead_market_deltas(self.path, expected_date="2026-11-10")
                self.assertEqual(data["startupDifference"], expected)
                self.assertEqual(data["priceDifference"], -21.64)

    def test_missing_deltas_do_not_reuse_website_or_nearby_cells(self):
        self.write("日前简析：市场开机台数451台。全省日前出清均价384.33元/兆瓦时。")
        book = load_workbook(self.path)
        book["快报  (日前)"]["A39"] = "市场开机台数451台，环比增加99台。全省日前出清均价384.33元/兆瓦时，环比上涨99元/兆瓦时。"
        book.save(self.path)
        book.close()
        payload = self.payload()
        source = apply_day_ahead_market_deltas(payload, workbook=self.path, log=lambda _: None)
        self.assertIsNone(source["startupDifference"])
        self.assertIsNone(source["priceDifference"])
        self.assertIsNone(payload["marketAnalysisData"]["priceBoard"]["rows"][0]["difference"])

    def test_a41_fields_are_independent(self):
        self.write("市场开机台数451台，环比减少5台。全省日前出清均价384.33元/兆瓦时。")
        source = read_day_ahead_market_deltas(self.path, expected_date="2026-11-10")
        self.assertEqual(source["startupDifference"], -5)
        self.assertIsNone(source["priceDifference"])
        self.write("市场开机台数451台。全省日前出清均价384.33元/兆瓦时，环比持平。")
        source = read_day_ahead_market_deltas(self.path, expected_date="2026-11-10")
        self.assertIsNone(source["startupDifference"])
        self.assertEqual(source["priceDifference"], 0)

    def test_a41_rejects_wrong_date_and_missing_workbook(self):
        self.write("市场开机台数451台，环比增加7台。")
        with self.assertRaisesRegex(ValueError, "运行日"):
            read_day_ahead_market_deltas(self.path, expected_date="2026-11-09")
        for path in (None, self.path.with_name("missing.xlsx")):
            payload = self.payload()
            source = apply_day_ahead_market_deltas(payload, workbook=path, log=lambda _: None)
            self.assertIn("error", source)
            self.assertIsNone(payload["marketAnalysisData"]["priceBoard"]["rows"][0]["difference"])
