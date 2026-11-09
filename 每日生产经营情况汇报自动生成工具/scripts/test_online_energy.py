from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from docx import Document
from docx.shared import RGBColor

from generate_red_marked_report import (
    build_online_energy_text,
    load_online_energy_summary,
    load_online_price_summary,
    fill_online_completion,
)


def _build_workbook(path: Path, *, fire: tuple[float, float, float], qingyuan: float,
                    offshore: float, tuolian: float, guihu: float,
                    prices: tuple[float, float, float] = (1629, 3479, 847)) -> None:
    workbook = Workbook()
    brief = workbook.active
    brief.title = "快报"
    shantou, haimen, dongguan = fire
    total = shantou + haimen + dongguan + qingyuan
    for row, energy in ((7, total), (8, shantou), (13, haimen), (19, dongguan), (25, qingyuan)):
        brief.cell(row, 1, {7: "分公司", 8: "汕头", 13: "海门", 19: "东莞", 25: "清远"}[row])
        if row != 7:
            brief.cell(row, 2, "合计")
        brief.cell(row, 10, energy)
    brief.cell(28, 1, "新能源")
    for row, label, energy in (
        (31, "分公司", offshore + tuolian + guihu),
        (32, "汕头海风", offshore),
        (33, "鮀莲光伏", tuolian),
        (34, "归湖光伏", guihu),
    ):
        brief.cell(row, 1, label)
        brief.cell(row, 11, energy)

    day_ahead = workbook.create_sheet("快报  (日前)")
    for row, label in ((7, "分公司"), (8, "汕头"), (13, "海门"), (19, "东莞")):
        day_ahead.cell(row, 1, label)
    day_ahead.cell(7, 7, 300)
    for row, price in ((8, prices[0]), (13, prices[1]), (19, prices[2])):
        day_ahead.cell(row, 7, price)
    for row, label, price in ((32, "汕头海风", 676), (33, "鮀莲光伏", 598), (34, "归湖光伏", 576)):
        day_ahead.cell(row, 1, label)
        day_ahead.cell(row, 8, price)
    workbook.save(path)


class OnlineEnergyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "出清情况（分公司2026.11.4）.xlsx"

    def test_splits_fire_renewable_and_qingyuan_from_brief(self) -> None:
        # 11.4 实际值：火电三厂 1629.02/3478.60/847.48、清远 230.57、
        # 新能源 348.70/32.54/18.48，总数 6585.39。
        _build_workbook(self.path, fire=(1629.02, 3478.60, 847.48), qingyuan=230.571,
                        offshore=348.70, tuolian=32.54, guihu=18.48)
        energy = load_online_energy_summary("2026-11-04", self.path)

        self.assertAlmostEqual(energy["companies"]["汕头"], 1629.02)
        self.assertAlmostEqual(energy["companies"]["清远"], 230.571)
        self.assertAlmostEqual(energy["companies"]["光伏"], 51.02)
        self.assertAlmostEqual(energy["renewableEnergy"], 399.72)
        self.assertAlmostEqual(energy["total"], 6585.391, places=3)

    def test_text_reports_every_unit_and_price(self) -> None:
        _build_workbook(self.path, fire=(1629.02, 3478.60, 847.48), qingyuan=230.571,
                        offshore=348.70, tuolian=32.54, guihu=18.48)
        energy = load_online_energy_summary("2026-11-04", self.path)
        prices = load_online_price_summary("2026-11-04", self.path)

        text = build_online_energy_text("2026-11-04", energy, prices)

        self.assertTrue(text.startswith("11月4日分公司上网电量6585万千瓦时，其中，汕头1629万、"
                                        "海门3479万、东莞847万、海上风电349万、光伏51万千瓦时、"
                                        "清远231万千瓦时；"))
        self.assertIn("分公司日前均价300厘/千瓦时，其中汕头1629厘、海门3479厘、东莞847厘、"
                      "海上风电676厘、鮀莲598厘、归湖576厘/千瓦时。", text)

    def test_zero_realtime_sheet_yields_zero_total(self) -> None:
        # 报告日当天的工作簿上网电量列尚未出数，调用方只将电量标记待补。
        _build_workbook(self.path, fire=(0, 0, 0), qingyuan=0, offshore=0, tuolian=0, guihu=0)
        energy = load_online_energy_summary("2026-11-05", self.path)

        self.assertEqual(energy["total"], 0)

    def test_missing_energy_updates_prices_and_explicit_colors_after_reopen(self) -> None:
        _build_workbook(self.path, fire=(0, 0, 0), qingyuan=0, offshore=0, tuolian=0, guihu=0,
                        prices=(409.57, -50, 434.26))
        prices = load_online_price_summary("2026-11-09", self.path)
        for color in ("000000", "FF0000"):
            for energy in (None, load_online_energy_summary("2026-11-09", self.path)):
                with self.subTest(template_color=color, energy=energy):
                    doc = Document()
                    paragraph = doc.add_paragraph()
                    old = paragraph.add_run("11月3日分公司上网电量6228万千瓦时；分公司日前均价160厘/千瓦时。")
                    old.font.color.rgb = RGBColor.from_string(color)
                    old.font.name = "仿宋"
                    fill_online_completion(paragraph, "2026-11-09", energy, prices)
                    output = self.path.with_suffix(".docx")
                    doc.save(output)
                    actual = Document(output).paragraphs[0]
                    self.assertEqual(actual.runs[0].text, "11月9日分公司上网电量待补；")
                    self.assertEqual(str(actual.runs[0].font.color.rgb), "FF0000")
                    self.assertEqual(str(actual.runs[1].font.color.rgb), "000000")
                    self.assertEqual(actual.runs[1].font.name, "仿宋")
                    self.assertIn("分公司日前均价300厘/千瓦时，其中汕头410厘、海门-50厘、东莞434厘", actual.text)
                    self.assertNotIn("6228", actual.text)
                    self.assertNotIn("160厘", actual.text)

    def test_available_energy_and_prices_are_both_black(self) -> None:
        _build_workbook(self.path, fire=(100, 200, 300), qingyuan=40, offshore=50, tuolian=6, guihu=7)
        energy = load_online_energy_summary("2026-11-09", self.path)
        prices = load_online_price_summary("2026-11-09", self.path)
        doc = Document()
        paragraph = doc.add_paragraph("旧内容")
        paragraph.runs[0].font.color.rgb = RGBColor(255, 0, 0)
        fill_online_completion(paragraph, "2026-11-09", energy, prices)
        self.assertEqual(paragraph.text, build_online_energy_text("2026-11-09", energy, prices))
        self.assertNotIn("待补", paragraph.text)
        self.assertTrue(all(str(run.font.color.rgb) == "000000" for run in paragraph.runs))


if __name__ == "__main__":
    unittest.main()
