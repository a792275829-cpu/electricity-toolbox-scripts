from __future__ import annotations

import gc
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk


def descendants(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class WpsToolbarLayoutTests(unittest.TestCase):
    def test_all_document_config_buttons_fit_at_520_pixels(self) -> None:
        try:
            root = tk.Tk()
            root.geometry("520x720")
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.runtime import ToolPaths, load_module

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_responsive_toolbar_test", paths.wps_writer)
        frame = module.WpsWriterFrame(root)
        frame.pack(fill="both", expand=True)
        root.update_idletasks()

        expected = {
            "Add config", "Edit", "Copy", "Remove", "Move up", "Move down",
            "Import", "Export", "Preview", "Write to WPS",
        }
        buttons = {
            str(widget.cget("text")): widget
            for widget in descendants(frame)
            if isinstance(widget, module.ttk.Button)
            and str(widget.cget("text")) in expected
        }
        self.assertEqual(expected, set(buttons))
        left = frame.winfo_rootx()
        right = left + frame.winfo_width()
        for text, button in buttons.items():
            with self.subTest(button=text):
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_rootx(), left)
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), right)

        tree_top = frame.tree.winfo_rooty()
        tree_bottom = tree_top + frame.tree.winfo_height()
        for text in ("Add config", "Edit", "Copy", "Remove", "Move up", "Move down", "Import", "Export"):
            with self.subTest(toolbar_button=text):
                self.assertLess(buttons[text].winfo_rooty(), tree_top)
        for text in ("Preview", "Write to WPS"):
            with self.subTest(execution_button=text):
                self.assertGreaterEqual(buttons[text].winfo_rooty(), tree_bottom)

        frame.destroy()
        root.destroy()
        gc.collect()

    def test_wps_page_shows_only_the_module_specific_log(self) -> None:
        try:
            root = tk.Tk()
            root.geometry("1000x760")
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.pages import WpsWriterPage
        from toolbox.runtime import TaskRegistry, ToolPaths

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        page = WpsWriterPage(root, paths, TaskRegistry())
        page.pack(fill="both", expand=True)
        root.update_idletasks()

        self.assertEqual(page.footer.winfo_manager(), "")
        self.assertEqual(page.actions.winfo_manager(), "")
        self.assertIsNot(page.log_text, page.writer_frame.log_text)

        visible_log_frames = [
            str(widget.cget("text"))
            for widget in descendants(page)
            if isinstance(widget, ttk.LabelFrame) and widget.winfo_ismapped()
        ]
        self.assertIn("WPS operation log", visible_log_frames)
        self.assertNotIn("运行日志", visible_log_frames)

        page.destroy()
        root.destroy()
        gc.collect()


if __name__ == "__main__":
    unittest.main()
