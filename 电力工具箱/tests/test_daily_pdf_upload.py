from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = ROOT / "广东运行日报PDF解析"
sys.path.insert(0, str(TOOL_DIR)) if str(TOOL_DIR) not in sys.path else None

import run_daily_pdf_parser as pipeline
from gd_pdf_tool import uploader
from gd_pdf_tool.excel_writer import write_quote_excel
from gd_pdf_tool.parser import QuoteRow
from toolbox.daily_pdf_page import DailyPdfParserPage
from toolbox.runtime import TaskRegistry, ToolPaths


class DailyPdfUploadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.pdf = self.folder / "广东电力现货市场2026年10月运行日报（10.12）.pdf"
        self.pdf.write_bytes(b"fixture: date is supplied by the filename")
        self.excel = self.folder / "现货运行日报报价容量区间20261012.xlsx"
        self.rows = [QuoteRow("(-∞,0]", 100, 200, 300), QuoteRow("(0, 25]", 0.2, 0, 0)]
        write_quote_excel(self.rows, self.excel)

    def test_report_date_comes_from_pdf_and_both_paths_are_preserved(self):
        plan = uploader.build_upload_plan(self.pdf, self.excel)
        self.assertEqual(date(2026, 10, 12), plan.report_date)
        self.assertEqual(self.pdf.resolve(), plan.pdf_path)
        self.assertEqual(self.excel.resolve(), plan.excel_path)
        self.assertEqual(2, uploader.verify_quote_workbook(self.excel, self.rows))

    def test_mismatching_excel_filename_date_is_rejected_before_upload(self):
        wrong = self.excel.with_name("现货运行日报报价容量区间20261011.xlsx")
        self.excel.rename(wrong)
        with self.assertRaisesRegex(ValueError, "日期不一致"):
            uploader.build_upload_plan(self.pdf, wrong)

    def test_readback_checks_values_order_and_display_format(self):
        for cell, value, formatting in (("B3", -1, "0.0"), ("A3", "(25, 50]", None), ("B3", 0.2, "0%")):
            with self.subTest(cell=cell, value=value, formatting=formatting):
                write_quote_excel(self.rows, self.excel)
                workbook = load_workbook(self.excel)
                workbook["Sheet1"][cell] = value
                if formatting:
                    workbook["Sheet1"][cell].number_format = formatting
                workbook.save(self.excel)
                workbook.close()
                with self.assertRaises(ValueError):
                    uploader.verify_quote_workbook(self.excel)

    def test_pipeline_defaults_to_parse_and_upload_and_parse_only_has_no_network_write(self):
        self.assertEqual("both", pipeline.build_parser().parse_args([str(self.pdf)]).mode)
        with mock.patch.object(pipeline, "convert_pdf_to_excel", return_value=self.excel) as convert, \
             mock.patch.object(pipeline, "upload_report_files") as upload:
            pipeline.process_report(self.pdf, self.excel, logger=lambda _: None)
            convert.assert_called_once()
            self.assertEqual(date(2026, 10, 12), upload.call_args.args[0].report_date)
            upload.reset_mock()
            pipeline.process_report(self.pdf, self.excel, mode="parse", logger=lambda _: None)
            upload.assert_not_called()

    def test_upload_only_preserves_existing_excel_and_checks_it_before_network(self):
        original = self.excel.read_bytes()
        with mock.patch.object(pipeline, "convert_pdf_to_excel") as convert, \
             mock.patch.object(pipeline, "upload_report_files") as upload:
            pipeline.process_report(self.pdf, self.excel, mode="upload", logger=lambda _: None)
            convert.assert_not_called()
            upload.assert_called_once()
        self.assertEqual(original, self.excel.read_bytes())
        self.excel.unlink()
        with mock.patch.object(pipeline, "upload_report_files") as upload:
            with self.assertRaisesRegex(ValueError, "上传文件无效"):
                pipeline.process_report(self.pdf, self.excel, mode="upload", logger=lambda _: None)
            upload.assert_not_called()

    def test_upload_failure_retains_generated_workbook(self):
        with mock.patch.object(pipeline, "convert_pdf_to_excel", return_value=self.excel), \
             mock.patch.object(pipeline, "upload_report_files", side_effect=RuntimeError("server rejected")):
            with self.assertRaisesRegex(RuntimeError, "server rejected"):
                pipeline.process_report(self.pdf, self.excel, logger=lambda _: None)
        self.assertEqual(2, uploader.verify_quote_workbook(self.excel, self.rows))

    def test_auth_state_profile_and_lock_are_local_to_this_tool(self):
        with mock.patch.object(uploader, "TOOL_DIR", self.folder):
            auth = uploader.load_auth_module()
        self.assertEqual(self.folder / "auth_state.json", auth.AUTH_STATE_PATH)
        self.assertEqual(self.folder / ".browser-profile", auth.PROFILE_DIR)
        with auth.AuthStateLock():
            self.assertTrue(auth.AUTH_LOCK_PATH.exists())
        self.assertFalse(auth.AUTH_LOCK_PATH.exists())
        self.assertNotEqual(ROOT / "上网电量抓取/auth_state.json", auth.AUTH_STATE_PATH)

    def test_remote_verification_rejects_wrong_date_filename_status_and_missing_timestamp(self):
        plan = uploader.build_upload_plan(self.pdf, self.excel)
        valid = {"date": "2026-10-12", "importStatus": 1, "importFileName": self.excel.name,
                 "updateTime": "2026-10-12 16:00:00"}
        context = mock.Mock()
        auth = mock.Mock()
        auth.parse_json_response.return_value = {"retCode": "T200", "data": [valid]}
        record = uploader.verify_remote_status(context, auth, data_type="SPOT_RUN_REPORT_QUOTE_CAPACITY", plan=plan, path=self.excel)
        self.assertEqual(valid, record)
        self.assertEqual("2026-10-12", context.request.get.call_args.kwargs["params"]["startTime"])
        for key, value in (("date", "2026-10-11"), ("importFileName", "other.xlsx"), ("importStatus", 0), ("updateTime", "")):
            with self.subTest(key=key):
                auth.parse_json_response.return_value = {"data": [{**valid, key: value}]}
                with self.assertRaises(RuntimeError):
                    uploader.verify_remote_status(context, auth, data_type="SPOT_RUN_REPORT_QUOTE_CAPACITY", plan=plan, path=self.excel)

    def test_partial_failure_closes_browser_and_releases_local_lock(self):
        plan = uploader.build_upload_plan(self.pdf, self.excel)
        auth = mock.Mock()
        auth.AuthStateLock.return_value = mock.MagicMock()
        context = auth.launch_context.return_value
        logs = []
        with mock.patch.object(uploader, "load_auth_module", return_value=auth), \
             mock.patch("playwright.sync_api.sync_playwright") as engine, \
             mock.patch.object(uploader, "open_upload_page"), \
             mock.patch.object(uploader, "upload_one", side_effect=[{}, RuntimeError("Excel failed")]) as upload:
            engine.return_value = mock.MagicMock()
            with self.assertRaisesRegex(RuntimeError, "Excel failed"):
                uploader.upload_report_files(plan, config=SimpleNamespace(headless=True), logger=logs.append)
            self.assertEqual(["现货交易日报", "报价容量数据"], [call.kwargs["label"] for call in upload.call_args_list])
        context.close.assert_called_once()
        auth.AuthStateLock.return_value.__exit__.assert_called_once()
        self.assertTrue(any("已完成：现货交易日报" in line for line in logs))

    def test_quotation_status_without_filename_is_supported_but_pdf_still_requires_it(self):
        plan = uploader.build_upload_plan(self.pdf, self.excel)
        record = {"date": "2026-10-12 00:00:00", "dataItem": "报价容量数据", "importStatus": 1,
                  "importFileName": "", "updateTime": "2026-10-12 17:29:09"}
        context, auth = mock.Mock(), mock.Mock()
        auth.parse_json_response.return_value = {"retCode": "T200", "data": [record]}
        self.assertEqual(record, uploader.verify_remote_status(
            context, auth, data_type="SPOT_RUN_REPORT_QUOTE_CAPACITY", plan=plan, path=self.excel,
        ))
        with self.assertRaisesRegex(RuntimeError, "回读不一致"):
            uploader.verify_remote_status(context, auth, data_type="SPOT_RUN_REPORT_DATA", plan=plan, path=self.pdf)


