"""Compact file picker shared by toolbox pages and standalone dialogs."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


class FileSelectButton(ttk.Button):
    def __init__(self, parent, *, text="选择文件", command=None):
        # Retain the descriptive text for keyboard/accessibility clients and
        # the tooltip; only the folder-plus icon is drawn on the button.
        style = ttk.Style(parent)
        style.configure("FileSelect.TButton", padding=5, width=0, anchor="center")
        if style.theme_use() == "aqua":
            # Aqua's native push-button bezel enforces a 70 px minimum even
            # for an image-only button. Use the same compact icon in dialogs.
            style.layout("FileSelect.TButton", [("Button.padding", {
                "sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})],
            })])
        self._icons = []
        for ink in ("#777772", "#b5b5b0"):
            scale = 3
            bitmap = Image.new("RGBA", (20 * scale, 20 * scale))
            draw = ImageDraw.Draw(bitmap)
            draw.line([(2 * scale, 7 * scale), (2 * scale, 5 * scale),
                       (4 * scale, 3 * scale), (8 * scale, 3 * scale),
                       (11 * scale, 6 * scale)], fill=ink, width=scale * 2,
                      joint="curve")
            draw.rounded_rectangle((2 * scale, 7 * scale, 18 * scale, 18 * scale),
                                   radius=2 * scale, outline=ink, width=scale * 2)
            draw.line((7 * scale, 12 * scale, 13 * scale, 12 * scale), fill=ink, width=scale)
            draw.line((10 * scale, 9 * scale, 10 * scale, 15 * scale), fill=ink, width=scale)
            self._icons.append(ImageTk.PhotoImage(bitmap.resize((20, 20), Image.Resampling.LANCZOS), master=parent))
        super().__init__(parent, text=text, command=command, style="FileSelect.TButton",
                         image=(self._icons[0], "disabled", self._icons[1]), compound="image")
        self._tip = None
        self._tip_timer = None
        self.bind("<Enter>", self._schedule_tip)
        self.bind("<FocusIn>", self._schedule_tip)
        for event in ("<Leave>", "<FocusOut>", "<ButtonPress>", "<Destroy>"):
            self.bind(event, self._hide_tip)

    def _schedule_tip(self, _event=None):
        self._hide_tip()
        self._tip_timer = self.after(450, self._show_tip)

    def _show_tip(self):
        self._tip_timer = None
        if self.instate(("disabled",)) or not self.winfo_ismapped():
            return
        self._tip = tk.Toplevel(self)
        self._tip.overrideredirect(True)
        self._tip.geometry(f"+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height() + 4}")
        ttk.Label(self._tip, text=self.cget("text"), padding=(7, 4)).pack()

    def _hide_tip(self, _event=None):
        if self._tip_timer is not None:
            self.after_cancel(self._tip_timer)
            self._tip_timer = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
