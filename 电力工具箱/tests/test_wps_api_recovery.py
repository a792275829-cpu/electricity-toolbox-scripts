from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from toolbox.runtime import load_module


def load_writer():
    return load_module(
        "wps_api_recovery_test",
        Path(__file__).resolve().parents[2] / "wps自动" / "wps_excel_to_kdocs_gui.py",
    )


class WpsOperationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_writer()
        self.page = mock.Mock()
        self.ready = {"ready": True, "title": "Test document", "url": "about:blank"}

    def test_waits_again_when_api_disappears_before_evaluate(self) -> None:
        self.page.evaluate.side_effect = [
            RuntimeError("WPS_API_NOT_READY: document API changed"),
            {"saved": True},
        ]
        log = mock.Mock()
        with mock.patch.object(self.module, "wait_for_wps_ready", return_value=self.ready) as wait:
            result = self.module.evaluate_wps_operation(self.page, "script", [1], log, 180)
        self.assertEqual(result, {"saved": True})
        self.assertEqual(wait.call_args_list, [
            mock.call(self.page, log, timeout_seconds=180),
            mock.call(self.page, log, timeout_seconds=60),
        ])
        self.assertEqual(self.page.evaluate.call_count, 2)
        log.assert_called_once()

    def test_failed_retry_readiness_blocks_second_write_and_preserves_first_error(self) -> None:
        first_error = RuntimeError("Execution context was destroyed during navigation")
        self.page.evaluate.side_effect = first_error
        with mock.patch.object(self.module, "wait_for_wps_ready", side_effect=[
            self.ready, {"ready": False, "title": "Login", "url": "about:blank"},
        ]):
            with self.assertRaisesRegex(RuntimeError, "首次操作失败原因：Execution context") as caught:
                self.module.evaluate_wps_operation(self.page, "script", None, mock.Mock(), 45)
        self.assertIs(caught.exception.__cause__, first_error)
        self.page.evaluate.assert_called_once()

    def test_initial_not_ready_never_evaluates_operation(self) -> None:
        with mock.patch.object(self.module, "wait_for_wps_ready", return_value={"ready": False}):
            with self.assertRaisesRegex(RuntimeError, "确认已登录且能访问该表格"):
                self.module.evaluate_wps_operation(self.page, "script", None, mock.Mock(), 120)
        self.page.evaluate.assert_not_called()

    def test_unrelated_type_error_is_not_retried(self) -> None:
        error = RuntimeError("TypeError: Cannot read properties of undefined (reading 'Range')")
        self.page.evaluate.side_effect = error
        with mock.patch.object(self.module, "wait_for_wps_ready", return_value=self.ready):
            with self.assertRaises(RuntimeError) as caught:
                self.module.evaluate_wps_operation(self.page, "script", None, mock.Mock(), 120)
        self.assertIs(caught.exception, error)
        self.page.evaluate.assert_called_once()
        self.page.wait_for_timeout.assert_not_called()

    def test_api_recovery_is_bounded_and_keeps_first_error(self) -> None:
        self.page.evaluate.side_effect = [
            RuntimeError("WPS_API_NOT_READY: first failure"),
            RuntimeError("WPS_API_NOT_READY: second failure"),
            RuntimeError("WPS_API_NOT_READY: third failure"),
        ]
        with mock.patch.object(self.module, "wait_for_wps_ready", return_value=self.ready):
            with self.assertRaisesRegex(RuntimeError, "重试 3 次.*first failure"):
                self.module.evaluate_wps_operation(self.page, "script", None, mock.Mock(), 180)
        self.assertEqual(self.page.evaluate.call_count, 3)
        self.assertEqual(self.page.wait_for_timeout.call_count, 2)

    def test_null_source_cell_is_a_successful_read(self) -> None:
        self.page.evaluate.return_value = None
        with mock.patch.object(self.module, "wait_for_wps_ready", return_value=self.ready):
            result = self.module.evaluate_wps_operation(self.page, "script", None, mock.Mock(), 120)
        self.assertIsNone(result)
        self.page.evaluate.assert_called_once()


FIXTURE_HTML = """
<meta charset="utf-8">
<title>WPS regression fixture</title>
<script>
  window.cells = JSON.parse(sessionStorage.getItem('cells') || '{}');
  window.writes = 0;
  window.saves = 0;
  const worksheets = (name) => ({
    Name: '目标',
    Range: (address) => ({
      MergeCells: false,
      get Value() { return window.cells[address] ?? null; },
      set Value(value) { window.writes++; window.cells[address] = value; },
      get Value2() { return this.Value; },
      get Text() { return this.Value; }
    })
  });
  worksheets.Count = 1;
  window.testApp = {Ready: Promise.resolve(true), Worksheets: worksheets};
  window.WPSOpenApi = {
    EtApplication: () => window.testApp,
    save: async () => {
      window.saves++;
      sessionStorage.setItem('cells', JSON.stringify(window.cells));
    }
  };
</script>
"""


