from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest
import runpy
import os
import shutil
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


class RuntimeTests(unittest.TestCase):
    def test_tool_paths_resolve_from_workspace(self) -> None:
        from toolbox.runtime import ToolPaths

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            paths = ToolPaths(workspace)

            self.assertEqual(paths.workspace, workspace.resolve())
            self.assertEqual(
                paths.online_energy,
                workspace.resolve() / "上网电量抓取" / "export_online_energy.py",
            )
            self.assertEqual(
                paths.private_uploader,
                workspace.resolve()
                / "电力工具脚本"
                / "private-data-uploader-tool"
                / "scripts"
                / "upload-private-data.mjs",
            )
            self.assertEqual(
                paths.wps_writer,
                workspace.resolve() / "wps自动" / "wps_excel_to_kdocs_gui.py",
            )

    def test_load_module_rejects_missing_file(self) -> None:
        from toolbox.runtime import load_module

        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.py"
            with self.assertRaisesRegex(FileNotFoundError, "missing.py"):
                load_module("missing_tool", missing)

    def test_utf8_environment_sets_python_encoding(self) -> None:
        from toolbox.runtime import utf8_environment

        environment = utf8_environment()

        self.assertEqual(environment["PYTHONUTF8"], "1")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")

    def test_python_executable_uses_current_interpreter_on_macos(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS-specific runtime expectation")

        from toolbox.runtime import python_executable, pythonw_executable

        self.assertEqual(Path(python_executable()).resolve(), Path(sys.executable).resolve())
        self.assertEqual(Path(pythonw_executable()).resolve(), Path(sys.executable).resolve())

    def test_node_executable_uses_plain_node_on_macos(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS-specific runtime expectation")

        from toolbox.runtime import node_executable

        self.assertEqual(node_executable(), "node")

    def test_task_registry_tracks_and_terminates_owned_process(self) -> None:
        from toolbox.runtime import TaskRegistry

        registry = TaskRegistry()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())

        registry.register_process(process)
        self.assertTrue(registry.has_running_tasks())

        registry.terminate_all()
        deadline = time.time() + 5
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.05)

        self.assertIsNotNone(process.poll())
        self.assertFalse(registry.has_running_tasks())


