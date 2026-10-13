from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from toolbox.runtime import load_module


class GroupUploadShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module(
            "group_upload_shutdown_test",
            Path(__file__).resolve().parents[2] / "集团每日上传/upload_daily_report.py",
        )

    def test_stops_pages_before_context_and_cancels_slow_shutdown_reminder(self) -> None:
        module = self.module
        context = mock.Mock()
        page = mock.Mock()
        context.pages = [page]
        order = []
        page.close.side_effect = lambda **kwargs: order.append("page")
        context.close.side_effect = lambda: order.append("context")
        logs = []
        with (
            mock.patch.object(module, "log", side_effect=logs.append),
            mock.patch.object(module.threading, "Timer") as timer,
        ):
            module.close_upload_context(context)
            timer.call_args.args[1]()

        self.assertEqual(order, ["page", "context"])
        page.close.assert_called_once_with(run_before_unload=False)
        timer.return_value.start.assert_called_once()
        timer.return_value.cancel.assert_called_once()
        self.assertTrue(any("仍在等待" in line for line in logs))

    def test_page_close_failure_still_closes_context(self) -> None:
        module = self.module
        page = mock.Mock()
        page.close.side_effect = module.PlaywrightError("page already gone")
        context = mock.Mock(pages=[page])
        with mock.patch.object(module, "log"), mock.patch.object(module.threading, "Timer"):
            module.close_upload_context(context)
        context.close.assert_called_once()

    def test_cleanup_failure_preserves_first_upload_error(self) -> None:
        module = self.module
        context = mock.Mock(pages=[])
        context.close.side_effect = module.PlaywrightError("cleanup failed")
        with (
            mock.patch.object(module, "log"),
            mock.patch.object(module.threading, "Timer") as timer,
            self.assertRaisesRegex(RuntimeError, "first upload error"),
        ):
            try:
                raise RuntimeError("first upload error")
            finally:
                module.close_upload_context(context)
        timer.return_value.cancel.assert_called_once()

    def test_cleanup_failure_after_success_is_reported(self) -> None:
        module = self.module
        context = mock.Mock(pages=[])
        context.close.side_effect = module.PlaywrightError("cleanup failed")
        with (
            mock.patch.object(module, "log"),
            mock.patch.object(module.threading, "Timer"),
            self.assertRaisesRegex(module.PlaywrightError, "cleanup failed"),
        ):
            module.close_upload_context(context)

    def test_api_upload_saves_closes_and_releases_lock_in_order(self) -> None:
        module = self.module
        uploads = [module.UploadFile(Path("能销-20261008.xlsx"), "能销", "2026-10-08")]
        context = mock.Mock(pages=[])
        manager = mock.MagicMock()
        events = []
        context.close.side_effect = lambda: events.append("close")
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "auth_state.json.lock"
            with (
                mock.patch.object(module, "AUTH_LOCK_PATH", lock_path),
                mock.patch.object(module, "log", side_effect=events.append),
                mock.patch.object(module, "prepare_upload_files", return_value=("2026-10-08", uploads)),
                mock.patch.object(module, "load_config", return_value=module.Config()),
                mock.patch.object(module, "sync_playwright", return_value=manager),
                mock.patch.object(module, "launch_context", return_value=context),
                mock.patch.object(module, "update_province_excel_for_upload", return_value=uploads),
                mock.patch.object(module, "get_daily_status", return_value={"dataStatus": 1}),
                mock.patch.object(module, "import_daily_file") as import_file,
                mock.patch.object(module, "create_daily_report", side_effect=lambda *a, **kw: events.append("report")),
                mock.patch.object(module, "save_auth_state", side_effect=lambda *a: events.append("save")),
                mock.patch.object(module.threading, "Timer"),
            ):
                module.run_api_upload([uploads[0].path])
            self.assertFalse(lock_path.exists())
        import_file.assert_not_called()
        self.assertLess(events.index("report"), events.index("save"))
        self.assertLess(events.index("save"), events.index("close"))
        self.assertLess(events.index("close"), events.index("集团上传登录态锁已释放。"))
        self.assertEqual(events[-1], "\n处理完成。")
