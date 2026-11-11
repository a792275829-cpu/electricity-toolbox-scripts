from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook, load_workbook

from toolbox.runtime import ToolPaths, load_module
from toolbox.review_classification import prepare_review_folder
from toolbox.tasks import CancellationToken
from toolbox_shared.node_prices import QUARTER_SLOTS, collect_node_prices, read_node_prices


class SummaryNodePriceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = load_module("summary_node_test", ToolPaths(Path(__file__).resolve().parents[2]).summary)

    def source(self, market="日前", day="2026-11-12", *, names=None, values=None, slots=None,
               filename_day=None, auxiliary=True):
        names = names or ["其他下海北A升压站220_KV#1", "广澳海风场", "海风升压站", "其他节点"]
        values = values or [[str(i + base) for i in range(96)] for base in (-100, 0, 40, 999)]
        book = Workbook()
        sheet = book.active
        sheet.title = f"{market}节点电价查询({day})"
        sheet.append(["节点名称", "数据项", *(slots or QUARTER_SLOTS)])
        for name, row in zip(names, values):
            sheet.append([name, "电价(元/MWh)", *row])
        if auxiliary:
            province = book.create_sheet(f"全省-{market}节点电价查询({day})")
            province.append(["节点名称", "数据项", *QUARTER_SLOTS])
            province.append(["全省", "电价(元/MWh)", *([9999] * 96)])
            numeric = book.create_sheet("Sheet1")
            for row in values:
                numeric.append(row)
        path = self.root / f"{market}节点电价查询({filename_day or day}).xlsx"
        book.save(path)
        book.close()
        return path

    def test_filter_or_once_and_average_all_96_with_zero_and_negative(self):
        path = self.source()
        result = read_node_prices(path)
        self.assertEqual(result.run_date, date(2026, 11, 12))
        self.assertEqual(len(result.nodes), 3)
        self.assertEqual(len(result.quarter_averages), 96)
        for index, value in enumerate(result.quarter_averages):
            self.assertAlmostEqual(value, index - 20)

    def test_date_comes_from_sheet_instead_of_filename(self):
        result = read_node_prices(self.source(filename_day="2026-11-11"))
        self.assertEqual(result.run_date, date(2026, 11, 12))

    def test_columns_are_matched_by_time_labels(self):
        values = [[str(i + base) for i in reversed(range(96))] for base in (-100, 0, 40, 999)]
        result = read_node_prices(self.source(slots=list(reversed(QUARTER_SLOTS)), values=values))
        self.assertEqual(result.quarter_averages[0], -20)
        self.assertEqual(result.quarter_averages[-1], 75)

    def test_duplicate_or_missing_quarter_is_rejected(self):
        for slots in ([*QUARTER_SLOTS[:-1]], [*QUARTER_SLOTS[:-1], "00:00"]):
            with self.subTest(slots=slots[-1]):
                with self.assertRaisesRegex(ValueError, "时段缺失或重复"):
                    read_node_prices(self.source(slots=slots))

    def test_invalid_prices_preserve_previous_report(self):
        output = self.root / "汇总.xlsx"
        output.write_bytes(b"previous")
        for bad in (None, "", " ", "nan", "inf", True):
            values = [[str(i) for i in range(96)] for _ in range(4)]
            values[0][5] = bad
            self.source(values=values)
            with self.subTest(value=bad), self.assertRaisesRegex(ValueError, "01:15.*电价缺失或无效"):
                self.module.run_summary(self.root, output)
            self.assertEqual(output.read_bytes(), b"previous")

    def test_duplicate_node_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "节点重复"):
            read_node_prices(self.source(names=["海风A", "海风A"]))

    def test_no_matching_node_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未找到"):
            read_node_prices(self.source(names=["其他节点"]))

    def test_multiple_sources_for_same_market_are_rejected(self):
        self.source()
        self.source(day="2026-11-13")
        with self.assertRaisesRegex(ValueError, "来源冲突"):
            collect_node_prices(self.root, lambda _: None)

    def test_excel_lock_file_is_ignored(self):
        path = self.source()
        (self.root / f"~${path.name}").write_bytes(b"lock")
        self.assertEqual(len(collect_node_prices(self.root, lambda _: None)), 1)

    def test_prepare_preserves_node_files_and_summary_has_last_four_rows(self):
        sources = [self.source(), self.source("实时", "2026-11-10")]
        originals = {p: p.read_bytes() for p in sources}
        book = Workbook()
        sheet = book.active
        sheet.title = "海门#1机组"
        sheet.append(["时刻", "电量", "出清电价"])
        for hour in range(24):
            sheet.append([f"{hour:02d}:00", 1, 200])
        book.save(self.root / "日前交易结果查询(2026-11-12).xlsx")
        book.close()
        prepare_review_folder(self.root, logger=lambda _: None, token=CancellationToken(),
                              template=self.root / "no-template")
        self.assertTrue(all(p.read_bytes() == original for p, original in originals.items()))
        self.assertEqual(list(self.root.glob("*/*节点电价*.xlsx")), [])
        output = self.module.run_summary(self.root, self.root / "汇总.xlsx")
        book = load_workbook(output, data_only=True)
        try:
            self.assertEqual(book.sheetnames, ["海门", "海风汇总"])
            sheet = book["海风汇总"]
            self.assertEqual((sheet.max_row, sheet.max_column), (4, 96))
            self.assertEqual(sheet["A1"].value, "海风日前 2026-11-12")
            self.assertEqual(sheet["A3"].value, "海风实时 2026-11-10")
            for row in (2, 4):
                for index in range(96):
                    self.assertEqual(sheet.cell(row, index + 1).value, index - 20)
                    self.assertEqual(sheet.cell(row, index + 1).number_format, "0.00")
            self.assertEqual(book["海门"]["C4"].value, 200)
        finally:
            book.close()

    def test_node_only_folder_with_one_market_leaves_other_96_blank(self):
        self.source("实时", "2026-11-10")
        output = self.module.run_summary(self.root, self.root / "汇总.xlsx")
        book = load_workbook(output, data_only=True)
        try:
            self.assertEqual(book.sheetnames, ["海风汇总"])
            sheet = book.active
            self.assertEqual(sheet["A1"].value, "海风日前")
            self.assertTrue(all(cell.value is None for cell in sheet[2]))
            self.assertEqual(sheet["CR4"].value, 75)
        finally:
            book.close()
