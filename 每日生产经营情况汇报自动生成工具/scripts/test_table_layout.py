"""表格版面：过页开始、海风预测表压缩、红字段日期替换的测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from docx.shared import Pt

from generate_red_marked_report import (
    apply_table_page_breaks,
    compact_forecast_table,
    replace_leading_month_day,
)


def _document(*paragraphs: str) -> Document:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    return document


class TablePageBreakTests(unittest.TestCase):
    def test_both_table_headings_start_on_new_page(self) -> None:
        document = _document(
            "三、现货交易策略",
            "策略正文。",
            "四、日清分情况",
            "表1  11月4日分公司日清分情况",
            "单位：万千瓦时、厘/千瓦时、万元",
            "五、短期市场预测",
            "天气正文。",
            "下表为未来五天海风功率预测：",
        )
        applied = apply_table_page_breaks(document)
        self.assertEqual(applied, ["四、日清分情况", "下表为未来五天海风功率预测："])
        flags = {
            paragraph.text.strip(): paragraph.paragraph_format.page_break_before
            for paragraph in document.paragraphs
        }
        self.assertTrue(flags["四、日清分情况"])
        self.assertTrue(flags["下表为未来五天海风功率预测："])
        # 表1 标题紧跟小节标题，不单独分页。
        self.assertFalse(flags["表1  11月4日分公司日清分情况"])

    def test_missing_anchor_is_skipped(self) -> None:
        document = _document("四、日清分情况", "表1  11月4日分公司日清分情况")
        self.assertEqual(apply_table_page_breaks(document), ["四、日清分情况"])

    def test_repeated_call_is_idempotent(self) -> None:
        document = _document("四、日清分情况", "下表为未来五天海风功率预测：")
        apply_table_page_breaks(document)
        self.assertEqual(len(apply_table_page_breaks(document)), 2)
        for paragraph in document.paragraphs:
            self.assertTrue(paragraph.paragraph_format.page_break_before)


class ForecastTableCompactTests(unittest.TestCase):
    def _table(self, rows: int = 26, cols: int = 6) -> Document:
        document = Document()
        table = document.add_table(rows=rows, cols=cols)
        for index, row in enumerate(table.rows):
            for cell in row.cells:
                cell.paragraphs[0].add_run(f"{index}")
                for run in cell.paragraphs[0].runs:
                    run.font.size = Pt(12)
        return document, table

    def test_rows_and_fonts_are_shrunk(self) -> None:
        document, table = self._table()
        self.assertEqual(compact_forecast_table(table), 26)
        for row in table.rows:
            self.assertAlmostEqual(row.height.cm, 0.5, places=2)
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    self.assertEqual(paragraph.paragraph_format.space_before, Pt(0))
                    self.assertEqual(paragraph.paragraph_format.space_after, Pt(0))
                    for run in paragraph.runs:
                        self.assertEqual(run.font.size, Pt(10))

    def test_compact_keeps_table_within_one_page(self) -> None:
        document, table = self._table()
        section = document.sections[0]
        available = section.page_height.cm - section.top_margin.cm - section.bottom_margin.cm
        compact_forecast_table(table, available_height_cm=available)
        # 行高按版心高度反算，保证标题＋整表不超过一页。
        total = sum(row.height.cm for row in table.rows)
        self.assertLess(total + 2.0, available)

    def test_compact_caps_row_height_for_small_page(self) -> None:
        _, table = self._table()
        compact_forecast_table(table, available_height_cm=10.0)
        # (10 - 2) / 26 ≈ 0.31cm，下限 0.32cm；26 行共 8.32cm + 2cm < 10cm。
        for row in table.rows:
            self.assertAlmostEqual(row.height.cm, 0.32, places=2)
        self.assertLess(sum(row.height.cm for row in table.rows) + 2.0, 10.5)

    def test_compact_disables_document_grid_and_cell_padding(self) -> None:
        # 模板带 docGrid；不关「对齐到网格」时 Word 会把 10pt 行吸到 31pt，26 行跨两页。
        _, table = self._table()
        compact_forecast_table(table)
        margins = table._tbl.tblPr.find(qn("w:tblCellMar"))
        self.assertIsNotNone(margins)
        self.assertEqual(margins.find(qn("w:top")).get(qn("w:w")), "0")
        self.assertEqual(margins.find(qn("w:bottom")).get(qn("w:w")), "0")
        for row in table.rows:
            self.assertIsNotNone(row._tr.get_or_add_trPr().find(qn("w:cantSplit")))
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    properties = paragraph._p.get_or_add_pPr()
                    snap = properties.find(qn("w:snapToGrid"))
                    self.assertIsNotNone(snap)
                    self.assertEqual(snap.get(qn("w:val")), "0")
                    mark = properties.find(qn("w:rPr"))
                    self.assertIsNotNone(mark)
                    self.assertEqual(mark.find(qn("w:sz")).get(qn("w:val")), "20")


class LeadingDateTests(unittest.TestCase):
    def test_replaces_leading_month_day_only(self) -> None:
        text = "11月3日分公司上网电量6228万千瓦时，其中汕头1279万。"
        self.assertEqual(
            replace_leading_month_day(text, "11月5日"),
            "11月5日分公司上网电量6228万千瓦时，其中汕头1279万。",
        )

    def test_leaves_text_without_leading_date(self) -> None:
        text = "分公司上网电量6228万千瓦时。"
        self.assertEqual(replace_leading_month_day(text, "11月5日"), text)

    def test_does_not_touch_dates_inside_text(self) -> None:
        text = "11月3日检修计划自11月7日起。"
        self.assertEqual(
            replace_leading_month_day(text, "11月5日"), "11月5日检修计划自11月7日起。"
        )


if __name__ == "__main__":
    unittest.main()
