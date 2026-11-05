from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from docx import Document
from openpyxl import Workbook

from generate_red_marked_report import (
    LOCAL_TEMPLATE_PATH,
    _parse_unit_status,
    build_unit_status_text,
    fill_startup_status,
    load_unit_status_texts,
)

BASE_ROWS: list[tuple] = [
    ("南方分公司", None, "机组状态", "有效容量\n（MW）", "出力下限", "出力上限", "约束原因"),
    ("汕头", "合计", "/", 1200, "/", "/", "/"),
    (None, "#1", "运行", 300, 120, 300, "必开"),
    (None, "#2", "运行", 300, 120, 300, None),
    (None, "#3", "备用", 600, None, None, None),
    (None, "约束情况", "停机序列：2、1，启机序列：3。", None, None, None, None),
    ("海门", "合计", "/", 4072, "/", "/", "/"),
    (None, "#1", "运行", 1036, 400, 1036, None),
    (None, "#2", "停备", 1036, 350, 1036, None),  # 停备也写作「备用」
    (None, "#3", "供热", 1000, 560, 920, "主供热机组"),
    (None, "#4", "停机", 1000, 350, 1000, None),  # 停机也写作「备用」
    ("东莞", "合计", "/", 1941.96, "/", "/", "/"),
    (None, "#1,2", "备用", 472.5, 270, 472.5, None),
    (None, "#3,4", "运行", 472.5, 270, 472.5, None),
    (None, "#5,6", "检修", 498.48, 270, 498.48, "检修时间10月8日3:00-11月7日23:30"),
    (None, "#7,8", "备用", 498.48, 270, 498.48, None),
    ("清远", "合计", "/", 240, "/", "/", "/"),
    (None, "#1,2", "备用", 120, 70, 115.9, None),
    (None, "#3,4", "运行", 120, 70, 115.9, None),
    ("二、分公司中长期明细（年+月+周，折发电量，MWh）", None, None, None, None, None, None),
]


class UnitStatusTextTests(unittest.TestCase):
    def test_parse_stops_at_next_section(self) -> None:
        parsed = _parse_unit_status(BASE_ROWS)
        self.assertEqual([unit for unit, _ in parsed["汕头"]], ["1", "2", "3"])
        self.assertEqual([unit for unit, _ in parsed["海门"]], ["1", "2", "3", "4"])
        self.assertEqual([unit for unit, _ in parsed["东莞"]], ["1,2", "3,4", "5,6", "7,8"])
        self.assertEqual(len(parsed["清远"]), 2)
        self.assertNotIn("二、分公司中长期明细", parsed)

    def test_shantou_and_haimen_group_by_status_order(self) -> None:
        parsed = _parse_unit_status(BASE_ROWS)
        self.assertEqual(
            build_unit_status_text("汕头", parsed["汕头"]),
            "#1、#2机组运行；#3机组备用。",
        )
        self.assertEqual(
            build_unit_status_text("海门", parsed["海门"]),
            "#1机组运行；#2、#4机组备用；#3机组供热。",
        )

    def test_dongguan_uses_suites_and_comma_separators(self) -> None:
        parsed = _parse_unit_status(BASE_ROWS)
        # #1,2=一套 … #7,8=四套；运行→调峰，备用保持备用，分隔用「，」
        # 按套号顺序逐套列出，不按状态聚合（否则会出现「二套…一、四套…三套」跳序）。
        self.assertEqual(
            build_unit_status_text("东莞", parsed["东莞"]),
            "一套机组备用，二套机组调峰，三套机组检修，四套机组备用。",
        )

    def test_dongguan_orders_suites_regardless_of_input_order(self) -> None:
        # 输入顺序打乱时仍按一套→四套输出。
        text = build_unit_status_text(
            "东莞",
            [("7,8", "备用"), ("3,4", "检修"), ("1,2", "运行"), ("5,6", "运行")],
        )
        self.assertEqual(
            text, "一套机组调峰，二套机组检修，三套机组调峰，四套机组备用。"
        )

    def test_qingyuan_keeps_unit_order_and_semicolons(self) -> None:
        parsed = _parse_unit_status(BASE_ROWS)
        self.assertEqual(
            build_unit_status_text("清远", parsed["清远"]),
            "一套机组备用；二套机组运行。",
        )

    def test_missing_status_is_skipped(self) -> None:
        self.assertIsNone(build_unit_status_text("汕头", []))
        self.assertEqual(
            build_unit_status_text("汕头", [("1", "运行"), ("2", "")]),
            "#1机组运行。",
        )


class StartupFillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)

    def _filled_blocks(self, previous: dict[str, str], current: dict[str, str]) -> list[str]:
        target = Path(self._directory.name) / "report.docx"
        shutil.copy(LOCAL_TEMPLATE_PATH, target)
        document = Document(target)
        self.assertEqual(fill_startup_status(document, current, previous), 8)
        document.save(target)

        saved = Document(target)
        texts = [paragraph.text.strip() for paragraph in saved.paragraphs]
        start = texts.index("一、机组启停情况")
        end = texts.index("二、分公司电量、电价完成情况")
        return [text for text in texts[start:end] if text]

    def test_identical_day_is_replaced_by_previous_day_wording(self) -> None:
        previous = {
            "汕头": "#1、#2机组运行；#3机组备用。",
            "海门": "#1机组运行；#2、#4机组备用；#3机组供热。",
            "东莞": "一套机组备用，二套机组调峰，三套机组检修，四套机组备用。",
            "清远": "一套机组备用；二套机组运行。",
        }
        current = dict(previous)
        current["汕头"] = "#1、#2、#3机组运行。"
        blocks = self._filled_blocks(previous, current)
        self.assertIn("汕头厂：#1、#2、#3机组运行。", blocks)
        self.assertIn("海门厂：与前一日运行方式一致。", blocks)
        self.assertIn("东莞厂：与前一日运行方式一致。", blocks)
        self.assertIn("清远厂：与前一日运行方式一致。", blocks)

    def test_first_block_never_uses_previous_day_wording(self) -> None:
        previous = {
            "汕头": "#1、#2机组运行；#3机组备用。",
            "海门": "#1机组运行；#2、#4机组备用；#3机组供热。",
            "东莞": "一套机组备用，二套机组调峰，三套机组检修，四套机组备用。",
            "清远": "一套机组备用；二套机组运行。",
        }
        blocks = self._filled_blocks(previous, dict(previous))
        first_date = blocks.index("11月3日：")
        second_date = blocks.index("11月4日：")
        first_block = blocks[first_date:second_date]
        self.assertIn("汕头厂：#1、#2机组运行；#3机组备用。", first_block)
        self.assertNotIn("汕头厂：与前一日运行方式一致。", first_block)


def _make_unit_workbook(path: Path, rows: list[tuple]) -> Path:
    workbook = Workbook()
    brief = workbook.active
    brief.title = "快报  (日前)"
    brief["A1"] = "项目"
    base = workbook.create_sheet("基础信息")
    for index, row in enumerate(rows, start=12):
        for column, value in enumerate(row, start=1):
            if value is not None:
                base.cell(index, column, value)
    workbook.save(path)
    workbook.close()
    return path


DONGGUAN_ROWS: list[tuple] = [
    ("南方分公司", None, "机组状态"),
    ("东莞", "合计", "/", 1941.96),
    (None, "#1,2", "供热", 472.5),
    (None, "#3,4", "运行", 472.5),
    (None, "#5,6", "检修", 498.48),
    (None, "#7,8", "备用", 498.48),
    (None, "约束情况", "运行日运行方式：一、二、四套机组调峰，三套检修。开机序列：2、4、1、3。"),
]


class DongguanStatusSourceTests(unittest.TestCase):
    def test_status_column_wins_over_constraint_text(self) -> None:
        # 东莞「约束情况」里写「一、二、四套调峰，三套检修」与 C 列不一致时，仍以 C 列机组状态为准。
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_unit_workbook(Path(tmp) / "wb.xlsx", DONGGUAN_ROWS)
            texts = load_unit_status_texts("2026-11-06", path)
        self.assertEqual(
            texts["东莞"],
            "一套机组供热，二套机组调峰，三套机组检修，四套机组备用。",
        )

    def test_emergency_standby_keeps_word_and_sorts_as_reserve(self) -> None:
        # 「应急备用」保留原词，但排序按「备用」处理，因此排在检修之前。
        text = build_unit_status_text(
            "汕头", [("3", "检修"), ("2", "应急备用"), ("1", "运行")]
        )
        self.assertEqual(text, "#1机组运行；#2机组应急备用；#3机组检修。")


if __name__ == "__main__":
    unittest.main()
