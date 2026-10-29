from __future__ import annotations

import gc
import sys
import time
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from toolbox.app import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH, PAGE_NAMES, ToolboxApp


class MinimumWindowLayoutTests(unittest.TestCase):
    def setUp(self):
        self.credentials = patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={})
        self.credentials.start()
        self.addCleanup(self.credentials.stop)
        self.app = ToolboxApp(workspace=Path(__file__).resolve().parents[2])
        self.addCleanup(self.close_app)
        self.app.geometry(f"{MIN_WINDOW_WIDTH}x{MIN_WINDOW_HEIGHT}")
        self.app.update()

    def close_app(self):
        self.app.destroy()
        gc.collect()

    def test_all_real_pages_keep_their_full_form_size_at_minimum_window(self):
        app = self.app
        self.assertEqual(app.minsize(), (MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT))
        for name in PAGE_NAMES:
            with self.subTest(page=name):
                app.show_page(name)
                page = app.pages[name]
                if name == "WPS写入工具":
                    deadline = time.monotonic() + 5
                    while not hasattr(page, "writer_frame") and time.monotonic() < deadline:
                        app.update()
                        time.sleep(.01)
                    self.assertTrue(hasattr(page, "writer_frame"))
                app.update()
                self.assertGreaterEqual(page.winfo_width(), page.winfo_reqwidth())
                self.assertGreaterEqual(page.winfo_height(), page.winfo_reqheight())
                viewport = app.page_viewport
                if page.winfo_reqheight() > viewport.canvas.winfo_height():
                    self.assertEqual(viewport.vertical.winfo_manager(), "grid")
                if page.winfo_reqwidth() > viewport.canvas.winfo_width():
                    self.assertEqual(viewport.horizontal.winfo_manager(), "grid")
        # Credentials and diagnostics remain reachable beside the tool form.
        rail = app.right_rail
        self.assertGreaterEqual(rail.content.winfo_height(), rail.content.winfo_reqheight())
        self.assertGreaterEqual(app.page_viewport.winfo_width(), app.winfo_width() * .5)

    def test_tab_focus_reveals_bottom_controls_and_navigation_resets_scroll(self):
        app = self.app
        page = app.pages[app.current_page]
        form = ttk.Frame(page, width=1050, height=1100)
        form.grid(row=10, column=0)
        form.grid_propagate(False)
        button = ttk.Button(form, text="底部操作")
        button.place(x=900, y=1030)
        app.update()
        view = app.page_viewport
        view._reveal_focus(type("Event", (), {"widget": button})())
        app.update_idletasks()
        self.assertGreater(view.canvas.yview()[0], 0)
        self.assertGreater(view.canvas.xview()[0], 0)
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                             view.canvas.winfo_rooty() + view.canvas.winfo_height())
        app.show_page("风力预测截图")
        app.update()
        self.assertEqual(view.canvas.yview()[0], 0)
        self.assertEqual(view.canvas.xview()[0], 0)
        self.assertEqual(view.horizontal.winfo_manager(), "")
        self.assertEqual(view.vertical.winfo_manager(), "")

    def test_small_display_caps_window_and_keeps_overflow_scrollable(self):
        app = self.app
        with patch.object(app, "winfo_screenwidth", return_value=1200), \
             patch.object(app, "winfo_screenheight", return_value=800):
            app._fit_initial_window()
        app.update()
        self.assertEqual(app.minsize(), (1120, 720))
        self.assertLessEqual(app.winfo_width(), 1120)
        app.show_page("现货价格分析表更新")
        app.update()
        page = app.pages[app.current_page]
        self.assertGreaterEqual(page.winfo_height(), page.winfo_reqheight())
        self.assertEqual(app.page_viewport.vertical.winfo_manager(), "grid")


if __name__ == "__main__":
    unittest.main()