class ToolboxAppTests(unittest.TestCase):
    def create_root(self) -> tk.Tk:
        try:
            root = tk.Tk()
            root.withdraw()
            return root
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def test_app_creates_pages_and_switches_without_recreating(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        created: dict[str, tk.Frame] = {}

        def make_factory(name: str):
            def factory(parent, _paths, _registry):
                frame = tk.Frame(parent, name=f"page_{len(created)}")
                frame.marker = tk.StringVar(master=frame, value=name)  # type: ignore[attr-defined]
                created[name] = frame
                return frame

            return factory

        factories = {name: make_factory(name) for name in PAGE_NAMES}
        app = ToolboxApp(page_factories=factories)
        app.withdraw()
        self.addCleanup(destroy_tk, app)

        self.assertEqual(list(app.pages), list(PAGE_NAMES))
        first_page = app.pages["导出上网电量"]
        app.show_page("电量汇总")
        app.show_page("导出上网电量")

        self.assertEqual(app.current_page, "导出上网电量")
        self.assertIs(app.pages["导出上网电量"], first_page)
        self.assertEqual(first_page.marker.get(), "导出上网电量")  # type: ignore[attr-defined]

    def test_tool_page_keeps_its_own_log_and_busy_state(self) -> None:
        from toolbox.runtime import TaskRegistry
        from toolbox.widgets import ToolPage

        root = self.create_root()
        self.addCleanup(destroy_tk, root)
        page = ToolPage(root, registry=TaskRegistry())
        page.append_log("第一条日志")
        page.set_busy(True, "运行中")
        root.update()

        self.assertIn("第一条日志", page.log_text.get("1.0", "end"))
        self.assertTrue(page.busy)
        self.assertEqual(page.status_var.get(), "运行中")


class PageAdapterTests(unittest.TestCase):
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
            destroy_tk(self.root)

    def test_core_pages_build_expected_paths_and_commands(self) -> None:
        from toolbox.pages import (
            OnlineEnergyPage,
            SummaryPage,
            TradeAnalysisPage,
        )

        online = OnlineEnergyPage(self.root, self.paths, self.registry)
        trade = TradeAnalysisPage(self.root, self.paths, self.registry)
        summary = SummaryPage(self.root, self.paths, self.registry)

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

    def test_summary_and_private_pages_auto_fill_d_plus_one_folder(self) -> None:
        from toolbox.pages import PrivateUploadPage, SummaryPage

        with tempfile.TemporaryDirectory() as directory:
            review_root = Path(directory)
            target = review_root / "6-18"
            target.mkdir()

            summary = SummaryPage(
                self.root,
                self.paths,
                self.registry,
                today=date(2026, 6, 17),
                review_root=review_root,
            )
            private = PrivateUploadPage(
                self.root,
                self.paths,
                self.registry,
                today=date(2026, 6, 17),
                review_root=review_root,
            )

        self.assertEqual(Path(summary.input_dir.get()), target)
        self.assertEqual(Path(private.source_folder.get()), target)

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
        from toolbox.pages import GroupUploadPage, PrivateUploadPage

        private = PrivateUploadPage(self.root, self.paths, self.registry)
        group = GroupUploadPage(self.root, self.paths, self.registry)

        plan_command = private.build_command("--plan", Path(r"C:\review"))
        self.assertEqual(plan_command[-3:], ["--plan", "--source", r"C:\review"])
        self.assertIn("--execute", private.build_command("--execute", Path(r"C:\review")))

        group_command = group.build_upload_command(
            [Path(r"C:\data\广东-能销数据-20260609.xlsx")],
            force=True,
        )
        self.assertEqual(Path(group_command[1]), self.paths.group_upload)
        self.assertIn("--force", group_command)

    def test_wps_writer_page_embeds_writer_frame(self) -> None:
        from toolbox.pages import WpsWriterPage

        page = WpsWriterPage(self.root, self.paths, self.registry)

        self.assertTrue(hasattr(page, "writer_frame"))
        self.assertFalse(hasattr(page, "open_button"))
        self.assertFalse(hasattr(page, "build_open_command"))

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

    def test_group_upload_auto_selects_latest_d_minus_two_energy_file(self) -> None:
        from toolbox.pages import GroupUploadPage

        with tempfile.TemporaryDirectory() as directory:
            upload_dir = Path(directory)
            older = upload_dir / "广东-能销数据-20260615.xlsx"
            latest = upload_dir / "广东-能销数据-20260615-新版.xlsx"
            other_date = upload_dir / "广东-能销数据-20260616.xlsx"
            province_template = upload_dir / "广东-省内数据-20260614.xlsx"
            for index, path in enumerate(
                [older, latest, other_date, province_template], start=1
            ):
                path.write_text("x", encoding="utf-8")
                os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
            os.utime(latest, (1_700_000_100, 1_700_000_100))

            match = GroupUploadPage.find_latest_energy_file_for_date(
                date(2026, 6, 17),
                upload_dir,
            )

        self.assertEqual(match, latest)

    def test_group_upload_page_initializes_with_d_minus_two_energy_file(self) -> None:
        from toolbox.pages import GroupUploadPage

        with tempfile.TemporaryDirectory() as directory:
            upload_dir = Path(directory)
            energy = upload_dir / "广东-能销数据-20260615.xlsx"
            province_template = upload_dir / "广东-省内数据-20260614.xlsx"
            energy.write_text("x", encoding="utf-8")
            province_template.write_text("x", encoding="utf-8")
            paths = type(
                "Paths",
                (),
                {
                    "group_upload": self.paths.group_upload,
                    "group_upload_dir": upload_dir,
                },
            )()

            page = GroupUploadPage(
                self.root,
                paths,  # type: ignore[arg-type]
                self.registry,
                today=date(2026, 6, 17),
            )

        self.assertEqual(page.selected_paths, [energy])
        self.assertIn("能销数据", page.selection_var.get())

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


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(__file__).resolve().parent.parent
        self.script_root = self.workspace.parent

    def test_pythonw_launcher_can_be_loaded_without_starting_mainloop(self) -> None:
        old_cwd = Path.cwd()
        old_sys_path = list(sys.path)
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                sys.path[:] = [
                    item
                    for item in sys.path
                    if item and Path(item).resolve() != self.workspace.resolve()
                ]
                for name in list(sys.modules):
                    if name == "toolbox" or name.startswith("toolbox."):
                        sys.modules.pop(name)
                namespace = runpy.run_path(
                    str(self.workspace / "toolbox_launcher.pyw"),
                    run_name="toolbox_smoke",
                )
            finally:
                sys.path[:] = old_sys_path
                os.chdir(old_cwd)
        self.assertIn("main", namespace)

    def test_default_app_workspace_is_script_collection_root(self) -> None:
        from toolbox.app import default_workspace

        workspace = default_workspace()

        self.assertEqual(self.workspace.parent.resolve(), workspace)
        self.assertTrue((workspace / "wps自动" / "wps_excel_to_kdocs_gui.py").is_file())

    def test_root_keeps_only_hidden_vbs_launcher_as_file(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows launcher layout expectation")
        root_files = [item for item in self.script_root.iterdir() if item.is_file()]
        self.assertEqual(1, len(root_files), [item.name for item in root_files])
        self.assertEqual(".vbs", root_files[0].suffix.lower())

    def test_toolbox_bundle_contains_python_entrypoint(self) -> None:
        self.assertTrue((self.workspace / "toolbox_launcher.pyw").is_file())
        self.assertTrue((self.workspace / "toolbox" / "app.py").is_file())
    def test_vbs_launcher_runs_powershell_hidden_and_can_move(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows launcher smoke test")
        source = next(item for item in self.script_root.iterdir() if item.is_file() and item.suffix.lower() == ".vbs")
        content = source.read_text(encoding="utf-8")

        self.assertIn("TOOLBOX_SMOKE", content)
        self.assertIn("waitOnReturn = False", content)
        self.assertIn("shell.Run command, 0, waitOnReturn", content)
        self.assertNotIn("C:\\Users\\lllg\\Desktop\\脚本汇总", content)

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "moved-toolbox.vbs"
            shutil.copy2(source, copied)
            result = subprocess.run(
                ["cscript.exe", "//nologo", str(copied)],
                cwd=directory,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TOOLBOX_SMOKE": "1"},
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("TOOLBOX_SCRIPT=", result.stdout)
        self.assertIn("toolbox_launcher.pyw", result.stdout)

    def test_macos_command_launcher_runs_from_moved_copy(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS launcher smoke test")

        source = self.script_root / "电力工具箱.command"
        self.assertTrue(source.is_file())
        content = source.read_text(encoding="utf-8")
        self.assertIn("TOOLBOX_SMOKE", content)
        self.assertNotIn("C:\\Users\\lllg", content)

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "moved-toolbox.command"
            shutil.copy2(source, copied)
            copied.chmod(0o755)
            result = subprocess.run(
                [str(copied)],
                cwd=directory,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TOOLBOX_SMOKE": "1"},
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("TOOLBOX_SCRIPT=", result.stdout)
        self.assertIn("toolbox_launcher.pyw", result.stdout)

    def test_readme_documents_unified_and_legacy_launchers(self) -> None:
        readme = next(self.script_root.glob("*/README_*.txt"))
        content = readme.read_text(encoding="utf-8")

        self.assertIn("电力工具箱.bat", content)
        self.assertIn("原有", content)
        self.assertIn("保留", content)


if __name__ == "__main__":
    unittest.main()
