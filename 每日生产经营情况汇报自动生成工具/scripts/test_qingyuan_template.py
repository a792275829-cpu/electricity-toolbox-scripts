from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from docx import Document
from openpyxl import Workbook

from generate_red_marked_report import (
    DAILY_CLEARING_REQUIRED_HEADERS,
    LOCAL_TEMPLATE_PATH,
    QINGYUAN_DAILY_CLEARING_VALUES,
    build_day_ahead_clearing_text,
    clear_auto_section_red,
    ensure_daily_clearing_table_structure,
    ensure_qingyuan_template_content,
    ensure_spot_deviation_analysis,
    is_current_daily_clearing_table,
    load_day_ahead_clearing_summary,
    startup_date_paragraphs,
)


class QingyuanTemplateTests(unittest.TestCase):
    def test_qingyuan_content_is_complete_and_idempotent(self) -> None:
        document = Document(LOCAL_TEMPLATE_PATH)
        ensure_qingyuan_template_content(document)
        ensure_qingyuan_template_content(document)

        dates = startup_date_paragraphs(document)
        self.assertGreaterEqual(len(dates), 2)
        for date_paragraph in dates[:2]:
            start = next(
                index
                for index, paragraph in enumerate(document.paragraphs)
                if paragraph._element is date_paragraph._element
            ) + 1
            following = document.paragraphs[start:]
            next_boundary = next(
                (
                    index
                    for index, paragraph in enumerate(following)
                    if re.fullmatch(r"(?:\d+月)?\d+日：", paragraph.text.strip())
                    or paragraph.text.strip() == "二、分公司电量、电价完成情况"
                ),
                len(following),
            )
            block = following[:next_boundary]
            self.assertEqual(
                sum(paragraph.text.strip().startswith("清远厂：") for paragraph in block),
                1,
            )

        qingyuan_rows = [
            row
            for row in document.tables[0].rows
            if re.sub(r"\s+", "", row.cells[0].text) == "清远"
        ]
        self.assertEqual(len(qingyuan_rows), 1)
        self.assertEqual(
            [cell.text.strip() for cell in qingyuan_rows[0].cells],
            QINGYUAN_DAILY_CLEARING_VALUES,
        )
        table = document.tables[0]
        self.assertTrue(is_current_daily_clearing_table(table))
        self.assertEqual(len(table.columns), 24)
        header_text = "".join(cell.text.replace("\n", "") for row in table.rows[:2] for cell in row.cells)
        for header in DAILY_CLEARING_REQUIRED_HEADERS:
            self.assertIn(header, header_text)

        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
        bias_index = paragraphs.index("3.现货偏差分析")
        forecast_index = paragraphs.index("五、短期市场预测")
        self.assertLess(bias_index, forecast_index)
        self.assertEqual(paragraphs[bias_index + 1], "日前-实时：")
        self.assertEqual(paragraphs[bias_index + 3], "实际-实时：")

    def test_legacy_structure_is_upgraded_from_canonical_template(self) -> None:
        document = Document()
        document.add_table(rows=1, cols=1)
        document.add_paragraph("五、短期市场预测")

        ensure_daily_clearing_table_structure(document)
        ensure_spot_deviation_analysis(document)

        self.assertTrue(is_current_daily_clearing_table(document.tables[0]))
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
        self.assertLess(paragraphs.index("3.现货偏差分析"), paragraphs.index("五、短期市场预测"))

    def test_recent_completion_descriptions_include_qingyuan(self) -> None:
        document = Document(LOCAL_TEMPLATE_PATH)
        heading_index = next(
            index
            for index, paragraph in enumerate(document.paragraphs)
            if paragraph.text.strip() == "1.近两日完成情况"
        )
        online_text = document.paragraphs[heading_index + 1].text
        day_ahead_text = document.paragraphs[heading_index + 2].text
        self.assertIn("清远", online_text)
        self.assertIn("清远（计划）", day_ahead_text)

        company = {"energy": 0, "price": 0}
        summary = {
            "totalEnergy": 0,
            "averagePrice": 0,
            "companies": {
                "汕头": company,
                "海门": company,
                "东莞": company,
                "海上风电": company,
                "鮀莲": company,
                "归湖": company,
                "清远": company,
            },
        }
        generated = build_day_ahead_clearing_text("2026-08-12", summary)
        self.assertIn("、清远（计划）0万千瓦时；", generated)

    def test_auto_generated_sections_lose_red_heading(self) -> None:
        def red_runs(paragraph) -> list[str]:
            return [
                run.text
                for run in paragraph.runs
                if run.font.color and run.font.color.rgb
                and str(run.font.color.rgb).upper() == "FF0000"
            ]

        document = Document(LOCAL_TEMPLATE_PATH)
        strategy = next(p for p in document.paragraphs if p.text.strip() == "三、现货交易策略")
        bias = next(p for p in document.paragraphs if p.text.strip() == "3.现货偏差分析")
        self.assertTrue(red_runs(strategy))
        self.assertTrue(red_runs(bias))

        # 现货偏差分析取数失败时保留红字，提示仍需人工补数。
        clear_auto_section_red(document, spot_filled=False)
        self.assertEqual(red_runs(strategy), [])
        self.assertTrue(red_runs(bias))

        clear_auto_section_red(document, spot_filled=True)
        self.assertEqual(red_runs(bias), [])

    def test_day_ahead_total_includes_qingyuan_plan_energy(self) -> None:
        # 口径已确认：日前出清总电量＝快报（日前）F7 火电口径 + 清远计划电量，
        # 不能再退回「只报火电」的写法（人工稿曾写 6035，与分项对不上）。
        with tempfile.TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "出清情况（分公司2026.11.4).xlsx"
            workbook = Workbook()
            basic_info = workbook.active
            basic_info.title = "基础信息"
            basic_info["AA64"] = 0
            basic_info["AC64"] = 2300
            day_ahead = workbook.create_sheet("快报  (日前)")
            for row, energy, price in ((7, 6034.8139, 236), (8, 1636, 539)):
                day_ahead.cell(row, 6, energy)
                day_ahead.cell(row, 7, price)
            for row, label, energy, price in (
                (25, "汕头海风", 452, 432),
                (26, "鮀莲光伏", 39, 591),
                (27, "归湖光伏", 33, 553),
            ):
                day_ahead.cell(row, 1, label)
                day_ahead.cell(row, 7, energy)
                day_ahead.cell(row, 8, price)
            workbook.save(workbook_path)

            summary = load_day_ahead_clearing_summary("2026-11-04", workbook_path)

        self.assertAlmostEqual(summary["qingyuanSource"]["cells"]["AC64"], 2300)
        self.assertAlmostEqual(summary["baseTotalEnergy"], 6034.8139)
        self.assertAlmostEqual(summary["renewableEnergy"], 524)
        self.assertAlmostEqual(summary["totalEnergy"], 6788.8139)
        generated = build_day_ahead_clearing_text("2026-11-04", summary)
        self.assertTrue(generated.startswith("11月4日分公司日前出清电量6789万千瓦时。"))
        self.assertIn("、清远（计划）230万千瓦时；", generated)

    def test_qingyuan_day_ahead_energy_sums_basic_info_cells(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "出清情况（分公司2026.8.12).xlsx"
            workbook = Workbook()
            basic_info = workbook.active
            basic_info.title = "基础信息"
            basic_info["AA64"] = 12.4
            basic_info["AC64"] = 7.6
            day_ahead = workbook.create_sheet("快报  (日前)")
            for row, energy, price in (
                (7, 100, 400),
                (8, 20, 410),
                (13, 30, 420),
                (19, 40, 430),
            ):
                day_ahead.cell(row, 6, energy)
                day_ahead.cell(row, 7, price)
            for row, label, energy, price in (
                (25, "汕头海风", 5, 300),
                (26, "鮀莲光伏", 3, 310),
                (27, "归湖光伏", 2, 320),
            ):
                day_ahead.cell(row, 1, label)
                day_ahead.cell(row, 7, energy)
                day_ahead.cell(row, 8, price)
            workbook.save(workbook_path)

            summary = load_day_ahead_clearing_summary("2026-08-12", workbook_path)

        self.assertEqual(summary["companies"]["清远"]["energy"], 2)
        self.assertEqual(summary["baseTotalEnergy"], 100)
        self.assertEqual(summary["renewableEnergy"], 10)  # 海风 5 + 鮀莲 3 + 归湖 2
        self.assertEqual(summary["totalEnergy"], 112)
        self.assertEqual(
            summary["qingyuanSource"],
            {"sheet": "基础信息", "cells": {"AA64": 12.4, "AC64": 7.6}},
        )
        generated = build_day_ahead_clearing_text("2026-08-12", summary)
        self.assertTrue(generated.startswith("8月12日分公司日前出清电量112万千瓦时。"))
        self.assertIn("、清远（计划）2万千瓦时；", generated)


if __name__ == "__main__":
    unittest.main()
