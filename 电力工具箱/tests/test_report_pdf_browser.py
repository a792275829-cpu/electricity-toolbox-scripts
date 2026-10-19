from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "南方分公司电力市场日报"))
from gd_power_report import pdf_export, pipeline, legacy_print_report


class ReportPdfBrowserTests(unittest.TestCase):
    def test_combined_export_starts_one_browser_and_closes_on_failure(self):
        with mock.patch.object(pdf_export, "sync_playwright") as playwright, \
             mock.patch.object(pipeline, "render_legacy_concise_report", return_value=Path("phone.html")), \
             mock.patch.object(pipeline, "render_print_report", return_value=Path("print.html")), \
             mock.patch.object(pipeline, "export_legacy_concise_pdf", return_value=Path("phone.pdf")) as phone, \
             mock.patch.object(pipeline, "export_print_pdf", side_effect=RuntimeError("print failed")) as printing:
            sdk = playwright.return_value.__enter__.return_value
            browser = sdk.chromium.launch.return_value
            with self.assertRaisesRegex(RuntimeError, "print failed"):
                pipeline._render_concise_outputs(Path("."), {}, lambda _: None)
            sdk.chromium.launch.assert_called_once_with(headless=True)
            phone.assert_called_once_with(Path("phone.html"), browser=browser)
            printing.assert_called_once_with(Path("print.html"), browser=browser)
            browser.close.assert_called_once()

    def test_failed_page_export_keeps_callers_browser_open(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "print.html"
            source.write_text("<main>test</main>")
            browser = mock.Mock()
            page = browser.new_page.return_value
            page.pdf.side_effect = RuntimeError("pdf failed")
            with mock.patch.object(legacy_print_report, "_sync_metric_card_heights"):
                with self.assertRaisesRegex(RuntimeError, "pdf failed"):
                    legacy_print_report.export_print_pdf(source, browser=browser)
            page.close.assert_called_once()
            browser.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
