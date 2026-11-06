from __future__ import annotations

import importlib.util
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


WORKSPACE = Path(__file__).resolve().parents[2]
MODULE_ROOT = WORKSPACE / "500kV断面分析"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "toolbox_section_analysis_runner",
        MODULE_ROOT / "run_500kv_analysis.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SectionAnalysisModuleTests(unittest.TestCase):
    def test_module_assets_and_catalog_registration_exist(self) -> None:
        from toolbox.catalog import default_catalog
        from toolbox.runtime import ToolPaths

        paths = ToolPaths(WORKSPACE)
        self.assertTrue(paths.section_analysis_runner.is_file())
        self.assertTrue(paths.section_analysis_topology.is_file())
        self.assertTrue((paths.section_analysis_dir / "data" / "500kv_node_positions.json").is_file())
        descriptor = next(item for item in default_catalog() if item.tool_id == "section-analysis")
        self.assertEqual(descriptor.name, "500kV断面分析")
        self.assertEqual(descriptor.page_class, "SectionAnalysisPage")

    def test_runner_uses_source_based_html_names(self) -> None:
        runner = load_runner()
        self.assertEqual(
            runner.result_html_name(Path("日前节点电价查询(2026-07-20).xlsx")),
            "日前节点电价查询(2026-07-20)_500kV分析.html",
        )

    def test_runner_writes_html_directly_to_output_root(self) -> None:
        runner = load_runner()
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "日前节点电价查询.xlsx"
            topology = tmp_path / "topology.md"
            output_root = tmp_path / "downloads"
            source.touch()
            topology.touch()
            expected_html = output_root / "日前节点电价查询_500kV分析.html"
            runner.generate_blockage_map = Mock(
                return_value=SimpleNamespace(
                    html_path=expected_html,
                    kept_rows=118,
                    removed_rows=2724,
                    flagged_edges=10,
                    flagged_intervals=85,
                )
            )

            result = runner.run_many(
                [source],
                output_root,
                topology_path=topology,
            )

            self.assertEqual(result, [expected_html])
            self.assertEqual(
                runner.generate_blockage_map.call_args.kwargs["output_dir"],
                output_root.resolve(),
            )
            self.assertEqual(
                runner.generate_blockage_map.call_args.kwargs["html_name"],
                "日前节点电价查询_500kV分析.html",
            )


class SectionAnalysisPageTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.runtime import TaskRegistry, ToolPaths

        self.paths = ToolPaths(WORKSPACE)
        self.registry = TaskRegistry()

    def tearDown(self) -> None:
        if getattr(self, "root", None) is not None:
            self.root.destroy()

    def test_page_defaults_and_command_match_analysis_contract(self) -> None:
        from toolbox.section_analysis_page import SectionAnalysisPage

        page = SectionAnalysisPage(self.root, self.paths, self.registry)
        command = page.build_command(
            [Path("/tmp/prices.xlsx")],
            Path("/tmp/results"),
            self.paths.section_analysis_topology,
        )

        actions = [widget.cget("text") for widget in page.actions.winfo_children()]
        self.assertIn("拓扑编辑", actions)
        self.assertIn("浏览器编辑", actions)
        self.assertEqual(actions[-2:], ["开始分析", "打开结果目录"])
        self.assertFalse(hasattr(page, "open_latest_button"))
        self.assertFalse(hasattr(page, "threshold"))
        self.assertFalse(hasattr(page, "rule_summary"))
        self.assertEqual(Path(page.output_root.get()), Path.home() / "Downloads")
        self.assertEqual(Path(command[1]), self.paths.section_analysis_runner)
        self.assertIn("--threshold", command)
        self.assertIn("80.0", command)
        self.assertNotIn("--keep-processed", command)
        self.assertNotIn("--no-keep-processed", command)
        self.assertEqual(command[-1], "/tmp/prices.xlsx")

    def test_editor_entry_reuses_open_window_and_applies_json_path(self) -> None:
        from toolbox.section_analysis_page import SectionAnalysisPage
        page = SectionAnalysisPage(self.root, self.paths, self.registry)
        with patch('toolbox.topology_editor.TopologyEditor') as editor_class:
            page.open_topology_editor()
            callback = editor_class.call_args.kwargs['on_apply']
            callback(Path('/tmp/applied.json'))
            self.assertEqual(page.topology_path.get(), '/tmp/applied.json')
            editor_class.return_value.winfo_exists.return_value = True
            page.open_topology_editor()
            editor_class.assert_called_once()
            editor_class.return_value.lift.assert_called_once()

    def test_browser_editor_entry_reuses_service_and_receives_applied_path(self) -> None:
        import queue
        from toolbox.section_analysis_page import SectionAnalysisPage
        page = SectionAnalysisPage(self.root, self.paths, self.registry)
        service = Mock(closed=False, dirty=False, url='http://127.0.0.1:12345/#test', applied=queue.Queue())
        with patch('toolbox_shared.topology_web.BrowserEditor', return_value=service) as factory, \
             patch('toolbox.section_analysis_page.webbrowser.open_new_tab', return_value=True) as browser:
            page.open_browser_editor()
            page.open_browser_editor()
            factory.assert_called_once()
            self.assertEqual(browser.call_count, 2)
            service.applied.put(Path('/tmp/browser-applied.json'))
            page.after_cancel(page._browser_poll_id)
            page._poll_browser_editor()
            self.assertEqual(page.topology_path.get(), '/tmp/browser-applied.json')
        page.destroy()
        service.close.assert_called_once()

    def test_success_opens_all_generated_html_in_default_browser(self) -> None:
        from toolbox.section_analysis_page import SectionAnalysisPage, result_html_name

        page = SectionAnalysisPage(self.root, self.paths, self.registry)
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "结果 目录"
            output.mkdir()
            sources = [Path("日前节点电价.xlsx"), Path("实时节点电价.xlsx")]
            for source in sources:
                (output / result_html_name(source)).write_text("<html></html>")
            page.set_files(sources)
            page.output_root.set(str(output))
            page.run_process = Mock()
            page.start_analysis()
            with patch("toolbox.section_analysis_page.webbrowser.open_new_tab", return_value=True) as browser:
                browser.assert_not_called()
                page.run_process.call_args.kwargs["on_success"](0)
            self.assertEqual(browser.call_args_list, [
                call((output / result_html_name(source)).resolve().as_uri())
                for source in sources
            ])

    def test_success_does_not_open_missing_html(self) -> None:
        from toolbox.section_analysis_page import SectionAnalysisPage

        page = SectionAnalysisPage(self.root, self.paths, self.registry)
        with TemporaryDirectory() as tmp:
            page.set_files([Path("未生成.xlsx")])
            page.output_root.set(tmp)
            page.run_process = Mock()
            page.start_analysis()
            with patch("toolbox.section_analysis_page.webbrowser.open_new_tab") as browser:
                page.run_process.call_args.kwargs["on_success"](0)
                browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
