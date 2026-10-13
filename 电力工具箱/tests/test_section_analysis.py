from __future__ import annotations

import importlib.util
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock


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
            80.0,
        )

        self.assertEqual(page.threshold.get(), "80")
        self.assertIn("全天累计至少4个时点", page.rule_summary.get())
        self.assertEqual(Path(page.output_root.get()), Path.home() / "Downloads")
        self.assertEqual(Path(command[1]), self.paths.section_analysis_runner)
        self.assertIn("--threshold", command)
        self.assertIn("80.0", command)
        self.assertNotIn("--keep-processed", command)
        self.assertNotIn("--no-keep-processed", command)
        self.assertEqual(command[-1], "/tmp/prices.xlsx")


if __name__ == "__main__":
    unittest.main()
