"""人工维护段落的识别测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_red_marked_report import collect_manual_sections


def _document(*paragraphs: str) -> Document:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    return document


class ManualSectionTests(unittest.TestCase):
    def test_strategy_section_skips_generated_first_paragraph(self) -> None:
        document = _document(
            "三、现货交易策略",
            "运行日统调负荷需求增长，全天最大14500万千瓦……",
            "粤东区域：500kV线路：胪盘乙线检修至12月底。",
            "汕头厂：综合考虑运行日负荷需求变化……",
            "四、日清分情况",
            "表1  11月2日分公司日清分情况",
        )
        texts = [item["text"] for item in collect_manual_sections(document)]
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[0].startswith("粤东区域"))
        self.assertTrue(texts[1].startswith("汕头厂"))
        # 「四、日清分情况」之后的内容不应被收进来。
        self.assertFalse(any("日清分" in text for text in texts))

    def test_forecast_section_collects_interval_paragraph(self) -> None:
        document = _document(
            "五、短期市场预测",
            "未来三天全省各地区多以多云为主，气温于23-30℃之间波动；",
            "预计全市场统调负荷区间在1-1.5亿千瓦，全省均价480-580厘/千瓦时。",
            "下表为未来五天海风功率预测：",
        )
        texts = [item["text"] for item in collect_manual_sections(document)]
        self.assertEqual(len(texts), 1)
        self.assertIn("1-1.5亿千瓦", texts[0])

    def test_missing_heading_is_ignored(self) -> None:
        document = _document("一、机组启停情况", "汕头厂：#1机组运行。")
        self.assertEqual(collect_manual_sections(document), [])

    def test_only_heading_without_body(self) -> None:
        document = _document("三、现货交易策略", "四、日清分情况")
        self.assertEqual(collect_manual_sections(document), [])


if __name__ == "__main__":
    unittest.main()
