from __future__ import annotations

import tkinter as tk
import unittest
from datetime import date
from pathlib import Path

from toolbox.runtime import TaskRegistry, ToolPaths
from toolbox.transaction_info_page import TransactionInfoUpdatePage


class TransactionInfoUpdatePageTests(unittest.TestCase):
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

    def test_command_contains_workbook_date_and_safety_options(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        paths = ToolPaths(workspace)
        page = TransactionInfoUpdatePage(
            self.root, paths, TaskRegistry(), today=date(2026, 9, 18)
        )
        command = page.build_update_command(
            Path("/tmp/交易系统信息.xlsx"),
            date(2026, 9, 17),
            start_date=date(2026, 9, 11),
            create_backup=False,
        )
        self.assertEqual(page.start_date.get(), "")
        self.assertEqual(page.end_date.get(), "2026-09-18")
        self.assertIn(str(paths.transaction_info_update), command)
        self.assertIn("--start-date", command)
        self.assertIn("--no-backup", command)
        page.destroy()


if __name__ == "__main__":
    unittest.main()
