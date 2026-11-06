"""Four-/six-unit Haimen templates must preserve all existing report mappings."""
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook
from docx import Document

from generate_red_marked_report import (
    LOCAL_TEMPLATE_PATH,
    clearing_input_cache,
    fill_daily_clearing_table,
    fill_startup_status,
    load_daily_clearing_inputs,
    load_day_ahead_clearing_summary,
    load_online_energy_summary,
    load_online_price_summary,
    load_plant_strategies,
    load_unit_status_texts,
)
from test_daily_clearing_fill import _build_workbook
from test_unit_status import BASE_ROWS


def make_template(path, *, six_units=False):
    _build_workbook(path)
    book = load_workbook(path)
    brief = book["快报"]
    for row, energy, price, online in ((32, 40, 300, 12), (33, 10, 310, 3), (34, 5, 320, 1)):
        brief.cell(row, 7, energy)
        brief.cell(row, 8, price)
        brief.cell(row, 11, online)
    ahead = book.copy_worksheet(brief)
    ahead.title = "快报  (日前)"
    ahead["F7"], ahead["G7"] = 540, 510
    ahead["A39"] = "日前简析：\n汕头厂：策略甲。\n海门厂：策略乙。\n东莞厂：策略丙。"
    basic = book.create_sheet("基础信息")
    for number, values in enumerate(BASE_ROWS, 12):
        for column, value in enumerate(values, 1):
            basic.cell(number, column, value)
    basic["Z37"], basic["Z39"], basic["Z64"] = "清远上网电量（MWh）", "时刻", "合计"
    basic["AA39"] = basic["AC39"] = "计划\n发电量"
    basic["AA64"], basic["AC64"] = 0, 2300
    if six_units:
        for sheet in (brief, ahead):
            sheet.insert_rows(18, 2)
            for number, unit in ((18, "#5机组"), (19, "#6机组")):
                sheet.cell(number, 2, unit)
                for column in (6, 7, 10):
                    sheet.cell(number, column, 9999)  # Must never substitute for Dongguan.
        basic.insert_rows(23, 2)
        basic["B23"], basic["C23"] = "#5", "调试"
        basic["B24"], basic["C24"] = "#6", "调试"
        basic["Z64"], basic["AA64"], basic["AC64"] = "22:00", 0, 100
    book.save(path)
    book.close()


class ClearingTemplateCompatibilityTests(unittest.TestCase):
    def test_word_fields_read_back_for_both_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            for six_units in (False, True):
                with self.subTest(six_units=six_units):
                    source = Path(directory) / f"{six_units}.xlsx"
                    target = Path(directory) / f"{six_units}.docx"
                    make_template(source, six_units=six_units)
                    data = load_daily_clearing_inputs("2026-11-05", source)
                    statuses = load_unit_status_texts("2026-11-07", source)
                    document = Document(LOCAL_TEMPLATE_PATH)
                    self.assertEqual(fill_daily_clearing_table(document.tables[0], data["rows"]), 11)
                    self.assertEqual(fill_startup_status(document, statuses, {}), 4)
                    document.save(target)
                    saved = Document(target)
                    dongguan = next(row for row in saved.tables[0].rows
                                    if row.cells[0].text.replace(" ", "") == "东莞")
                    self.assertEqual(dongguan.cells[4].text, "90")
                    self.assertEqual(dongguan.cells[5].text, "530")
                    has_new_units = any("#5、#6机组调试" in p.text for p in saved.paragraphs)
                    self.assertEqual(has_new_units, six_units)

    def test_both_templates_keep_energy_prices_daily_rows_and_strategies(self):
        with tempfile.TemporaryDirectory() as directory:
            results = []
            for six_units in (False, True):
                with self.subTest(six_units=six_units):
                    path = Path(directory) / f"{six_units}.xlsx"
                    make_template(path, six_units=six_units)
                    original = path.read_bytes()
                    with clearing_input_cache():
                        ahead = load_day_ahead_clearing_summary("2026-11-07", path)
                        prices = load_online_price_summary("2026-11-06", path)
                        statuses = load_unit_status_texts("2026-11-07", path)
                    online = load_online_energy_summary("2026-11-06", path)
                    daily = load_daily_clearing_inputs("2026-11-05", path)
                    strategies = load_plant_strategies(path)
                    self.assertEqual(ahead["companies"]["东莞"], {"energy": 90, "price": 530})
                    self.assertEqual(ahead["totalEnergy"], 825)
                    self.assertEqual(ahead["companies"]["清远"]["energy"], 230)
                    row = 66 if six_units else 64
                    self.assertEqual(ahead["qingyuanSource"]["cells"], {f"AA{row}": 0, f"AC{row}": 2300})
                    self.assertEqual(prices["companies"]["东莞"], 530)
                    self.assertEqual(online["companies"]["东莞"], 100)
                    self.assertEqual(online["total"], 621)
                    if six_units:
                        self.assertIn("#5、#6机组调试", statuses["海门"])
                    else:
                        self.assertNotIn("#5", statuses["海门"])
                    self.assertEqual(strategies, {"汕头厂": "策略甲。", "海门厂": "策略乙。", "东莞厂": "策略丙。"})
                    self.assertEqual(path.read_bytes(), original)
                    results.append((ahead["companies"], prices["companies"], online["companies"], daily["rows"]))
            self.assertEqual(results[0], results[1])

    def test_missing_total_is_not_replaced_by_hourly_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-total.xlsx"
            make_template(path, six_units=True)
            book = load_workbook(path)
            book["基础信息"]["Z66"] = None
            book.save(path)
            book.close()
            with self.assertRaisesRegex(KeyError, "计划发电量合计行"):
                load_day_ahead_clearing_summary("2026-11-07", path)

    def test_missing_plant_label_does_not_use_the_old_row_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-plant.xlsx"
            make_template(path, six_units=True)
            book = load_workbook(path)
            for name in ("快报", "快报  (日前)"):
                book[name]["A21"] = None
            book.save(path)
            book.close()
            for load in (load_day_ahead_clearing_summary, load_online_price_summary, load_online_energy_summary):
                with self.subTest(load=load.__name__), self.assertRaisesRegex(KeyError, "东莞"):
                    load("2026-11-07", path)