class WpsBrowserRecoveryTests(unittest.TestCase):
    """Run the production JavaScript in a real browser against a local API fixture."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_writer()
        if cls.module.sync_playwright is None:
            raise unittest.SkipTest("Playwright is unavailable")
        cls.playwright = cls.module.sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self) -> None:
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.context.route(
            "https://wps-test.invalid/**",
            lambda route: route.fulfill(content_type="text/html", body=FIXTURE_HTML),
        )
        self.page.goto("https://wps-test.invalid/document")
        manager = mock.MagicMock()
        manager.__enter__.return_value = self.playwright
        self.patchers = [
            mock.patch.object(self.module, "sync_playwright", return_value=manager),
            mock.patch.object(self.module, "open_browser_context", return_value=(self.browser, self.context, False)),
            mock.patch.object(self.module, "find_or_open_page", return_value=self.page),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.context.close)

    def mappings(self):
        values = [[[0.125]], [["2026-09-27 08:30:00"]], [[""]], [[False]], [[0]], [[1, "文本"], [2, ""]]]
        return [
            self.module.PreparedMapping(
                index=index, kdocs_url=self.page.url, workbook_path="fixture.xlsx",
                source_sheet="来源", source_range="A1", target_sheet="目标",
                target_start=f"A{index * 3}", target_range=f"A{index * 3}" if index < 6 else "A18:B19",
                rows=len(value), cols=len(value[0]), values=value,
            )
            for index, value in enumerate(values, start=1)
        ]

    def test_six_regions_recover_from_missing_api_save_and_read_back_after_reload(self) -> None:
        original_evaluate = self.page.evaluate
        operations = []

        def disappearing_api(script, payload=None):
            if "const results = [];" in script:
                operations.append(script)
                if len(operations) == 1:
                    original_evaluate("""() => {
                      const savedApi = window.WPSOpenApi;
                      window.WPSOpenApi = undefined;
                      setTimeout(() => { window.WPSOpenApi = savedApi; }, 50);
                    }""")
            return original_evaluate(script, payload)

        prepared = self.mappings()
        log = mock.Mock()
        with mock.patch.object(self.page, "evaluate", side_effect=disappearing_api):
            result = self.module.write_to_wps(self.page.url, prepared, log, browser_mode="cdp")
        self.assertTrue(result["saved"])
        self.assertEqual(len(operations), 2)
        self.assertEqual(len(result["results"]), 6)
        self.assertEqual(self.page.evaluate("() => [window.writes, window.saves]"), [6, 1])
        self.assertTrue(any("第 2/3 次" in call.args[0] for call in log.call_args_list))
        for item, entry in zip(prepared, result["results"]):
            self.assertEqual(self.module.compare_matrix(item.values, entry["value"], item.rows, item.cols), [])
        self.page.reload()
        for item in prepared:
            actual = self.module.read_online_range_values(
                self.page.url, item.target_sheet, item.target_range, item.rows, item.cols,
                "cdp", "http://127.0.0.1:9222", lambda _: None,
            )
            self.assertEqual(actual, item.values)
        info = self.module.read_online_document_info(self.page.url, "cdp", "", lambda _: None)
        self.assertEqual(info, {"title": "WPS regression fixture", "sheets": ["目标"]})

    def test_batch_reads_saved_regions_using_one_connection(self) -> None:
        prepared = self.mappings()
        self.module.write_to_wps(self.page.url, prepared, lambda _: None, browser_mode="cdp")
        self.page.reload()
        requests = [(self.page.url, item.target_sheet, item.target_range, item.rows, item.cols)
                    for item in prepared]
        with mock.patch.object(self.module, "open_browser_context", return_value=(self.browser, self.context, False)) as opened:
            actual = self.module.read_online_ranges(requests, "cdp", "", lambda _: None)
        opened.assert_called_once()
        self.assertEqual(actual, [item.values for item in prepared])
        self.assertEqual(self.page.evaluate("() => window.writes"), 0)

    def test_ready_probe_rejects_api_replaced_while_ready_is_awaited(self) -> None:
        self.page.evaluate("""() => {
          window.testApp.Ready = {then(resolve) {
            window.WPSOpenApi = undefined;
            resolve(true);
          }};
        }""")
        result = self.module.wait_for_wps_ready(self.page, lambda _: None, 1)
        self.assertFalse(result["ready"])

    def test_resolved_document_promise_does_not_hide_application_failure(self) -> None:
        self.page.evaluate("""() => {
          window.WPSOpenApi.EtApplication = () => { throw new Error('app unavailable'); };
          window.WPSOpenApi.documentReadyPromise = Promise.resolve(true);
        }""")
        result = self.module.wait_for_wps_ready(self.page, lambda _: None, 1)
        self.assertFalse(result["ready"])

    def test_missing_save_api_never_assigns_a_cell(self) -> None:
        self.page.evaluate("() => { delete window.WPSOpenApi.save; }")
        with self.assertRaisesRegex(RuntimeError, "表格保存功能尚未就绪"):
            self.module.write_to_wps(self.page.url, self.mappings(), lambda _: None, browser_mode="cdp")
        self.assertEqual(self.page.evaluate("() => [window.writes, window.saves]"), [0, 0])


if __name__ == "__main__":
    unittest.main()
