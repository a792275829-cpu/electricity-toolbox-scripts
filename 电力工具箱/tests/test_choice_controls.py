from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import unittest

from toolbox_shared.choices import OptionCheckbutton, SettingCheckbutton, SegmentedGroup
from toolbox.ui.theme import configure_theme


class ChoiceControlTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("600x240")
        self.style = ttk.Style(self.root)
        configure_theme(self.style)

    def tearDown(self):
        self.root.destroy()

    def test_multiple_choices_toggle_independently_and_respect_disabled(self):
        values = [tk.BooleanVar(self.root, True), tk.BooleanVar(self.root, False)]
        buttons = [OptionCheckbutton(self.root, text=name, variable=value)
                   for name, value in zip(("归湖", "海风"), values)]
        for button in buttons:
            button.pack()
        self.root.update()
        self.assertEqual([True, False], [v.get() for v in values])
        buttons[1].invoke()
        self.assertEqual([True, True], [v.get() for v in values])
        buttons[0].state(["disabled"])
        buttons[0].invoke()
        self.assertTrue(values[0].get())
        buttons[0].state(["!disabled"])
        buttons[0].invoke()
        self.assertEqual([False, True], [v.get() for v in values])

    def test_segment_group_preserves_default_callback_and_exclusive_selection(self):
        value = tk.StringVar(self.root, "upload")
        calls = []
        group = SegmentedGroup(self.root, variable=value,
                               choices=(("summary", "仅汇总"), ("upload", "仅上传"), ("both", "汇总并上传")),
                               command=lambda: calls.append(value.get()))
        group.pack()
        self.root.update()
        self.assertEqual("upload", value.get())
        self.assertEqual([False, True, False], [b.instate(["selected"]) for b in group.buttons])
        group.buttons[2].invoke()
        self.assertEqual("both", value.get())
        self.assertEqual([False, False, True], [b.instate(["selected"]) for b in group.buttons])
        self.assertEqual(["both"], calls)
        group.buttons[0].state(["disabled"])
        group.buttons[0].invoke()
        self.assertEqual("both", value.get())
        self.assertEqual(["both"], calls)
        group.buttons[1].focus_force()
        self.root.update()
        group.buttons[1].event_generate("<KeyPress-space>")
        group.buttons[1].event_generate("<KeyRelease-space>")
        self.root.update()
        self.assertEqual("upload", value.get())
        widths = [button.winfo_width() for button in group.buttons]
        self.assertLessEqual(max(widths) - min(widths), 1)
        self.assertLessEqual(group.winfo_width(), 520)

    def test_setting_check_uses_checkmark_indicator_and_keeps_boolean_command(self):
        value = tk.BooleanVar(self.root, False)
        calls = []
        check = SettingCheckbutton(self.root, text="写入前生成时间戳备份", variable=value,
                                  command=lambda: calls.append(value.get()))
        check.pack()
        self.root.update()
        check.invoke()
        self.assertTrue(value.get())
        self.assertEqual([True], calls)
        self.assertIn("Toolbox", str(self.style.layout("TCheckbutton")))
        self.assertNotIn("Radiobutton.indicator", str(self.style.layout("Segment.TRadiobutton")))


if __name__ == "__main__":
    unittest.main()
