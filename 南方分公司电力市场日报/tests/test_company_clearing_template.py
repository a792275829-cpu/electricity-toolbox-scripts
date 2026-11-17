"""Four/six-unit recap compatibility, independent of business workbooks."""
from datetime import date, time
from pathlib import Path
import sys
import tempfile
import unittest

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "南方分公司电力市场日报")]
from gd_power_report import company_clearing_xlsx as reader
from gd_power_report.legacy_concise_report import prepare_legacy_concise_context
from gd_power_report.legacy_print_report import prepare_print_context
from gd_power_report.realtime_unit_xlsx import EXPECTED_UNITS, parse_realtime_unit_workbook


def template(six):
    book = Workbook()
    sheet = book.active
    sheet.title = "新"
    shift = 2 if six else 0
    sheet["A6"] = date(2026, 11, 8)
    sheet["A6"].number_format = '"分公司日前出清复盘"yyyy/m/d'
    sheet["A7"] = "单位：万千瓦时、元/MWh、万元"
    sheet["A8"] = "南方分公司"
    for coordinate, value in {"D8": "中长期", "G8": "日前出清", "G9": "电量", "H9": "电价",
                              "A10": "火电机组合计", "A16": "海门"}.items():
        sheet[coordinate] = value
    sheet.merge_cells("G8:H8")
    sheet.merge_cells(f"A16:A{20 + shift}")
    for number in range(1, 7 if six else 5):
        row = 16 + number
        sheet.cell(row, 2, f"#{number}机组")
        sheet.cell(row, 7, "/" if number >= 5 else number * 100)
        sheet.cell(row, 8, "/" if number >= 5 else number * 10)
        sheet.cell(row, 17, -3.8227 if number == 5 else 0).number_format = "0.0000"
    sheet.cell(22 + shift, 1, "东莞")
    sheet.cell(27 + shift, 1, "清远")
    sheet.cell(30 + shift, 1, "新能源")
    sheet.cell(32 + shift, 1, "分公司")
    sheet.cell(33 + shift, 1, "汕头海风")
    sheet.cell(34 + shift, 1, "鮀莲光伏")
    sheet.cell(35 + shift, 1, "归湖光伏")
    for column, title in ((2, "火电分时明细"), (12, "新能源分时明细")):
        sheet.cell(41 + shift, column, date(2026, 11, 8)).number_format = f'"{title}"yyyy/m/d'
    sheet.cell(42 + shift, 1, "时刻")
    for hour in range(24):
        sheet.cell(46 + shift + hour, 1, f"{hour}:00")
        sheet.cell(46 + shift + hour, 2, hour + 1)
        sheet.cell(46 + shift + hour, 3, 300)
    sheet.cell(70 + shift, 1, "合计")
    sheet.cell(71 + shift, 1, "二、实时市场简析")
    sheet.cell(72 + shift, 1, date(2026, 11, 6))
    sheet.cell(72 + shift, 1).number_format = '"分公司实时出清复盘"yyyy/m/d'
    sheet.cell(73 + shift, 1, "单位：万千瓦时、元/MWh、万元")
    for row, column, value in ((74, 6, "实时"), (75, 6, "电量"), (75, 7, "电价"),
                                (74, 16, "电能均价")):
        sheet.cell(row + shift, column, value)
    for row, label, energy, price in ((76, "分公司火电", 100, 200),
                                      (85, "分公司新能源", 300, 400),
                                      (88, "归湖", 0, 0)):
        sheet.cell(row + shift, 1, label)
        sheet.cell(row + shift, 6, energy)
        sheet.cell(row + shift, 16, price)
    return book


