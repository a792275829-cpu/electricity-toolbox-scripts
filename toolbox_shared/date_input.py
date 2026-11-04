"""Portable Tk date entry with a calendar; no third-party GUI dependency."""

from __future__ import annotations

import calendar
import tkinter as tk
from dataclasses import dataclass
from datetime import date
from tkinter import font, ttk

from .dates import normalize_date_input, parse_date_input

WEEKDAY_LABELS = ("日", "一", "二", "三", "四", "五", "六")


def shift_month(month: date, delta: int) -> date:
    """Move to another month without ever leaving the supported date range."""
    index = (month.year - 1) * 12 + month.month - 1 + delta
    index = max(0, min(index, 9999 * 12 - 1))
    return date(index // 12 + 1, index % 12 + 1, 1)


def configure_calendar_styles(widget) -> None:
    style = ttk.Style(widget)
    family = font.nametofont("TkDefaultFont", root=widget).actual("family")
    style.configure("CalendarPanel.TFrame", background="#ffffff", bordercolor="#e8e8e5",
                    lightcolor="#e8e8e5", darkcolor="#e8e8e5")
    style.configure("CalendarDay.TButton", font=(family, 14), padding=(4, 7), anchor="center")
    style.configure("CalendarDaySelected.Primary.TButton", font=(family, 14, "bold"),
                    padding=(4, 7), anchor="center")
    style.configure("CalendarToday.TButton", font=(family, 14, "bold"),
                    padding=(4, 7), anchor="center")
    style.configure("CalendarNav.TButton", font=(family, 13), padding=(4, 5), anchor="center")
    style.configure("CalendarMonth.TLabel", font=(family, 15, "bold"), anchor="center")
    style.configure("CalendarPanelTitle.TLabel", font=(family, 12), foreground="#777772", anchor="center")
    style.configure("CalendarHint.TLabel", font=(family, 11), foreground="#777772", anchor="w")
    style.configure("CalendarWeekday.TLabel", font=(family, 11), foreground="#777772", anchor="center")
    style.configure("CalendarAdjacent.TLabel", font=(family, 14), foreground="#b0b0aa")
    style.configure("Calendar.TSeparator", background="#e8e8e5",
                    lightcolor="#e8e8e5", darkcolor="#e8e8e5")
    style.configure("CalendarTodayAction.TButton", font=(family, 12), padding=(6, 7), anchor="center")
    # 双月历并排时用同款但更紧凑的字号与内边距，避免弹层比工具箱窗口还宽。
    style.configure("CalendarRangeDay.TButton", font=(family, 12), padding=(2, 5), anchor="center")
    style.configure("CalendarRangeDaySelected.Primary.TButton", font=(family, 12, "bold"),
                    padding=(2, 5), anchor="center")
    style.configure("CalendarRangeToday.TButton", font=(family, 12, "bold"),
                    padding=(2, 5), anchor="center")
    style.configure("CalendarRangeNav.TButton", font=(family, 12), padding=(3, 4), anchor="center")
    style.configure("CalendarRangeMonth.TLabel", font=(family, 13, "bold"), anchor="center")
    style.configure("CalendarRangeWeekday.TLabel", font=(family, 10), foreground="#777772",
                    anchor="center")
    style.configure("CalendarRangeAdjacent.TLabel", font=(family, 12), foreground="#b0b0aa")


@dataclass(frozen=True)
class MonthPalette:
    """Cell metrics and label styles for one month grid."""
    weekday: str = "CalendarWeekday.TLabel"
    adjacent: str = "CalendarAdjacent.TLabel"
    cell_min: int = 44
    row_min: int = 42
    day_width: int = 3


SINGLE_PALETTE = MonthPalette()
RANGE_PALETTE = MonthPalette(weekday="CalendarRangeWeekday.TLabel",
                             adjacent="CalendarRangeAdjacent.TLabel",
                             cell_min=34, row_min=34, day_width=2)


def draw_month(frame, month: date, *, day_style, on_pick,
               palette: MonthPalette = SINGLE_PALETTE) -> None:
    """Draw a fixed six-week grid so the popup never changes height while paging."""
    for child in frame.winfo_children():
        child.destroy()
    for column, label in enumerate(WEEKDAY_LABELS):
        frame.columnconfigure(column, weight=1, uniform="days", minsize=palette.cell_min)
        ttk.Label(frame, text=label, style=palette.weekday, anchor="center").grid(
            row=0, column=column, sticky="ew", pady=(0, 6)
        )
    weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(month.year, month.month)
    weeks.extend([[0] * 7 for _ in range(6 - len(weeks))])
    first_ordinal = month.toordinal() - (month.weekday() + 1) % 7
    for row, week in enumerate(weeks, start=1):
        frame.rowconfigure(row, minsize=palette.row_min, uniform="weeks")
        for column, number in enumerate(week):
            if number == 0:
                ordinal = first_ordinal + (row - 1) * 7 + column
                label = str(date.fromordinal(ordinal).day) if 1 <= ordinal <= date.max.toordinal() else ""
                ttk.Label(frame, text=label, style=palette.adjacent,
                          width=palette.day_width, anchor="center").grid(
                    row=row, column=column, sticky="nsew", padx=2, pady=3
                )
                continue
            day = date(month.year, month.month, number)
            ttk.Button(frame, text=str(day.day), width=palette.day_width, style=day_style(day),
                       command=lambda value=day: on_pick(value)).grid(
                row=row, column=column, sticky="nsew", padx=2, pady=3
            )


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
        configure_calendar_styles(self)

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
        try:
            selected = parse_date_input(self.variable.get())
        except ValueError:
            selected = None
        draw_month(self.days_frame, month,
                   day_style=lambda day: self._day_style(day, selected),
                   on_pick=self.select_date)

    @staticmethod
    def _day_style(day: date, selected: date | None) -> str:
        return (
            "CalendarDaySelected.Primary.TButton" if day == selected else
            "CalendarToday.TButton" if day == date.today() else "CalendarDay.TButton"
        )

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


class DateRangeInput(ttk.Frame):
    """开始日期／结束日期输入，弹层是左右两张月历。

    左侧月历选择开始日期，右侧月历选择结束日期；默认翻月联动，翻任意一张月历时
    两张月历一起推进、始终相邻。取消联动后两张月历各自独立翻页，适合跨较远月份
    反复核对起止日期。
    """

    def __init__(
        self,
        parent,
        *,
        start_variable: tk.StringVar,
        end_variable: tk.StringVar,
        width: int = 12,
        link_months: bool = True,
        separator: str = "至",
    ):
        super().__init__(parent)
        self.start_variable = start_variable
        self.end_variable = end_variable
        self.popup: tk.Toplevel | None = None
        self._dismiss_binding: str | None = None
        self.link_months = bool(link_months)
        self.link_var = tk.BooleanVar(master=self, value=self.link_months)
        self.left_month = date.today().replace(day=1)
        self.right_month = shift_month(self.left_month, 1)
        self.start_entry = ttk.Entry(self, textvariable=start_variable, width=width)
        self.start_entry.grid(row=0, column=0, sticky="ew")
        ttk.Label(self, text=separator).grid(row=0, column=1, padx=6)
        self.end_entry = ttk.Entry(self, textvariable=end_variable, width=width)
        self.end_entry.grid(row=0, column=2, sticky="ew")
        self.left_month_label: ttk.Label | None = None
        self.right_month_label: ttk.Label | None = None
        self.left_days_frame: ttk.Frame | None = None
        self.right_days_frame: ttk.Frame | None = None
        self.hint_label: ttk.Label | None = None
        for entry in (self.start_entry, self.end_entry):
            entry.bind("<FocusOut>", lambda _event, target=entry: self._normalize(target))
            entry.bind("<ButtonRelease-1>", self._open_from_click)
            entry.bind("<Down>", self._open_from_key)
            entry.bind("<Escape>", lambda _event: self.close_calendar())
        self.bind("<Destroy>", self._on_destroy, add="+")

    def selected_dates(self) -> tuple[date | None, date | None]:
        def parse(variable: tk.StringVar) -> date | None:
            text = variable.get().strip()
            if not text:
                return None
            try:
                return parse_date_input(text)
            except ValueError:
                return None
        return parse(self.start_variable), parse(self.end_variable)

    def _normalize(self, entry: ttk.Entry) -> None:
        variable = self.start_variable if entry is self.start_entry else self.end_variable
        if variable.get().strip():
            try:
                variable.set(normalize_date_input(variable.get()))
            except ValueError:
                pass  # Preserve invalid input for the page's own validation message.

    def _open_from_click(self, _event=None) -> None:
        self.open_calendar()

    def _open_from_key(self, _event=None) -> str:
        self.open_calendar()
        self.popup.focus_set()
        return "break"

    def open_calendar(self) -> None:
        if self.popup is not None and self.popup.winfo_exists():
            return
        start, end = self.selected_dates()
        left = (start or end or date.today()).replace(day=1)
        right = shift_month(left, 1)
        if not self.link_months and end is not None:
            candidate = end.replace(day=1)
            if candidate > left:
                right = candidate
        self.left_month, self.right_month = left, right
        configure_calendar_styles(self)
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
        panels = ttk.Frame(outer)
        panels.pack(fill="both", expand=True)
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(2, weight=1)
        self.left_month_label, self.left_days_frame = self._build_panel(
            panels, 0, "开始日期", "left")
        ttk.Separator(panels, orient="vertical", style="Calendar.TSeparator").grid(
            row=0, column=1, sticky="ns", padx=10)
        self.right_month_label, self.right_days_frame = self._build_panel(
            panels, 2, "结束日期", "right")
        self.hint_label = ttk.Label(outer, text="", style="CalendarHint.TLabel")
        self.hint_label.pack(fill="x", pady=(8, 0))
        ttk.Separator(outer, style="Calendar.TSeparator").pack(fill="x", pady=(8, 8))
        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        ttk.Button(footer, text="今天", style="CalendarTodayAction.TButton",
                   command=self.select_today).pack(side="left")
        ttk.Checkbutton(footer, text="翻月联动", variable=self.link_var,
                        command=self._apply_link_choice).pack(side="right")
        self._render()
        popup.update_idletasks()
        x = min(self.winfo_rootx(), max(0, popup.winfo_screenwidth() - popup.winfo_reqwidth()))
        y = self.winfo_rooty() + self.winfo_height() + 3
        if y + popup.winfo_reqheight() > popup.winfo_screenheight():
            y = max(0, self.winfo_rooty() - popup.winfo_reqheight())
        popup.geometry(f"+{max(0, x)}+{y}")
        popup.deiconify()
        self._dismiss_binding = self.winfo_toplevel().bind(
            "<ButtonPress-1>", self._outside_click, add="+"
        )

    def _build_panel(self, parent, column: int, title: str, side: str):
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=column, sticky="nsew")
        ttk.Label(panel, text=title, style="CalendarPanelTitle.TLabel").pack(fill="x")
        navigation = ttk.Frame(panel)
        navigation.pack(fill="x", pady=(4, 0))
        for button_column, (label, offset) in enumerate((("«", -12), ("‹", -1))):
            ttk.Button(navigation, text=label, width=2, style="CalendarRangeNav.TButton",
                       command=lambda delta=offset, target=side: self.change_month(target, delta)
                       ).grid(row=0, column=button_column)
        month_label = ttk.Label(navigation, style="CalendarRangeMonth.TLabel", width=10, anchor="center")
        month_label.grid(row=0, column=2, sticky="ew", padx=4)
        navigation.columnconfigure(2, weight=1)
        for button_column, (label, offset) in enumerate((("›", 1), ("»", 12)), start=3):
            ttk.Button(navigation, text=label, width=2, style="CalendarRangeNav.TButton",
                       command=lambda delta=offset, target=side: self.change_month(target, delta)
                       ).grid(row=0, column=button_column)
        days = ttk.Frame(panel)
        days.pack(fill="both", expand=True, pady=(8, 0))
        return month_label, days

    def _render(self) -> None:
        start, end = self.selected_dates()
        if self.left_month_label is None or self.right_month_label is None:
            return
        self.left_month_label.configure(text=f"{self.left_month.year}年{self.left_month.month}月")
        self.right_month_label.configure(text=f"{self.right_month.year}年{self.right_month.month}月")
        draw_month(self.left_days_frame, self.left_month,
                   day_style=lambda day: self._day_style(day, start, end),
                   on_pick=lambda day: self.pick("left", day), palette=RANGE_PALETTE)
        draw_month(self.right_days_frame, self.right_month,
                   day_style=lambda day: self._day_style(day, start, end),
                   on_pick=lambda day: self.pick("right", day), palette=RANGE_PALETTE)
        self._update_hint(start, end)

    @staticmethod
    def _day_style(day: date, start: date | None, end: date | None) -> str:
        # 起止日加粗；macOS 的原生按钮不吃背景色，区间底色画不出来，所以用提示行
        # 报告“已选 A 至 B，共 N 天”，不依赖颜色表达区间。
        if day == start or day == end:
            return "CalendarRangeDaySelected.Primary.TButton"
        if day == date.today():
            return "CalendarRangeToday.TButton"
        return "CalendarRangeDay.TButton"

    def _update_hint(self, start: date | None, end: date | None) -> None:
        if self.hint_label is None:
            return
        if start is None and end is None:
            text = "左侧选择开始日期，右侧选择结束日期"
        elif start is None:
            text = f"结束 {end.isoformat()}，请在左侧选择开始日期"
        elif end is None:
            text = f"开始 {start.isoformat()}，请在右侧选择结束日期"
        elif end < start:
            text = f"开始 {start.isoformat()} 晚于结束 {end.isoformat()}，请重新选择"
        else:
            text = f"已选 {start.isoformat()} 至 {end.isoformat()}，共 {(end - start).days + 1} 天"
        if not self.link_months:
            text = f"{text}（两月独立翻页）"
        self.hint_label.configure(text=text)

    def change_month(self, side: str, delta: int) -> None:
        if side == "left":
            self.left_month = shift_month(self.left_month, delta)
            if self.link_months:
                self.right_month = shift_month(self.left_month, 1)
        else:
            self.right_month = shift_month(self.right_month, delta)
            if self.link_months:
                self.left_month = shift_month(self.right_month, -1)
        self._render()

    def pick(self, side: str, day: date) -> None:
        start, end = self.selected_dates()
        if side == "left":
            start = day
            if end is None or end < day:
                end = day
            self._apply(start, end)
            return
        end = day
        if start is None or start > day:
            start = day
        self._apply(start, end)
        self.close_calendar()
        self.end_entry.focus_set()

    def select_today(self) -> None:
        today = date.today()
        self._apply(today, today)
        self.close_calendar()
        self.end_entry.focus_set()

    def _apply(self, start: date, end: date) -> None:
        self.start_variable.set(start.isoformat())
        self.end_variable.set(end.isoformat())
        self._render()

    def set_linked(self, linked: bool) -> None:
        self.link_months = bool(linked)
        self.link_var.set(self.link_months)
        if self.link_months:
            self.right_month = shift_month(self.left_month, 1)
        self._render()

    def _apply_link_choice(self) -> None:
        self.set_linked(self.link_var.get())

    def _outside_click(self, event) -> None:
        popup = self.popup
        def contains(widget) -> bool:
            return (
                widget.winfo_rootx() <= event.x_root < widget.winfo_rootx() + widget.winfo_width()
                and widget.winfo_rooty() <= event.y_root < widget.winfo_rooty() + widget.winfo_height()
            )
        if popup is not None and not contains(popup) and not contains(self.start_entry) \
                and not contains(self.end_entry):
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