class DailyPdfUploadResponseTests(unittest.TestCase):
    def setUp(self):
        self.page = mock.Mock()
        self.listeners = {}
        self.page.on.side_effect = lambda name, callback: self.listeners.update({name: callback})
        self.page.remove_listener.side_effect = lambda name, callback: self.listeners.pop(name)
        self.clock = 0
        self.page.wait_for_timeout.side_effect = self.advance
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(uploader.time, "monotonic", side_effect=lambda: self.clock).start()
        self.endpoint = "/datacenter/gd/api/pub/data/report/import/valid/multi"
        self.response = SimpleNamespace(request=SimpleNamespace(method="POST", url=uploader.BASE_URL + self.endpoint))
        self.logs = []

    def advance(self, milliseconds):
        self.clock += milliseconds / 1000

    def wait(self, action, **kwargs):
        return uploader.wait_upload_response(
            self.page, action, endpoint=self.endpoint, label="现货交易日报文件校验",
            logger=self.logs.append, **kwargs,
        )

    def test_listens_before_selection_and_ignores_unrelated_responses(self):
        def action():
            callback = self.listeners["response"]
            callback(SimpleNamespace(request=SimpleNamespace(method="GET", url=self.response.request.url)))
            callback(SimpleNamespace(request=SimpleNamespace(method="POST", url=self.response.request.url + "/other")))
            callback(self.response)
        self.assertIs(self.response, self.wait(action))
        self.page.wait_for_timeout.assert_not_called()
        self.assertEqual({}, self.listeners)

    def test_request_failure_exits_without_waiting_full_timeout(self):
        request = SimpleNamespace(method="POST", url=self.response.request.url, failure="net::ERR_CONNECTION_RESET")
        with self.assertRaisesRegex(RuntimeError, "文件校验网络请求失败.*ERR_CONNECTION_RESET"):
            self.wait(lambda: self.listeners["requestfailed"](request))
        self.page.wait_for_timeout.assert_not_called()
        self.assertEqual({}, self.listeners)

    def test_validation_timeout_names_stage_and_does_not_retry(self):
        action = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "文件校验超时.*尚未执行本文件的全部上传") as raised:
            self.wait(action, timeout_ms=1000)
        action.assert_called_once()
        self.assertIn("Timeout 1000ms", str(raised.exception.__cause__))
        self.assertEqual({}, self.listeners)

    def test_submission_timeout_warns_of_possible_write_instead_of_retrying(self):
        action = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "上传请求可能已提交.*核对网站状态"):
            self.wait(action, timeout_ms=1000, submitted=True)
        action.assert_called_once()

    def test_progress_is_reported_while_waiting_for_slow_response(self):
        def advance(milliseconds):
            self.clock += 5
            if self.clock >= 20:
                self.listeners["response"](self.response)
        self.page.wait_for_timeout.side_effect = advance
        self.assertIs(self.response, self.wait(mock.Mock(), timeout_ms=30000))
        self.assertTrue(any("已等待 15 秒" in line for line in self.logs))

    def test_selection_error_retains_original_exception_and_removes_listeners(self):
        error = ValueError("invalid local file")
        with self.assertRaises(ValueError) as raised:
            self.wait(mock.Mock(side_effect=error))
        self.assertIs(error, raised.exception)
        self.assertEqual({}, self.listeners)


class DailyPdfUploadPageTests(unittest.TestCase):
    def test_page_defaults_to_both_and_builds_correct_mode_argument(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        try:
            page = DailyPdfParserPage(root, ToolPaths(ROOT), TaskRegistry())
            root.update_idletasks()
            self.assertEqual("both", page.mode.get())
            self.assertEqual("解析并上传", page.run_button["text"])
            command = page.build_command(Path("source.pdf"), Path("result.xlsx"))
            self.assertEqual(["--mode", "both"], command[-2:])
            page.mode.set("upload")
            self.assertEqual("上传已有文件", page.run_button["text"])
            self.assertEqual("upload", page.build_command(Path("source.pdf"), Path("result.xlsx"))[-1])
            page.destroy()
        finally:
            root.destroy()
