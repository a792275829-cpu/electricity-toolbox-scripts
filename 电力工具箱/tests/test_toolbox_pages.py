from __future__ import annotations
import importlib

import gc
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
import runpy
import os
import shutil
from contextlib import ExitStack
from datetime import date
from datetime import timedelta
from pathlib import Path
from unittest import mock

def destroy_tk(root: tk.Misc) -> None:
    try:
        root.update_idletasks()
    except tk.TclError:
        pass
    root.destroy()


class PageAdapterTests(unittest.TestCase):
    """Toolbox page adapters: commands, mapping panel, WPS page and directory opening."""
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.runtime import TaskRegistry, ToolPaths

        self.paths = ToolPaths(Path(__file__).resolve().parents[2])
        self.registry = TaskRegistry()

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            # Finish asynchronous page imports before destroying their Tk owner.
            for child in self.root.winfo_children():
                loader = getattr(child, "_writer_loader", None)
                if loader is not None and loader.ident is not None:
                    loader.join(5)
                    self.assertFalse(loader.is_alive(), "WPS page import did not finish")
            destroy_tk(self.root)
            gc.collect()  # Tk variables must be finalized on the main thread.

    def test_core_pages_build_expected_paths_and_commands(self) -> None:
        from toolbox.pages import (
            OnlineEnergyPage,
            SummaryUploadPage,
            TradeAnalysisPage,
        )

        online = OnlineEnergyPage(self.root, self.paths, self.registry)
        trade = TradeAnalysisPage(self.root, self.paths, self.registry)
        summary = SummaryUploadPage(self.root, self.paths, self.registry)

        export_command = online.build_export_command("2026-06-09")
        self.assertEqual(export_command[-1], "2026-06-09")
        self.assertEqual(Path(export_command[-2]), self.paths.online_energy)
        self.assertTrue(trade.output_dir.get())
        self.assertEqual(summary.default_output_for(Path("C:/data")).suffix, ".xlsx")

    def test_online_energy_defaults_to_previous_day(self) -> None:
        from toolbox.pages import OnlineEnergyPage

        page = OnlineEnergyPage(self.root, self.paths, self.registry)
        expected = date.today() - timedelta(days=1)

        self.assertEqual(page.run_date.get(), expected.isoformat())

    def test_trade_auto_match_finds_latest_d_and_d_minus_two_files(self) -> None:
        from toolbox.pages import TradeAnalysisPage

        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            older_d = downloads / "出清情况（分公司2026.6.17).xlsx"
            latest_d = downloads / "出清情况（分公司2026.6.17) (1).xlsx"
            latest_d_minus_two = downloads / "出清情况（分公司2026.6.15) (1).xlsx"
            unrelated = downloads / "市场代购电量调整系数(20260617).xlsx"
            for index, path in enumerate(
                [older_d, latest_d, latest_d_minus_two, unrelated], start=1
            ):
                path.write_text("x", encoding="utf-8")
                os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
            os.utime(latest_d, (1_700_000_100, 1_700_000_100))

            matches = TradeAnalysisPage.find_clearing_files_for_dates(
                date(2026, 6, 17),
                downloads,
            )

        self.assertEqual(matches, [latest_d, latest_d_minus_two])

    def test_review_folder_match_uses_d_plus_one_then_latest_fallback(self) -> None:
        from toolbox.pages import default_review_folder_for_date

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "6-18"
            fallback = root / "6-17"
            target.mkdir()
            fallback.mkdir()
            os.utime(fallback, (1_700_000_100, 1_700_000_100))

            self.assertEqual(
                default_review_folder_for_date(date(2026, 6, 17), root),
                target,
            )

            target.rmdir()
            self.assertEqual(
                default_review_folder_for_date(date(2026, 6, 17), root),
                fallback,
            )

    def test_summary_upload_page_auto_fills_d_plus_one_folder(self) -> None:
        from toolbox.pages import SummaryUploadPage

        with tempfile.TemporaryDirectory() as directory:
            review_root = Path(directory)
            target = review_root / "6-18"
            target.mkdir()
            page = SummaryUploadPage(
                self.root, self.paths, self.registry,
                today=date(2026, 6, 17), review_root=review_root,
            )
            self.assertEqual(Path(page.input_dir.get()), target)
            self.assertEqual(Path(page.output_file.get()), target / "6-18_汇总.xlsx")

    def test_report_page_maps_source_dates_and_builds_word_command(self) -> None:
        from toolbox.pages import ReportPage

        page = ReportPage(self.root, self.paths, self.registry)
        self.assertEqual(
            page.source_dates("2026-06-09"),
            (date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 7)),
        )

        command = page.build_word_command(
            "2026-06-09",
            Path("C:/online.xlsx"),
            Path("C:/day-ahead.xlsx"),
            Path("C:/daily.xlsx"),
            None,
        )
        self.assertIn("--online-workbook", command)
        self.assertIn("--day-ahead-workbook", command)
        self.assertIn("--daily-clearing-workbook", command)

    def test_upload_pages_build_non_shell_commands(self) -> None:
        from toolbox.pages import (
            GuangdongPricePage,
            GroupUploadPage,
            MarketTableUpdatePage,
            SummaryUploadPage,
        )

        private = SummaryUploadPage(self.root, self.paths, self.registry)
        group = GroupUploadPage(self.root, self.paths, self.registry)
        market = MarketTableUpdatePage(self.root, self.paths, self.registry)
        guangdong = GuangdongPricePage(
            self.root, self.paths, self.registry, today=date(2026, 7, 11)
        )

        plan_command = private.build_upload_command("--plan", Path(r"C:\review"))
        self.assertEqual(plan_command[-3:], ["--plan", "--source", r"C:\review"])
        self.assertIn("--execute", private.build_upload_command("--execute", Path(r"C:\review")))

        group_command = group.build_upload_command(
            [Path(r"C:\data\广东-能销数据-20260609.xlsx")],
            force=True,
        )
        self.assertEqual(Path(group_command[1]), self.paths.group_upload)
        self.assertIn("--force", group_command)

        market_command = market.build_update_command(
            date(2026, 7, 2),
            Path(r"C:\data\现货统调市场出清、结算数据2026.xlsx"),
        )
        self.assertEqual(Path(market_command[1]), self.paths.market_table_update)
        self.assertIn("--date", market_command)
        self.assertIn("2026-07-02", market_command)

        self.assertEqual(guangdong.run_date.get(), "2026-07-11")
        self.assertEqual(
            Path(guangdong.prediction_input.get()),
            self.paths.guangdong_price_output_dir / "广东电价数据总表.xlsx",
        )
        prediction_command = guangdong.build_prediction_command(
            Path("/tmp/model.xlsx"), Path("/tmp/prediction"), 1, 3
        )
        self.assertEqual(Path(prediction_command[1]), self.paths.guangdong_price_forecast)
        self.assertEqual(
            prediction_command[-4:],
            ["--forecast-days", "1", "--training-months", "3"],
        )

    def test_market_mapping_panel_is_complete_and_scrollable(self) -> None:
        from toolbox.pages import MarketTableUpdatePage

        page = MarketTableUpdatePage(self.root, self.paths, self.registry)
        text = page.mapping_text.get("1.0", "end")

        self.assertEqual(page.mapping_text.cget("state"), "disabled")
        self.assertEqual(page.mapping_text.cget("wrap"), "word")
        self.assertTrue(page.mapping_text.cget("yscrollcommand"))
        self.assertTrue(page.mapping_scrollbar.cget("command"))
        for index, description in enumerate(
            page.MAPPING_DESCRIPTIONS, start=1
        ):
            self.assertIn(f"{index}. {description}", text)

    def test_wps_writer_page_embeds_writer_frame(self) -> None:
        from toolbox.pages import WpsWriterPage

        page = WpsWriterPage(self.root, self.paths, self.registry)
        deadline = time.monotonic() + 5
        while not hasattr(page, "writer_frame") and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(hasattr(page, "writer_frame"))
        self.assertIs(page.writer_frame.task_registry, self.registry)
        self.assertFalse(hasattr(page, "open_button"))
        self.assertFalse(hasattr(page, "build_open_command"))

    def test_wps_writer_keeps_document_configs_visible_when_resized(self) -> None:
        from toolbox.runtime import load_module

        module = load_module("toolbox_wps_writer_layout_test", self.paths.wps_writer)
        frame = module.WpsWriterFrame(self.root)
        frame.pack(fill="both", expand=True)
        self.root.geometry("520x360")
        self.root.update_idletasks()

        content_root = frame.winfo_children()[0]
        self.assertGreaterEqual(content_root.grid_rowconfigure(1)["minsize"], 220)

    def test_wps_writer_imports_exported_config_payload(self) -> None:
        from toolbox.runtime import load_module

        module = importlib.import_module('toolbox_shared.wps_service')
        payload = {
            "schema": "wps_excel_to_kdocs_config_export",
            "version": 1,
            "exported_at": "2026-06-29T16:45:11",
            "recent_urls": ["https://www.kdocs.cn/l/target"],
            "browser_mode": "cdp",
            "cdp_url": "http://127.0.0.1:9222",
            "configs": [
                {
                    "name": "上网电量写入",
                    "source_type": "excel",
                    "source_url": "",
                    "kdocs_url": "https://www.kdocs.cn/l/target",
                    "local_file": "C:/Users/lllg/Desktop/source.xlsx",
                    "regions": [
                        {
                            "source_sheet": "汇总",
                            "source_start": "A3",
                            "source_end": "C26",
                            "target_sheet": "汕头",
                            "target_start": "T3",
                            "target_end": "V26",
                        }
                    ],
                }
            ],
        }

        runtime_config = module.runtime_config_from_export_payload(payload)

        self.assertEqual(runtime_config["browser_mode"], "cdp")
        self.assertEqual(runtime_config["cdp_url"], "http://127.0.0.1:9222")
        self.assertEqual(runtime_config["configs"][0]["name"], "上网电量写入")
        self.assertEqual(runtime_config["configs"][0]["regions"][0]["target_sheet"], "汕头")

    def test_wps_writer_exports_runtime_config_with_schema(self) -> None:
        from toolbox.runtime import load_module

        module = importlib.import_module('toolbox_shared.wps_service')
        runtime_config = {
            "recent_urls": ["https://www.kdocs.cn/l/target"],
            "browser_mode": "persistent",
            "cdp_url": "http://127.0.0.1:9222",
            "configs": [
                {
                    "name": "Config",
                    "source_type": "kdocs",
                    "source_url": "https://www.kdocs.cn/l/source",
                    "kdocs_url": "https://www.kdocs.cn/l/target",
                    "local_file": "",
                    "regions": [
                        {
                            "source_sheet": "Sheet1",
                            "source_start": "B4",
                            "source_end": "C27",
                            "target_sheet": "Sheet2",
                            "target_start": "B3",
                            "target_end": "C26",
                        }
                    ],
                }
            ],
        }

        exported = module.build_config_export(runtime_config, exported_at="2026-06-29T16:45:11")

        self.assertEqual(exported["schema"], "wps_excel_to_kdocs_config_export")
        self.assertEqual(exported["version"], 1)
        self.assertEqual(exported["exported_at"], "2026-06-29T16:45:11")
        self.assertEqual(exported["configs"][0]["source_type"], "kdocs")
        self.assertEqual(module.runtime_config_from_export_payload(exported), runtime_config)

    def test_open_directory_uses_macos_open_command(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS-specific directory opener")

        from toolbox.pages import _open_directory

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output"
            with mock.patch("subprocess.run") as run:
                _open_directory(target)

            self.assertTrue(target.is_dir())
            run.assert_called_once_with(["open", str(target)], check=False)

    def test_report_gui_open_directory_uses_macos_open_command(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS-specific directory opener")

        from toolbox.runtime import load_module

        module = load_module("report_gui_open_directory_test", self.paths.report_gui)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output"
            with mock.patch("subprocess.run") as run:
                module.open_directory(target)

            self.assertTrue(target.is_dir())
            run.assert_called_once_with(["open", str(target)], check=False)

    def test_trade_analysis_open_directory_uses_macos_open_command(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS-specific directory opener")

        from toolbox.runtime import load_module

        module = load_module("trade_analysis_open_directory_test", self.paths.trade_analysis)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output"
            with mock.patch("subprocess.run") as run:
                module.open_directory(target)

            self.assertTrue(target.is_dir())
            run.assert_called_once_with(["open", str(target)], check=False)

    def test_default_page_factories_create_all_frames(self) -> None:
        from toolbox.app import PAGE_NAMES
        from toolbox.pages import page_factories

        factories = page_factories()
        self.assertEqual(list(factories), list(PAGE_NAMES))
        frames = [
            factories[name](self.root, self.paths, self.registry)
            for name in PAGE_NAMES
        ]
        self.assertEqual(len(frames), len(PAGE_NAMES))
        self.assertEqual(len({str(frame) for frame in frames}), len(PAGE_NAMES))
        self.assertIn("WPS写入工具", PAGE_NAMES)
        self.assertIn("市场表更新", PAGE_NAMES)
        self.assertIn("现货价格分析表更新", PAGE_NAMES)
        self.assertIn("交易系统信息更新", PAGE_NAMES)
        self.assertIn("广东电价预测", PAGE_NAMES)
        self.assertIn("广东运行日报解析上传", PAGE_NAMES)


if __name__ == "__main__":
    unittest.main()
