from __future__ import annotations

import gc
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk


def descendants(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class WpsToolbarLayoutTests(unittest.TestCase):
    def test_write_requires_confirmation_and_preview_does_not(self) -> None:
        from unittest import mock
        from toolbox.runtime import ToolPaths, load_module
        module = load_module("wps_overwrite_layout_test", ToolPaths(Path(__file__).resolve().parents[2]).wps_writer)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.addCleanup(root.destroy)
        root.geometry("520x720")
        config = {"overwrite": False, "configs": [{"name": "样例", "regions": []}]}
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(module, "save_config") as save:
            frame = module.WpsWriterFrame(root)
            self.addCleanup(frame.destroy)
            frame.pack(fill="both", expand=True)
            root.update_idletasks()
            self.assertFalse(any(isinstance(w, ttk.Checkbutton) and "覆盖" in w.cget("text")
                                 for w in descendants(frame)))
            frame.tree.selection_set(frame.tree.get_children()[0])
            with mock.patch.object(module.messagebox, "askyesno", side_effect=[False, True]) as ask, \
                 mock.patch.object(frame, "_start_task") as start:
                frame.preview()
                ask.assert_not_called()
                self.assertEqual(start.call_args.kwargs["name"], "WPS 预览")
                start.reset_mock()

                frame.write()
                start.assert_not_called()
                save.assert_not_called()
                self.assertIn("已有数据和公式", ask.call_args.args[1])
                self.assertEqual(ask.call_args.kwargs["default"], module.messagebox.NO)

                frame.write()
                start.assert_called_once()
                self.assertEqual(start.call_args.kwargs["name"], "WPS 写入")
                self.assertTrue(save.call_args.args[0]["overwrite"])

    def test_toolbar_wraps_when_buttons_need_more_space(self) -> None:
        try:
            root = tk.Tk()
            root.geometry("520x720")
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.runtime import ToolPaths, load_module
        from toolbox.ui.theme import configure_theme

        style = ttk.Style(root)
        configure_theme(style)
        style.configure("TButton", padding=(18, 5))
        module = load_module("wps_large_toolbar_test", ToolPaths(Path(__file__).resolve().parents[2]).wps_writer)
        frame = module.WpsWriterFrame(root)
        frame.pack(fill="both", expand=True)
        root.update_idletasks()
        self.assertGreater(frame.mapping_sort_toolbar.winfo_rooty(), frame.mapping_primary_toolbar.winfo_rooty())
        right = frame.winfo_rootx() + frame.winfo_width()
        for toolbar in (frame.mapping_primary_toolbar, frame.mapping_secondary_toolbar, frame.mapping_sort_toolbar):
            for button in toolbar.winfo_children():
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), right)
        frame.destroy()
        root.destroy()
        gc.collect()

    def test_all_document_config_buttons_fit_at_520_pixels(self) -> None:
        try:
            root = tk.Tk()
            root.geometry("520x720")
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.runtime import ToolPaths, load_module
        from toolbox.ui.theme import configure_theme

        configure_theme(ttk.Style(root))

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_responsive_toolbar_test", paths.wps_writer)
        frame = module.WpsWriterFrame(root)
        frame.pack(fill="both", expand=True)
        root.update_idletasks()

        expected = {
            "添加配置", "编辑", "复制", "删除", "上移", "下移",
            "导入", "导出", "预览", "开始写入",
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
        for text in ("添加配置", "编辑", "复制", "删除", "上移", "下移", "导入", "导出"):
            with self.subTest(toolbar_button=text):
                self.assertLess(buttons[text].winfo_rooty(), tree_top)
        for text in ("预览", "开始写入"):
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
        from toolbox.ui.theme import configure_theme

        configure_theme(ttk.Style(root))

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        page = WpsWriterPage(root, paths, TaskRegistry())
        page.pack(fill="both", expand=True)
        deadline = time.monotonic() + 5
        while not hasattr(page, "writer_frame") and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertTrue(hasattr(page, "writer_frame"))
        root.update_idletasks()

        self.assertEqual(page.footer.winfo_manager(), "")
        self.assertEqual(page.actions.winfo_manager(), "")
        self.assertIsNot(page.log_text, page.writer_frame.log_text)

        from toolbox_shared.log_console import LogConsole

        visible_consoles = [widget for widget in descendants(page)
                            if isinstance(widget, LogConsole) and widget.winfo_ismapped()]
        self.assertEqual(1, len(visible_consoles))
        visible_log_frames = [
            str(widget.cget("text"))
            for widget in descendants(page)
            if isinstance(widget, ttk.LabelFrame) and widget.winfo_ismapped()
        ]
        self.assertIs(visible_consoles[0], page.writer_frame.log_console)
        self.assertNotIn("操作日志", visible_log_frames)

        page.destroy()
        root.destroy()
        gc.collect()


if __name__ == "__main__":
    unittest.main()
