"""Portable Tk date entry with a calendar; no third-party GUI dependency."""

from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date
from tkinter import font, ttk

from toolbox_dates import normalize_date_input, parse_date_input


class DateInput(ttk.Frame):
    def __init__(self, parent, *, textvariable: tk.StringVar, width: int = 16):
        super().__init__(parent)
        self.variable = textvariable
        self.popup: tk.Toplevel | None = None
        self._dismiss_binding: str | None = None
        self.displayed_month = date.today().replace(day=1)
        self.entry = ttk.Entry(self, textvariable=textvariable, width=width)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<FocusOut>", self._normalize)
        self.entry.bind("<ButtonRelease-1>", self._open_from_click)
        self.entry.bind("<Down>", self._open_from_key)
        self.entry.bind("<Escape>", lambda _event: self.close_calendar())
        self.columnconfigure(0, weight=1)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _normalize(self, _event=None) -> None:
        if self.variable.get().strip():
            try:
                self.variable.set(normalize_date_input(self.variable.get()))
            except ValueError:
                pass  # Preserve invalid or incomplete input for the page's validation.

    def _open_from_key(self, _event=None) -> str:
        self.open_calendar()
        self.popup.focus_set()
        return "break"

    def _open_from_click(self, _event=None) -> None:
        self.open_calendar()

    def _configure_calendar_styles(self) -> None:
        style = ttk.Style(self)
        family = font.nametofont("TkDefaultFont", root=self).actual("family")
        style.configure("CalendarPanel.TFrame", background="#ffffff", bordercolor="#e8e8e5",
                        lightcolor="#e8e8e5", darkcolor="#e8e8e5")
        style.configure("CalendarDay.TButton", font=(family, 14), padding=(4, 7), anchor="center")
        style.configure("CalendarDaySelected.Primary.TButton", font=(family, 14, "bold"),
                        padding=(4, 7), anchor="center")
        style.configure("CalendarToday.TButton", font=(family, 14, "bold"),
                        padding=(4, 7), anchor="center")
        style.configure("CalendarNav.TButton", font=(family, 13), padding=(4, 5), anchor="center")
        style.configure("CalendarMonth.TLabel", font=(family, 15, "bold"), anchor="center")
        style.configure("CalendarWeekday.TLabel", font=(family, 11), foreground="#777772", anchor="center")
        style.configure("CalendarAdjacent.TLabel", font=(family, 14), foreground="#b0b0aa")
        style.configure("Calendar.TSeparator", background="#e8e8e5",
                        lightcolor="#e8e8e5", darkcolor="#e8e8e5")
        style.configure("CalendarTodayAction.TButton", font=(family, 12), padding=(6, 7), anchor="center")

    def open_calendar(self) -> None:
        if self.popup is not None and self.popup.winfo_exists():
            return
        try:
            selected = parse_date_input(self.variable.get())
        except ValueError:
            selected = date.today()
        self.displayed_month = selected.replace(day=1)
        self._configure_calendar_styles()
        popup = self.popup = tk.Toplevel(self)
        popup.withdraw()
        popup.resizable(False, False)
        popup.transient(self.winfo_toplevel())
        popup.overrideredirect(True)
        popup.bind("<Escape>", lambda _event: self.close_calendar())
        popup.protocol("WM_DELETE_WINDOW", self.close_calendar)
        outer = ttk.Frame(popup, style="CalendarPanel.TFrame", padding=(12, 12, 12, 10),
                          borderwidth=1, relief="solid")
        outer.pack(fill="both", expand=True)
        navigation = ttk.Frame(outer)
        navigation.pack(fill="x")
        for column, (label, offset) in enumerate((("«", -12), ("‹", -1))):
            ttk.Button(navigation, text=label, width=2, style="CalendarNav.TButton",
                       command=lambda delta=offset: self.change_month(delta)).grid(row=0, column=column)
        self.month_label = ttk.Label(navigation, style="CalendarMonth.TLabel", width=14, anchor="center")
        self.month_label.grid(row=0, column=2, sticky="ew", padx=8)
        navigation.columnconfigure(2, weight=1)
        for column, (label, offset) in enumerate((("›", 1), ("»", 12)), start=3):
            ttk.Button(navigation, text=label, width=2, style="CalendarNav.TButton",
                       command=lambda delta=offset: self.change_month(delta)).grid(row=0, column=column)
        self.days_frame = ttk.Frame(outer)
        self.days_frame.pack(fill="both", expand=True, pady=(10, 8))
        ttk.Separator(outer, style="Calendar.TSeparator").pack(fill="x", pady=(0, 8))
        ttk.Button(outer, text="今天", style="CalendarTodayAction.TButton",
                   command=lambda: self.select_date(date.today())).pack(fill="x")
        self._render_month()
        popup.update_idletasks()
        x = min(self.winfo_rootx(), max(0, popup.winfo_screenwidth() - popup.winfo_reqwidth()))
        y = self.winfo_rooty() + self.winfo_height() + 3
        if y + popup.winfo_reqheight() > popup.winfo_screenheight():
            y = max(0, self.winfo_rooty() - popup.winfo_reqheight())
        popup.geometry(f"+{max(0, x)}+{y}")
        popup.deiconify()
        # Keep the entry editable while the calendar is visible.
        self._dismiss_binding = self.winfo_toplevel().bind(
            "<ButtonPress-1>", self._outside_click, add="+"
        )

    def _render_month(self) -> None:
        month = self.displayed_month
        self.month_label.configure(text=f"{month.year}年{month.month}月")
        for child in self.days_frame.winfo_children():
            child.destroy()
        try:
            selected = parse_date_input(self.variable.get())
        except ValueError:
            selected = None
        for column, label in enumerate(("日", "一", "二", "三", "四", "五", "六")):
            self.days_frame.columnconfigure(column, weight=1, uniform="days", minsize=44)
            ttk.Label(self.days_frame, text=label, style="CalendarWeekday.TLabel", anchor="center").grid(
                row=0, column=column, sticky="ew", pady=(0, 6)
            )
        weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(month.year, month.month)
        weeks.extend([[0] * 7 for _ in range(6 - len(weeks))])
        first_ordinal = month.toordinal() - (month.weekday() + 1) % 7
        for row, week in enumerate(weeks, start=1):
            self.days_frame.rowconfigure(row, minsize=42, uniform="weeks")
            for column, number in enumerate(week):
                if number == 0:
                    ordinal = first_ordinal + (row - 1) * 7 + column
                    label = str(date.fromordinal(ordinal).day) if 1 <= ordinal <= date.max.toordinal() else ""
                    ttk.Label(self.days_frame, text=label, style="CalendarAdjacent.TLabel",
                              width=3, anchor="center").grid(
                        row=row, column=column, sticky="nsew", padx=2, pady=3
                    )
                    continue
                day = date(month.year, month.month, number)
                day_style = (
                    "CalendarDaySelected.Primary.TButton" if day == selected else
                    "CalendarToday.TButton" if day == date.today() else "CalendarDay.TButton"
                )
                button = ttk.Button(self.days_frame, text=str(day.day), width=3, style=day_style,
                                    command=lambda value=day: self.select_date(value))
                button.grid(row=row, column=column, sticky="nsew", padx=2, pady=3)

    def change_month(self, delta: int) -> None:
        index = (self.displayed_month.year - 1) * 12 + self.displayed_month.month - 1 + delta
        if 0 <= index < 9999 * 12:
            self.displayed_month = date(index // 12 + 1, index % 12 + 1, 1)
            self._render_month()

    def select_date(self, selected: date) -> None:
        self.variable.set(selected.isoformat())
        self.close_calendar()
        self.entry.focus_set()

    def _outside_click(self, event) -> None:
        popup = self.popup
        def contains(widget) -> bool:
            return (
                widget.winfo_rootx() <= event.x_root < widget.winfo_rootx() + widget.winfo_width()
                and widget.winfo_rooty() <= event.y_root < widget.winfo_rooty() + widget.winfo_height()
            )
        if popup is not None and not contains(popup) and not contains(self.entry):
            self.close_calendar()

    def close_calendar(self) -> None:
        if self._dismiss_binding is not None:
            self.winfo_toplevel().unbind("<ButtonPress-1>", self._dismiss_binding)
            self._dismiss_binding = None
        popup, self.popup = self.popup, None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self.close_calendar()
