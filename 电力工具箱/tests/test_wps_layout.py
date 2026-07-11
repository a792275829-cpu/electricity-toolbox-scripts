from __future__ import annotations

import gc
import tkinter as tk
import unittest
from pathlib import Path


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

        frame.destroy()
        root.destroy()
        gc.collect()


if __name__ == "__main__":
    unittest.main()
