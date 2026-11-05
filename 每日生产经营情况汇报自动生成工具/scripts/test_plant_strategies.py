"""三厂策略取自 D 日 A39、粤东段删除、表内负数标红的测试。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_red_marked_report import (
    extract_plant_strategies,
    fill_plant_strategies,
    highlight_negative_numbers,
    is_negative_text,
    load_plant_strategies,
    remove_east_guangdong_section,
    replace_cell_text,
)

SHANTOU = "综合考虑运行日负荷需求变化，同步调整汕头厂策略。各机组日前收益为：93万元、38万元、5万元。"
HAIMEN = "综合研判市场供需、机组燃料变动成本，延续低价欠发增收策略。"
DONGGUAN = "综合考虑运行日负荷需求、全月合同气量，动态调整报价策略。"
EAST = "粤东区域：500kV线路：胪盘乙线检修至12月底，海胪丙线检修（预计至12月28日）。"


class ExtractStrategyTests(unittest.TestCase):
    def test_parses_three_plants_without_seq_prefix(self) -> None:
        text = "\n".join(["日前简析：", "1.运行日统调负荷需求增长。", EAST, f"汕头厂：{SHANTOU}", f"海门厂：{HAIMEN}", f"东莞厂：{DONGGUAN}"])
        result = extract_plant_strategies(text)
        self.assertEqual(set(result), {"汕头厂", "海门厂", "东莞厂"})
        self.assertEqual(result["汕头厂"], SHANTOU)

    def test_strips_number_prefix_on_plant_line(self) -> None:
        # 11.6 的写法是「2汕头厂：…」「3.海门厂：…」，序号没有统一格式。
        text = "\n".join(["日前简析：", "1.运行日统调负荷需求小幅下行。", "2汕头厂：海风出力上涨。", "3.海门厂：受检修影响。", "4.东莞厂：综合考虑。"])
        result = extract_plant_strategies(text)
        self.assertEqual(result["汕头厂"], "海风出力上涨。")
        self.assertEqual(result["海门厂"], "受检修影响。")
        self.assertEqual(result["东莞厂"], "综合考虑。")

    def test_east_line_is_not_treated_as_strategy(self) -> None:
        text = "\n".join(["日前简析：", "1.总述。", EAST, f"汕头厂：{SHANTOU}"])
        result = extract_plant_strategies(text)
        self.assertEqual(set(result), {"汕头厂"})
        self.assertNotIn("胪盘乙线", result["汕头厂"])

    def test_missing_plant_is_absent(self) -> None:
        text = "\n".join(["日前简析：", "1.总述。", f"汕头厂：{SHANTOU}"])
        self.assertEqual(set(extract_plant_strategies(text)), {"汕头厂"})

    def test_empty_input(self) -> None:
        self.assertEqual(extract_plant_strategies(None), {})
        self.assertEqual(extract_plant_strategies(""), {})


class LoadStrategyTests(unittest.TestCase):
    def test_reads_a39(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wb.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "快报  (日前)"
            sheet["A39"] = "\n".join(["日前简析：", "1.总述。", f"汕头厂：{SHANTOU}"])
            workbook.save(path)
            workbook.close()
            self.assertEqual(set(load_plant_strategies(path)), {"汕头厂"})

    def test_missing_sheet_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wb.xlsx"
            workbook = Workbook()
            workbook.active.title = "快报"
            workbook.save(path)
            workbook.close()
            self.assertEqual(load_plant_strategies(path), {})
            self.assertEqual(load_plant_strategies(None), {})

    def test_no_path_falls_back_to_report_date(self) -> None:
        # 命令行未传 --day-ahead-workbook 时也应按报告日自动找到 D 日工作簿。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wb.xlsx"
            workbook = Workbook()
            workbook.active.title = "快报  (日前)"
            workbook.active["A39"] = "\n".join(
                ["日前简析：", "1.总述。", f"汕头厂：{SHANTOU}", f"海门厂：{HAIMEN}"]
            )
            workbook.save(path)
            workbook.close()
            with patch("generate_red_marked_report.find_clearing_workbook", return_value=path) as finder:
                result = load_plant_strategies(None, date_text="2026-11-06")
            finder.assert_called_once_with("2026-11-06")
            self.assertEqual(set(result), {"汕头厂", "海门厂"})

    def test_unresolvable_date_returns_empty(self) -> None:
        with patch(
            "generate_red_marked_report.find_clearing_workbook",
            side_effect=FileNotFoundError("没有对应日期的工作簿"),
        ):
            self.assertEqual(load_plant_strategies(None, date_text="2026-11-06"), {})


def _document() -> Document:
    document = Document()
    for text in (
        "一、机组启停情况",
        "汕头厂：#1、#2机组运行；#3机组备用。",
        "三、现货交易策略",
        "运行日统调负荷需求增长，全天最大14500万千瓦。",
        EAST,
        f"汕头厂：{SHANTOU}",
        f"海门厂：{HAIMEN}",
        f"东莞厂：{DONGGUAN}",
        "四、日清分情况",
    ):
        document.add_paragraph(text)
    return document


class FillAndRemoveTests(unittest.TestCase):
    def test_fills_strategy_without_touching_startup_section(self) -> None:
        document = _document()
        filled = fill_plant_strategies(document, {"汕头厂": "新策略甲。", "海门厂": "新策略乙。"})
        self.assertEqual(filled, ["汕头厂", "海门厂"])
        texts = [p.text.strip() for p in document.paragraphs]
        self.assertIn("汕头厂：新策略甲。", texts)
        self.assertIn("海门厂：新策略乙。", texts)
        # 东莞厂 A39 未提供，保留模板原文。
        self.assertIn(f"东莞厂：{DONGGUAN}", texts)
        # 机组启停段的同名开头不能被当成策略段替换。
        self.assertIn("汕头厂：#1、#2机组运行；#3机组备用。", texts)

    def test_remove_east_section_is_idempotent(self) -> None:
        document = _document()
        self.assertTrue(remove_east_guangdong_section(document))
        self.assertFalse(remove_east_guangdong_section(document))
        self.assertFalse(any(p.text.strip().startswith("粤东区域") for p in document.paragraphs))

    def test_strategy_paragraph_keeps_plant_prefix(self) -> None:
        document = _document()
        fill_plant_strategies(document, {"东莞厂": "新策略丙。"})
        text = next(p.text.strip() for p in document.paragraphs if p.text.strip().startswith("东莞厂："))
        self.assertEqual(text, "东莞厂：新策略丙。")


class NegativeHighlightTests(unittest.TestCase):
    def test_negative_detection(self) -> None:
        for value in ("-1147", "-50.5", "-1"):
            self.assertTrue(is_negative_text(value), value)
        for value in ("0", "-0", "/", "-", "12", "#DIV/0!", ""):
            self.assertFalse(is_negative_text(value), value)

    def test_negative_cell_turns_red(self) -> None:
        document = Document()
        table = document.add_table(rows=1, cols=3)
        for index, value in enumerate(("-772", "0", "145")):
            replace_cell_text(table.rows[0].cells[index], value, highlight_negative=True)
        colors = [
            str(run.font.color.rgb) if run.font.color and run.font.color.rgb else None
            for cell in table.rows[0].cells
            for paragraph in cell.paragraphs
            for run in paragraph.runs
        ]
        self.assertEqual(colors[0], "FF0000")
        self.assertNotEqual(colors[1], "FF0000")
        self.assertNotEqual(colors[2], "FF0000")

    def test_paragraph_negative_numbers_turn_red(self) -> None:
        document = Document()
        paragraph = document.add_paragraph(
            "最低节点电价-50元/MWh，最高节点电价1132.54元/MWh，偏差电量（较日前）31.21万千瓦时。"
        )
        self.assertEqual(highlight_negative_numbers(paragraph), 1)
        self.assertEqual(paragraph.text, "最低节点电价-50元/MWh，最高节点电价1132.54元/MWh，偏差电量（较日前）31.21万千瓦时。")
        reds = [r.text for r in paragraph.runs if r.font.color and r.font.color.rgb and str(r.font.color.rgb).upper() == "FF0000"]
        self.assertEqual(reds, ["-50"])

    def test_ranges_and_dates_are_not_highlighted(self) -> None:
        # 策略段里常见「0-1点」「2026-11-06」这类写法，不能被当成负数。
        for text in ("一套机组在0-1、7-23点开机运行。", "统计日为2026-11-06，无负数。"):
            document = Document()
            paragraph = document.add_paragraph(text)
            self.assertEqual(highlight_negative_numbers(paragraph), 0, text)

    def test_highlight_is_noop_without_negative(self) -> None:
        document = Document()
        paragraph = document.add_paragraph("全天出清均价589.2元/兆瓦时。")
        self.assertEqual(highlight_negative_numbers(paragraph), 0)


if __name__ == "__main__":
    unittest.main()
