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
    ensure_daily_clearing_table_structure,
    ensure_qingyuan_template_content,
    ensure_qingyuan_online_energy_text,
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
        self.assertEqual(len(table.columns), 18)
        header_text = "".join(cell.text.replace("\n", "") for row in table.rows[:2] for cell in row.cells)
        for header in DAILY_CLEARING_REQUIRED_HEADERS:
            self.assertIn(header, header_text)

        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
        bias_index = paragraphs.index("3.现货偏差分析")
        forecast_index = paragraphs.index("五、短期市场预测")
        self.assertLess(bias_index, forecast_index)
        self.assertTrue(paragraphs[bias_index + 1].startswith("分公司日前实时偏差"))
        self.assertTrue(paragraphs[bias_index + 2].startswith("实际上网偏差"))

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
        self.assertIn("、清远0万千瓦时；", online_text)
        self.assertIn("、清远（计划）0万千瓦时；", day_ahead_text)

        legacy_online = online_text.replace("、清远0万千瓦时", "")
        upgraded_online = ensure_qingyuan_online_energy_text(legacy_online)
        self.assertEqual(upgraded_online.count("清远0万千瓦时"), 1)
        self.assertEqual(ensure_qingyuan_online_energy_text(upgraded_online), upgraded_online)

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

        self.assertEqual(summary["companies"]["清远"]["energy"], 20)
        self.assertEqual(summary["baseTotalEnergy"], 100)
        self.assertEqual(summary["totalEnergy"], 120)
        self.assertEqual(
            summary["qingyuanSource"],
            {"sheet": "基础信息", "cells": {"AA64": 12.4, "AC64": 7.6}},
        )
        generated = build_day_ahead_clearing_text("2026-08-12", summary)
        self.assertTrue(generated.startswith("8月12日分公司日前出清电量120万千瓦时。"))
        self.assertIn("、清远（计划）20万千瓦时；", generated)


if __name__ == "__main__":
    unittest.main()
