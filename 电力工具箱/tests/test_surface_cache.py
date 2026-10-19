from __future__ import annotations

import gc
from unittest.mock import patch
import tkinter as tk
import unittest
from tkinter import ttk

from PIL import Image

from toolbox.ui.surface_cache import SurfaceCache, compose_surface
from toolbox.ui.theme import configure_theme


class SurfaceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("500x320")
        self.root._toolbox_collect_surface_specs = True
        configure_theme(ttk.Style(self.root))
        self.cache = SurfaceCache(self.root, max_entries=4, max_pixels=500_000)
        self.root.update()

    def tearDown(self) -> None:
        self.cache.close()
        self.root.destroy()
        gc.collect()

    def test_compose_keeps_corners_and_tiles_center_without_resampling(self) -> None:
        source = Image.new("RGB", (6, 6), "white")
        source.putpixel((0, 0), (1, 2, 3))
        source.putpixel((5, 5), (4, 5, 6))
        source.putpixel((2, 2), (7, 8, 9))
        source.putpixel((3, 2), (10, 11, 12))
        result = compose_surface(source, 13, 11, 2)
        self.assertEqual((1, 2, 3), result.getpixel((0, 0)))
        self.assertEqual((4, 5, 6), result.getpixel((12, 10)))
        self.assertEqual((7, 8, 9), result.getpixel((10, 8)))
        self.assertEqual((10, 11, 12), result.getpixel((9, 8)))

    def test_hover_and_click_keep_native_behavior_without_rebuilding_images(self) -> None:
        clicks = []
        button = ttk.Button(self.root, text="电量汇总", command=lambda: clicks.append(True), style="Nav.TButton")
        button.pack()
        self.cache.track(button)
        self.root.update()
        self.cache.flush()
        builds = self.cache.build_count
        for _ in range(10):
            button.event_generate("<Enter>")
            self.assertTrue(button.instate(("active",)))
            button.event_generate("<Leave>")
        button.invoke()
        self.assertEqual([True], clicks)
        button.state(("disabled",))
        button.invoke()
        self.assertEqual([True], clicks)
        self.assertEqual(builds, self.cache.build_count)

    def test_entry_preserves_text_cursor_selection_and_disabled_state(self) -> None:
        variable = tk.StringVar(self.root, "原值")
        entry = ttk.Entry(self.root, textvariable=variable)
        entry.pack()
        self.cache.track(entry)
        self.root.update()
        self.cache.flush()
        entry.insert("end", "追加")
        entry.icursor(2)
        entry.selection_range(0, 2)
        self.assertEqual("原值追加", variable.get())
        self.assertEqual(2, entry.index("insert"))
        self.assertTrue(entry.selection_present())
        entry.state(("disabled",))
        entry.insert("end", "禁止")
        self.assertEqual("原值追加", variable.get())

    def test_resize_cache_is_bounded_and_never_reuses_a_live_background(self) -> None:
        self.cache.prepare_styles({"TButton": 2})
        first = ttk.Button(self.root, text="固定")
        second = ttk.Button(self.root, text="缩放")
        first.place(x=0, y=0, width=100, height=32)
        second.place(x=0, y=40, width=110, height=32)
        self.cache.track(first)
        self.cache.track(second)
        self.root.update()
        self.cache.flush()
        fixed_key = self.cache.widgets[first].assigned
        fixed_photos = tuple(str(photo) for photo in self.cache.entries[fixed_key].photos)
        for width in range(120, 400, 10):
            second.place_configure(width=width)
            self.root.update()
            self.cache.flush()
            self.assertEqual(("TButton", width, 32), self.cache.widgets[second].assigned)
            self.assertLessEqual(len(self.cache.entries), 4)
            self.assertEqual(2, len(self.cache._slots))
            self.assertLessEqual(self.cache.pixel_count, 500_000)
        self.assertEqual(fixed_photos, tuple(str(photo) for photo in self.cache.entries[fixed_key].photos))
        photo = str(self.cache.entries[fixed_key].photos[0])
        self.assertEqual((101, 33), (self.root.tk.call("image", "width", photo),
                                    self.root.tk.call("image", "height", photo)))

    def test_style_switch_keeps_geometry_and_reuses_selected_background(self) -> None:
        button = ttk.Button(self.root, text="模块", style="Nav.TButton")
        button.pack()
        self.cache.track(button)
        self.root.update()
        self.cache.flush()
        geometry = (button.winfo_reqwidth(), button.winfo_reqheight())
        self.cache.set_style(button, "NavSelected.TButton")
        selected = button.cget("style")
        self.cache.set_style(button, "Nav.TButton")
        builds = self.cache.build_count
        self.cache.set_style(button, "NavSelected.TButton")
        self.root.update()
        self.assertEqual(selected, button.cget("style"))
        self.assertEqual(builds, self.cache.build_count)
        self.assertEqual(geometry, (button.winfo_reqwidth(), button.winfo_reqheight()))

    def test_labelframe_uses_border_parcel_and_destroy_cancels_resize(self) -> None:
        frame = ttk.LabelFrame(self.root, text="运行日志")
        frame.place(x=0, y=0, width=300, height=160)
        self.cache.track(frame)
        self.root.update()
        self.cache.flush()
        key = self.cache.widgets[frame].assigned
        self.assertLess(key[2], frame.winfo_height())
        self.assertTrue(frame.identify(150, 160 - key[2]))
        frame.place_configure(width=320)
        self.root.update()
        callback = self.cache.widgets[frame].after_id
        frame.destroy()
        self.assertNotIn(frame, self.cache.widgets)
        self.assertNotIn(callback, self.root.tk.call("after", "info"))

    def test_prototype_only_caches_navigation_and_summary_and_keeps_mode_inputs(self) -> None:
        from toolbox.app import ToolboxApp
        from toolbox.catalog import catalog_factories

        with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}):
            app = ToolboxApp(page_factories=catalog_factories(), lazy_pages=True, render_prototype=True)
        try:
            app.show_page("出清结果汇总上传")
            app.update()
            summary = app.pages["出清结果汇总上传"]
            summary.input_dir.set("/示例/复盘")
            summary.mode.set("upload")
            app.update()
            app.surface_cache.flush()
            app.show_page("风力预测截图")
            app.update()
            for widget in app.surface_cache.widgets:
                if widget not in app.nav_buttons.values():
                    self.assertTrue(str(widget).startswith(str(summary) + "."))
            app.show_page("出清结果汇总上传")
            self.assertEqual("/示例/复盘", summary.input_dir.get())
            self.assertEqual("upload", summary.mode.get())
            self.assertEqual("开始上传", summary.run_button.cget("text"))
            self.assertEqual("", summary.summary_form.winfo_manager())
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
