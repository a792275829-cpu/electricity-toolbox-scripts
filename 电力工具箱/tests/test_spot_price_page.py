from __future__ import annotations

import tkinter as tk
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from toolbox.runtime import TaskRegistry, ToolPaths
from toolbox.spot_price_page import SpotPriceUpdatePage


class SpotPriceUpdatePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk 不可用：{exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def test_command_contains_dates_workbooks_and_safety_options(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        paths = ToolPaths(workspace)
        page = SpotPriceUpdatePage(
            self.root, paths, TaskRegistry(), today=date(2026, 9, 2)
        )
        command = page.build_update_command(
            date(2026, 8, 30),
            date(2026, 9, 1),
            Path("/tmp/归湖.xlsx"),
            Path("/tmp/鮀莲.xlsx"),
            overwrite=False,
            create_backup=False,
        )
        self.assertEqual(page.start_date.get(), "2026-09-01")
        self.assertIn(str(paths.spot_price_update), command)
        self.assertIn("--skip-existing", command)
        self.assertIn("--no-backup", command)
        self.assertIn("--haifeng-workbook", command)
        self.assertEqual(command[command.index("--stations") + 1 : -2], ["归湖", "鮀莲", "海风"])
        page.destroy()

    def test_update_checks_and_passes_only_selected_workbooks(self) -> None:
        page = SpotPriceUpdatePage(
            self.root, ToolPaths(Path(__file__).resolve().parents[2]), TaskRegistry()
        )
        self.addCleanup(page.destroy)
        with tempfile.TemporaryDirectory() as folder:
            for label, variable in page.station_workbooks.items():
                workbook = Path(folder) / f"{label}.xlsx"
                workbook.touch()
                variable.set(str(workbook))
            for selected in (("海风",), ("归湖", "海风"), ("鮀莲",)):
                with self.subTest(selected=selected):
                    for label, variable in page.selected_stations.items():
                        variable.set(label in selected)
                        if label not in selected:
                            page.station_workbooks[label].set("/not-present/unselected.xlsx")
                        else:
                            page.station_workbooks[label].set(str(Path(folder) / f"{label}.xlsx"))
                    with patch.object(page, "run_process") as run, \
                            patch("toolbox.spot_price_page.messagebox.showerror") as error:
                        page.start_update()
                    error.assert_not_called()
                    command = run.call_args.args[0]
                    self.assertIn("--no-backup", command)
                    self.assertEqual(
                        command[command.index("--stations") + 1 : command.index("--no-backup")],
                        list(selected),
                    )
                    for label, flag in (
                        ("归湖", "--guihu-workbook"), ("鮀莲", "--tuolian-workbook"),
                        ("海风", "--haifeng-workbook"),
                    ):
                        self.assertEqual(flag in command, label in selected)

    def test_no_selection_and_duplicate_selected_files_are_rejected(self) -> None:
        page = SpotPriceUpdatePage(
            self.root, ToolPaths(Path(__file__).resolve().parents[2]), TaskRegistry()
        )
        self.addCleanup(page.destroy)
        for variable in page.selected_stations.values():
            variable.set(False)
        with self.assertRaisesRegex(ValueError, "至少选择一个"):
            page.selected_workbooks()
        with tempfile.TemporaryDirectory() as folder:
            workbook = Path(folder) / "同一个.xlsx"
            workbook.touch()
            for label in ("归湖", "海风"):
                page.selected_stations[label].set(True)
                page.station_workbooks[label].set(str(workbook))
            with self.assertRaisesRegex(ValueError, "同一个工作簿"):
                page.selected_workbooks()


if __name__ == "__main__":
    unittest.main()
