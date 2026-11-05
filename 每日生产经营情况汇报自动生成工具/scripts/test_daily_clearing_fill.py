from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from docx import Document
from openpyxl import Workbook

from generate_red_marked_report import (
    LOCAL_TEMPLATE_PATH,
    build_spot_deviation_texts,
    fill_daily_clearing_table,
    fill_spot_deviation_analysis,
    load_daily_clearing_inputs,
)


def _build_workbook(path: Path) -> None:
    workbook = Workbook()
    brief = workbook.active
    brief.title = "快报"

    fire_rows = {
        7: ("分公司", None),
        8: ("汕头", "合计"),
        13: ("海门", "合计"),
        19: ("东莞", "合计"),
        25: ("清远", "合计"),
    }
    for row, (label, sublabel) in fire_rows.items():
        brief.cell(row, 1, label)
        if sublabel:
            brief.cell(row, 2, sublabel)
    for row, sublabel in ((9, "#1机组"), (10, "#2机组"), (11, "#3机组")):
        brief.cell(row, 2, sublabel)

    # 分公司行：总电费/容量电费/上网电量/综合电价
    brief.cell(7, 13, 350)
    brief.cell(7, 31, 12)
    brief.cell(7, 10, 605)
    brief.cell(7, 12, 640)
    brief.cell(7, 15, 19)

    def fire(row: int, *, long_term, price, day, day_price, real, real_price, online,
             fee, capacity, composite, long_income, spot) -> None:
        brief.cell(row, 4, long_term)
        brief.cell(row, 5, price)
        brief.cell(row, 6, day)
        brief.cell(row, 7, day_price)
        brief.cell(row, 8, real)
        brief.cell(row, 9, real_price)
        brief.cell(row, 10, online)
        brief.cell(row, 13, fee)
        brief.cell(row, 15, long_income)
        brief.cell(row, 27, spot)
        brief.cell(row, 12, composite)
        brief.cell(row, 31, capacity)

    fire(8, long_term=100, price=400, day=200, day_price=500, real=210, real_price=600,
         online=205, fee=100, capacity=5, composite=700, long_income=4, spot=10)
    fire(13, long_term=150, price=410, day=250, day_price=520, real=260, real_price=610,
         online=300, fee=200, capacity=6, composite=600, long_income=8, spot=99)
    fire(19, long_term=60, price=420, day=90, day_price=530, real=95, real_price=620,
         online=100, fee=50, capacity=1, composite=650, long_income=5, spot=30)
    fire(25, long_term=40, price=430, day=None, day_price=None, real=None, real_price=None,
         online=None, fee=10, capacity=0, composite=400, long_income=2, spot=None)
    brief.cell(25, 10).value = "/"
    brief.cell(25, 6).value = "/"

    # 海门的现货偏差收益取三项偏差边际贡献之和（R + U + X）
    brief.cell(13, 18, 20)
    brief.cell(13, 21, -2)
    brief.cell(13, 24, 3)

    brief.cell(28, 1, "新能源")
    brief.cell(28, 2, "市场出清及日清分")
    brief.cell(31, 1, "分公司")
    brief.cell(32, 1, "汕头海风")
    brief.cell(33, 1, "鮀莲光伏")
    brief.cell(34, 1, "归湖光伏")
    brief.cell(31, 3, 10)
    brief.cell(31, 5, 20)
    brief.cell(31, 6, 372)
    brief.cell(31, 7, 30)
    brief.cell(31, 8, 100)
    brief.cell(31, 9, 25)
    brief.cell(31, 10, 200)
    brief.cell(31, 11, 100)
    brief.cell(31, 13, 300)
    brief.cell(31, 14, -3)
    brief.cell(31, 15, 5)
    brief.cell(31, 16, -2)
    brief.cell(31, 19, 7)
    brief.cell(31, 20, -4)
    brief.cell(31, 21, 900)
    brief.cell(31, 22, -36)
    brief.cell(31, 23, -1)
    brief.cell(31, 24, 500)
    brief.cell(31, 25, -2)
    brief.cell(31, 28, -9)

    company = workbook.create_sheet("分公司")
    company.cell(6, 1, "分公司火电")
    company.cell(6, 2, 504.6062)
    company.cell(6, 6, 190.97)
    company.cell(6, 7, 5794.5917)
    company.cell(6, 8, 278.93)
    company.cell(6, 11, 145.43)
    company.cell(6, 13, -93.45)
    company.cell(6, 14, 44.71)

    for name, low, high in (("汕头", 700, 765.49), ("海门", -60, 900), ("东莞", -50, 800)):
        sheet = workbook.create_sheet(name)
        # 日前/实时两组标签分列不同行，值刻意不同：报告句取实时行，不能误取日前行。
        sheet.cell(12, 3, "日前最低价")
        sheet.cell(12, 4, low - 10)
        sheet.cell(13, 3, "日前最高价")
        sheet.cell(13, 4, high + 300)
        sheet.cell(14, 3, "实时最低价")
        sheet.cell(14, 4, low)
        sheet.cell(15, 3, "实时最高价")
        sheet.cell(15, 4, high)

    workbook.save(path)


class DailyClearingFillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.workbook_path = Path(self._directory.name) / "出清情况（分公司2026.11.2）.xlsx"
        _build_workbook(self.workbook_path)
        self.data = load_daily_clearing_inputs("2026-11-02", self.workbook_path)
        self.rows = {(label, sub): values for label, sub, values in self.data["rows"]}

    def test_company_spot_income_sums_plants(self) -> None:
        # 汕头 10 + 海门 (20 - 2 + 3) + 东莞 30 = 61；分公司自身 AA 列 99 不参与
        self.assertEqual(self.rows[("分公司火电", None)][20], "61")
        self.assertEqual(self.rows[("汕头", None)][20], "10")
        self.assertEqual(self.rows[("海门", None)][20], "21")
        self.assertEqual(self.rows[("东莞", None)][20], "30")
        self.assertEqual(self.rows[("清远", None)][20], "/")

    def test_company_total_fee_and_headers_follow_plant_sum(self) -> None:
        values = self.rows[("分公司火电", None)]
        self.assertEqual(values[22], "360")  # 100 + 200 + 50 + 10
        self.assertEqual(values[19], "19")
        self.assertEqual(values[21], "80")  # 61 + 19
        self.assertEqual(values[9], "605")
        self.assertEqual(values[23], "559")  # (350-12)/605*1000
        self.assertEqual(values[24], "640")

    def test_qingyuan_row_keeps_slash_and_zero_price(self) -> None:
        values = self.rows[("清远", None)]
        self.assertEqual(values[9], "/")
        self.assertEqual(values[20], "/")
        self.assertEqual(values[21], "2")
        self.assertEqual(values[23], "0")
        self.assertEqual(values[24], "400")

    def test_renewable_row_uses_mechanism_columns(self) -> None:
        values = self.rows[("分公司新能源", None)]
        self.assertEqual(values[9], "100")
        self.assertEqual(values[10], "10")
        self.assertEqual(values[11], "5")
        self.assertEqual(values[12], "7")
        self.assertEqual(values[21], "-11")
        self.assertEqual(values[23], "-80")  # (-3 - 5) / 100 * 1000

    def test_spot_deviation_texts_use_company_sheet(self) -> None:
        spot = self.data["spot"]
        self.assertAlmostEqual(spot["loadRate"], 47.8, places=3)
        # 节点电价取「实时最低价／实时最高价」两行；若误取日前行会是 -70／1200。
        self.assertEqual(spot["minPrice"], -60)
        self.assertEqual(spot["maxPrice"], 900)
        realtime, actual = build_spot_deviation_texts(spot)
        self.assertTrue(realtime.startswith("南方分公司实时出清电量5795万千瓦时"))
        self.assertIn("偏差电量（较日前）145.43万千瓦时", realtime)
        self.assertIn("平均运行负荷率47.8%", realtime)
        self.assertIn("最低节点电价-60元/MWh，最高节点电价900元/MWh", realtime)
        self.assertIn("加权均价为278.93元/MWh（日前：190.97元/MWh）", realtime)
        self.assertEqual(actual, "南方分公司火电机组实际上网偏差电量为-93万千瓦时，实现45万元增收。")

    def test_realtime_not_cleared_skips_spot_section(self) -> None:
        # 报告日当天的工作簿被误当 D-2：实时电量/偏差/实际偏差/收益全为 0，
        # 应判定数据未出、不生成现货偏差段落，但表1 照常填。
        from openpyxl import load_workbook

        target = Path(self._directory.name) / "出清情况（分公司2026.11.5）.xlsx"
        shutil.copy(self.workbook_path, target)
        book = load_workbook(target)
        sheet = book["分公司"]
        for column in (7, 11, 13, 14):
            sheet.cell(6, column, 0)
        book.save(target)
        book.close()

        data = load_daily_clearing_inputs("2026-11-05", target)

        self.assertIsNone(data["spot"])
        self.assertIn("实时出清数据为 0", data["spotSkipReason"])
        self.assertEqual(len(data["rows"]), 11)

    def test_table_and_spot_paragraphs_are_written_to_template(self) -> None:
        target = Path(self._directory.name) / "report.docx"
        shutil.copy(LOCAL_TEMPLATE_PATH, target)
        document = Document(target)
        self.assertEqual(fill_daily_clearing_table(document.tables[0], self.data["rows"]), 11)
        self.assertTrue(fill_spot_deviation_analysis(
            document, build_spot_deviation_texts(self.data["spot"])))
        document.save(target)

        saved = Document(target)
        table = saved.tables[0]
        company_row = next(
            row for row in table.rows
            if row.cells[0].text.replace(" ", "") == "分公司火电"
        )
        self.assertEqual(company_row.cells[19].text.strip(), "61")
        self.assertEqual(company_row.cells[21].text.strip(), "360")
        texts = [paragraph.text.strip() for paragraph in saved.paragraphs]
        self.assertIn("日前-实时：", texts)
        label = texts.index("实际-实时：")
        self.assertIn("实现45万元增收", texts[label + 1])

    def test_spot_paragraphs_are_not_red(self) -> None:
        # 现货偏差分析两段正文不标红负数，只有日清分表与市场分析段标红。
        target = Path(self._directory.name) / "report_plain.docx"
        shutil.copy(LOCAL_TEMPLATE_PATH, target)
        document = Document(target)
        fill_spot_deviation_analysis(document, build_spot_deviation_texts(self.data["spot"]))
        document.save(target)

        saved = Document(target)
        texts = [paragraph.text.strip() for paragraph in saved.paragraphs]
        for heading in ("日前-实时：", "实际-实时："):
            body = saved.paragraphs[texts.index(heading) + 1]
            reds = [
                run.text
                for run in body.runs
                if run.font.color and run.font.color.rgb
                and str(run.font.color.rgb).upper() == "FF0000"
            ]
            self.assertEqual(reds, [], f"{heading} 正文不应标红")


if __name__ == "__main__":
    unittest.main()