class TemplateCompatibilityTests(unittest.TestCase):
    def test_province_price_comparison_survives_wording_merge_and_render(self):
        from gd_power_report.pipeline import _merge_market_text_cards
        from gd_power_report.report import _prepare_market_cards

        for wording, expected in (("降低", -5.45), ("下降", -5.45),
                                  ("减少", -5.45), ("上涨", 5.45),
                                  ("上升", 5.45), ("增加", 5.45)):
            for realtime in (False, True):
                with self.subTest(wording=wording, realtime=realtime):
                    period = "实时" if realtime else "日前"
                    prefix = "较日前" if realtime else "环比"
                    card = reader._market_average_price_card(
                        f"全省{period}出清均价436.05元/兆瓦时，{prefix}{wording}5.45元/兆瓦时。",
                        realtime=realtime,
                    )
                    self.assertEqual((card["value"], card["delta"]), (436.05, expected))
                    merged = _merge_market_text_cards([], [card], {card["label"]})
                    visible = _prepare_market_cards(merged, realtime=realtime)[0]
                    self.assertEqual(visible["delta_text"], f"{'▼' if expected < 0 else '▲'} 5.45")
                    self.assertEqual(visible["delta_prefix"], prefix)

        card = reader._market_average_price_card("全省日前出清均价436.05元/兆瓦时。")
        self.assertIsNone(card["delta"])

    def test_six_unit_strategy_profits_and_placeholders(self):
        for count in (4, 6):
            with self.subTest(count=count):
                book = template(True)
                sheet = book["新"]
                sheet["B16"] = "合计"
                sheet["J8"], sheet["J9"] = "日前偏差", "边际贡献"
                for number in range(1, 7):
                    sheet.cell(16 + number, 10, number * 10 if number <= count else "/")
                strategy = {"plant": "海门厂", "text": "各机组日前收益分别为：" + "、".join(["999万元"] * count) + "。"}
                expected = "各机组日前收益分别为：" + "、".join(f"{n * 10}万元" for n in range(1, count + 1)) + "。"
                self.assertEqual(reader._sync_strategy_profits([strategy], sheet, date(2026, 11, 8))[0]["text"], expected)
                sheet.cell(18, 10).value = "/"
                self.assertEqual(reader._sync_strategy_profits([strategy], sheet, date(2026, 11, 8)), [strategy])
                sheet.cell(18, 10).value = "#VALUE!"
                self.assertEqual(reader._sync_strategy_profits([strategy], sheet, date(2026, 11, 8)), [strategy])
                book.close()

    def test_ambiguous_profit_rows_and_mismatched_dates_keep_source_text(self):
        book = template(True)
        sheet = book["新"]
        sheet["J8"], sheet["J9"] = "日前偏差", "边际贡献"
        for number in range(1, 7):
            sheet.cell(16 + number, 10, number * 10)
        strategy = {"plant": "海门厂", "text": "各机组日前收益分别为：1万元、2万元、3万元、4万元。"}
        self.assertEqual(reader._sync_strategy_profits([strategy], sheet, date(2026, 11, 8)), [strategy])
        sheet["B18"] = "#1机组"
        self.assertEqual(reader._sync_strategy_profits([strategy], sheet, date(2026, 11, 8)), [strategy])
        self.assertEqual(reader._sync_strategy_profits([strategy], sheet, date(2026, 11, 7)), [strategy])
        book.close()

    def test_excel_time_values_preserve_full_day_tail_after_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            book = template(True)
            sheet = book["新"]
            for hour in range(24):
                sheet.cell(48 + hour, 1).value = time(hour)
            path = Path(folder) / "time.xlsx"
            book.save(path)
            book.close()
            from openpyxl import load_workbook
            reopened = load_workbook(path)
            self.assertEqual(reader._time_rows(reopened["新"]), list(range(48, 72)))
            self.assertEqual(reader._appendix_ranges(reopened["新"]), ("A6:Q72", "A74:Q90"))
            reopened.close()

    def test_legacy_narrative_aliases(self):
        for title in ("日前简析", "日前市场简析"):
            with self.subTest(title=title):
                book = Workbook()
                sheet = book.active
                sheet.title = "快报v2"
                sheet["A4"], sheet["A68"], sheet["Q4"] = title, "实时市场简析", "表头"
                self.assertIs(reader._select_report_sheet(book), sheet)
                sheet["A4"] = None
                sheet["A5"] = title + "：内容"
                self.assertEqual(reader._section_text(sheet, reader.DAY_NARRATIVE_MARKERS, "A4"), title + "：内容")
                book.close()

    def test_complete_four_and_six_unit_tables_reach_both_renderers(self):
        for six in (False, True):
            with self.subTest(six=six), tempfile.TemporaryDirectory() as folder:
                book = template(six)
                shift = 2 if six else 0
                self.assertEqual(reader._appendix_ranges(book["新"]),
                                 (f"A6:Q{70 + shift}", f"A{72 + shift}:Q{88 + shift}"))
                path = Path(folder) / "收益表.xlsx"
                book.save(path)
                book.close()
                report = reader.parse_daily_report_workbook(path)
                self.assertEqual(report["realtime_date"], "2026-11-06")
                self.assertEqual(len(report["day_ahead"]["series"]["energy"]), 24)
                for context in (prepare_legacy_concise_context(report), prepare_print_context(report)):
                    rows = [row for block in context["day_blocks"] if block["block_kind"] == "day-recap"
                            for row in block["rows"]]
                    unit_rows = {cell["value"]: row for row in rows for cell in row["cells"]
                                 if cell["value"] in {"#5机组", "#6机组"}}
                    self.assertEqual(set(unit_rows), {"#5机组", "#6机组"} if six else set())
                    for row in unit_rows.values():
                        self.assertEqual(row["group_key"], "haimen")
                        self.assertIn("/", [cell["value"] for cell in row["cells"]])
                    labels = [cell["value"] for row in rows for cell in row["cells"]]
                    self.assertIn("东莞", labels)
                    self.assertIn("归湖光伏", labels)

    def test_realtime_prices_follow_labels_after_extra_rows(self):
        for six in (False, True):
            with self.subTest(six=six):
                book = template(six)
                sheet = book["新"]
                sheet.insert_rows(71 + (2 if six else 0), 5)
                # Stale coordinates must not override the relocated summaries.
                sheet["F72"], sheet["P72"] = 99999, 99999
                metrics = reader._energy_comparison_metrics(book)
                self.assertEqual(metrics["realtime_price"], 350)
                self.assertEqual(metrics["realtime_fee"], 140)
                sheet["A90" if not six else "A92"] = "分公司火电"
                self.assertIsNone(reader._energy_comparison_metrics(book)["realtime_price"])
                book.close()

    def test_optional_hourly_units_and_shifted_realtime_date(self):
        for optional in (False, True):
            with self.subTest(optional=optional), tempfile.TemporaryDirectory() as folder:
                book = template(True)
                # A former fixed date coordinate belongs to the day grid now.
                book["新"]["A68"] = date(2026, 11, 8)
                for plant, units in EXPECTED_UNITS.items():
                    sheet = book.create_sheet(plant)
                    names = list(units)
                    if optional and plant == "海门":
                        names += ["5号机", "6号机"]
                    for index, name in enumerate(names):
                        start = index * 30 + 1
                        sheet.cell(start, 1, name)
                        for column, label in enumerate(("时刻", "日前出清", "实时出清", "实际上网电量"), 1):
                            sheet.cell(start + 1, column, label)
                        for hour in range(24):
                            sheet.cell(start + 3 + hour, 1, f"{hour}:00")
                            for column in range(2, 5):
                                sheet.cell(start + 3 + hour, column, index + 1)
                path = Path(folder) / "收益表.xlsx"
                book.save(path)
                book.close()
                parsed = parse_realtime_unit_workbook(path, date(2026, 11, 6))
                haimen = next(plant for plant in parsed["plants"] if plant["plant"] == "海门")
                self.assertEqual(len(haimen["units"]), 6 if optional else 4)
                self.assertEqual(len(haimen["units"][-1]["hours"]), 24)
                with self.assertRaisesRegex(ValueError, "应为2026-11-07"):
                    parse_realtime_unit_workbook(path, date(2026, 11, 7))
                if optional:
                    book["海门"].cell(155, 1, "0:00")
                    book.save(path)
                    with self.assertRaisesRegex(ValueError, "海门6号机重复小时0:00"):
                        parse_realtime_unit_workbook(path, date(2026, 11, 6))
                    book["海门"].cell(155, 1, "1:00")
                    book["海门"].cell(152, 3).value = None
                    book.save(path)
                    with self.assertRaisesRegex(ValueError, "海门缺少机组块：6号机"):
                        parse_realtime_unit_workbook(path, date(2026, 11, 6))


if __name__ == "__main__":
    unittest.main()
