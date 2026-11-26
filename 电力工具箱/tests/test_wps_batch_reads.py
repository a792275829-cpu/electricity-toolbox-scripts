from __future__ import annotations
import importlib
import os
import zipfile

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from toolbox.runtime import load_module
from toolbox_shared import native_excel


class WpsBatchReadTests(unittest.TestCase):
    def test_uncached_formula_stops_but_cached_zero_and_empty_string_are_valid(self):
        module = importlib.import_module('toolbox_shared.wps_service')
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'formula.xlsx'
            book = Workbook(); book.active['A1'] = '=1-1'; book.active['B1'] = None
            book.save(source); book.close()
            with self.assertRaisesRegex(ValueError, '来源公式缺少计算缓存'):
                module.read_excel_range(source, 'Sheet', 'A1:B1')
            self.assertEqual(module.read_excel_range(source, 'Sheet', 'B1')[0], [['']])
            for cache, expected in (('<v>0</v>', 0), ('<v></v>', '')):
                with zipfile.ZipFile(source) as archive:
                    files = {name: archive.read(name) for name in archive.namelist()}
                xml = files['xl/worksheets/sheet1.xml'].decode()
                import re
                xml = re.sub(r'<v>.*?</v>', cache, xml)
                if expected == '':
                    xml = xml.replace('r="A1"', 'r="A1" t="str"')
                files['xl/worksheets/sheet1.xml'] = xml.encode()
                with zipfile.ZipFile(source, 'w') as archive:
                    for name, data in files.items(): archive.writestr(name, data)
                self.assertEqual(module.read_excel_range(source, 'Sheet', 'A1')[0], [[expected]])

    @unittest.skipUnless(native_excel.native_binary(), "build native/price-xlsx first")
    def test_native_checks_multiple_worksheet_parts_and_shared_formula_anchors(self):
        from toolbox_shared import wps_service as service

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.xlsx"
            book = Workbook()
            first = book.active
            first.title = "首张"
            first.append(["=1-1", '=""', 42])
            second = book.create_sheet("第二张")
            second["B2"] = "=1+2"
            book.save(path)
            book.close()

            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            first = "xl/worksheets/sheet1.xml"
            second = "xl/worksheets/sheet2.xml"
            files[first] = files[first].replace(b'<f>1-1</f><v></v>', b'<f>1-1</f><v>0</v>')
            files[first] = files[first].replace(b'<c r="B1">', b'<c r="B1" t="str">')
            files[second] = files[second].replace(
                b'<f>1+2</f>', b'<f t="shared" si="0" ref="$B$2:$C$4">1+2</f>',
            )
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)

            requests = [("首张", "C1"), ("第二张", "A1"), ("首张", "A1:B1")]
            with mock.patch.dict(os.environ, TOOLBOX_WPS_FORMULA_BACKEND="python"):
                reference = service.read_excel_ranges(path, requests)
            with mock.patch.dict(os.environ, TOOLBOX_WPS_FORMULA_BACKEND="auto"), \
                 mock.patch.object(service, "WPS_FORMULA_NATIVE_MIN_BYTES", 1), \
                 mock.patch.object(native_excel, "call", wraps=native_excel.call) as call:
                self.assertEqual(service.read_excel_ranges(path, requests), reference)
                call.assert_called_once()
                self.assertEqual([item["part"] for item in call.call_args.kwargs["payload"]], [first, second])
            self.assertEqual([item[0] for item in reference], [[[42]], [[""]], [[0, ""]]])
            for mode in ("python", "rust"):
                with mock.patch.dict(os.environ, TOOLBOX_WPS_FORMULA_BACKEND=mode):
                    with self.assertRaisesRegex(ValueError, "第二张!B2"):
                        service.read_excel_ranges(path, [*requests, ("第二张", "C4")])

    def test_auto_small_sheets_skip_native_and_large_native_failures_recheck_in_python(self):
        from toolbox_shared import wps_service as service

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.xlsx"
            book = Workbook()
            book.active["A1"] = "=1+2"
            book.save(path)
            book.close()
            with mock.patch.dict(os.environ, TOOLBOX_WPS_FORMULA_BACKEND="auto"), \
                 mock.patch.object(native_excel, "native_binary", return_value=Path("mock-rust")), \
                 mock.patch.object(native_excel, "call", side_effect=ValueError("unknown operation")) as call:
                with self.assertRaisesRegex(ValueError, "Sheet!A1"):
                    service.read_excel_range(path, "Sheet", "A1")
                call.assert_not_called()
                with mock.patch.object(service, "WPS_FORMULA_NATIVE_MIN_BYTES", 1):
                    with self.assertRaisesRegex(ValueError, "Sheet!A1"):
                        service.read_excel_range(path, "Sheet", "A1")
                call.assert_called_once()

    def test_replaced_source_during_range_read_stops(self):
        from toolbox_shared import wps_service as service

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.xlsx"
            replacement = Path(folder) / "replacement.xlsx"
            book = Workbook()
            book.active["A1"] = 42
            book.save(path)
            book.active["A1"] = "=1+2"
            book.save(replacement)
            book.close()
            validate = service.validate_formula_caches

            def replace_after_validation(path, requests, **kwargs):
                validate(path, requests, **kwargs)
                replacement.replace(path)

            with mock.patch.object(service, "validate_formula_caches", side_effect=replace_after_validation):
                with self.assertRaisesRegex(RuntimeError, "来源工作簿已修改"):
                    service.read_excel_range(path, "Sheet", "A1")

    def test_multiple_ranges_share_formula_and_value_readers_and_keep_mapping_order(self) -> None:
        root = Path(__file__).resolve().parents[2]
        module = importlib.import_module('toolbox_shared.wps_service')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.xlsx"
            workbook = Workbook()
            first = workbook.active
            first.title = "数据"
            first["A2"] = datetime(2026, 9, 24, 8, 30)
            first["B2"] = 0.125
            first["A5"] = 7
            second = workbook.create_sheet("补充")
            second["C3"] = "完成"
            workbook.save(source)
            workbook.close()

            def mapping(sheet: str, start: str, end: str, target: str):
                return module.MappingRow(
                    kdocs_url="https://example.invalid/table",
                    local_file=str(source),
                    source_sheet=sheet,
                    source_start=start,
                    source_end=end,
                    target_sheet="目标",
                    target_start=target,
                    target_end=target if start == end else "B" + target[1:],
                )

            mappings = [
                mapping("数据", "A2", "B2", "A10"),
                mapping("补充", "C3", "C3", "A20"),
                mapping("数据", "A5", "B5", "A30"),
            ]
            with mock.patch.object(
                module, "load_excel_workbook", wraps=module.load_excel_workbook
            ) as opened:
                prepared = module.prepare_mappings(mappings)

            opened.assert_called_once()
            self.assertEqual([1, 2, 3], [item.index for item in prepared])
            self.assertEqual([["2026-09-24 08:30:00", 0.125]], prepared[0].values)
            self.assertEqual([["完成"]], prepared[1].values)
            self.assertEqual([[7, ""]], prepared[2].values)
            self.assertEqual([1, 1, 1], [item.rows for item in prepared])

    def test_online_and_excel_sources_keep_original_mapping_order(self) -> None:
        root = Path(__file__).resolve().parents[2]
        module = importlib.import_module('toolbox_shared.wps_service')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.xlsx"
            workbook = Workbook()
            workbook.active.title = "数据"
            workbook.active["A1"] = 42
            workbook.save(source)
            workbook.close()
            mappings = [
                module.MappingRow(
                    "https://example.invalid/target", str(source), "数据", "A1", "A1", "目标", "A1", "A1"
                ),
                module.MappingRow(
                    "https://example.invalid/target", "", "在线", "B2", "B2", "目标", "A2", "A2",
                    source_type="kdocs", source_url="https://example.invalid/source",
                ),
                module.MappingRow(
                    "https://example.invalid/target", str(source), "数据", "A1", "A1", "目标", "A3", "A3"
                ),
            ]
            with mock.patch.object(module, "read_online_ranges", return_value=[[["在线值"]]]) as online:
                prepared = module.prepare_mappings(mappings)

            self.assertEqual([[[42]], [["在线值"]], [[42]]], [item.values for item in prepared])
            online.assert_called_once()

    def test_online_regions_share_context_and_preserve_order(self) -> None:
        root = Path(__file__).resolve().parents[2]
        module = importlib.import_module('toolbox_shared.wps_service')
        requests = [
            ("https://example.invalid/a", "数据", "B2", 1, 1),
            ("https://example.invalid/b", "补充", "A1:B1", 1, 2),
            ("https://example.invalid/a", "数据", "C3", 1, 1),
        ]
        context = mock.Mock()
        with mock.patch.object(module, "sync_playwright"), \
             mock.patch.object(module, "open_browser_context", return_value=(None, context, True)) as opened, \
             mock.patch.object(module, "find_or_open_page") as find, \
             mock.patch.object(module, "evaluate_wps_operation", side_effect=[None, [0.125, "完成"], 42]):
            result = module.read_online_ranges(requests, "cdp", "", lambda _: None)
        self.assertEqual(result, [[[""]], [[0.125, "完成"]], [[42]]])
        opened.assert_called_once()
        self.assertEqual(find.call_args_list, [mock.call(context, request[0]) for request in requests])
        context.close.assert_called_once()

    def test_online_failure_closes_owned_context_and_stops_remaining_reads(self) -> None:
        root = Path(__file__).resolve().parents[2]
        module = importlib.import_module('toolbox_shared.wps_service')
        requests = [("https://example.invalid/a", "数据", "A1", 1, 1)] * 3
        for owned in (False, True):
            with self.subTest(owned=owned):
                context = mock.Mock()
                with mock.patch.object(module, "sync_playwright"), \
                     mock.patch.object(module, "open_browser_context", return_value=(None, context, owned)), \
                     mock.patch.object(module, "find_or_open_page"), \
                     mock.patch.object(module, "evaluate_wps_operation", side_effect=[42, RuntimeError("source failed")]) as evaluate:
                    with self.assertRaisesRegex(RuntimeError, "source failed"):
                        module.read_online_ranges(requests, "cdp", "", lambda _: None)
                self.assertEqual(evaluate.call_count, 2)
                self.assertEqual(context.close.call_count, int(owned))


if __name__ == "__main__":
    unittest.main()
