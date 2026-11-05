"""两个表格的标题与表格同页（过页开始）的测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_red_marked_report import apply_table_page_breaks


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


if __name__ == "__main__":
    unittest.main()
