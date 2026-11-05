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
    style.configure("CalendarRangeStatus.TLabel", font=(family, 12, "bold"),
                    foreground="#2f2f2b", anchor="w")
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
               palette: MonthPalette = SINGLE_PALETTE, on_adjacent_pick=None) -> None:
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
                adjacent = date.fromordinal(ordinal) if 1 <= ordinal <= date.max.toordinal() else None
                label = str(adjacent.day) if adjacent else ""
                cell = ttk.Label(frame, text=label, style=palette.adjacent,
                                 width=palette.day_width, anchor="center")
                cell.grid(row=row, column=column, sticky="nsew", padx=2, pady=3)
                # 月初、月末补位的浅色日期同样可以点，跨月边界不用先翻页再选。
                if adjacent is not None and label and on_adjacent_pick is not None:
                    cell.bind("<Button-1>", lambda _event, value=adjacent: on_adjacent_pick(value))
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

    选择分两步：先点任意一张月历里的日期定开始日期，再点任意一张月历里的日期定
    结束日期，选完结束日期弹层自动收起。两张月历都可以点，所以起止落在同一个月
    时也能在同一个面板里连点两下选完；相邻月份补位的浅色日期同样可点，跨月边界
    不用先翻页。从结束日期框打开弹层时直接进入第二步，点一下即收起。

    默认翻月联动，翻任意一张月历时两张一起推进、始终相邻；取消联动后各自翻页，
    适合跨较远月份反复核对起止日期。
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
        start_optional: bool = False,
    ):
        super().__init__(parent)
        self.start_variable = start_variable
        self.end_variable = end_variable
        self.popup: tk.Toplevel | None = None
        self._dismiss_binding: str | None = None
        self.link_months = bool(link_months)
        # 业务允许开始日期留空时（补缺并接续一类），提示行不催用户补开始日期。
        self.start_optional = bool(start_optional)
        self.link_var = tk.BooleanVar(master=self, value=self.link_months)
        # "start"：下一次点日期是在定开始日期；"end"：在定结束日期。
        self.stage = "start"
        self.left_month = date.today().replace(day=1)
        self.right_month = shift_month(self.left_month, 1)
        self.start_entry = ttk.Entry(self, textvariable=start_variable, width=width)
        self.start_entry.grid(row=0, column=0, sticky="ew")
        ttk.Label(self, text=separator).grid(row=0, column=1, padx=6)
        self.end_entry = ttk.Entry(self, textvariable=end_variable, width=width)
        self.end_entry.grid(row=0, column=2, sticky="ew")
        self.status_label: ttk.Label | None = None
        self.left_month_label: ttk.Label | None = None
        self.right_month_label: ttk.Label | None = None
        self.left_days_frame: ttk.Frame | None = None
        self.right_days_frame: ttk.Frame | None = None
        self.hint_label: ttk.Label | None = None
        for entry, side in ((self.start_entry, "start"), (self.end_entry, "end")):
            entry.bind("<FocusOut>", lambda _event, target=entry: self._normalize(target))
            entry.bind("<ButtonRelease-1>", lambda _event, target=side: self._open_from_click(target))
            entry.bind("<Down>", lambda _event, target=side: self._open_from_key(target))
            entry.bind("<Return>", lambda _event: self._commit_entry())
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

    def _open_from_click(self, source: str, _event=None) -> None:
        self.open_calendar(source)

    def _open_from_key(self, source: str, _event=None) -> str:
        self.open_calendar(source)
        if self.popup is not None:
            self.popup.focus_set()
        return "break"

    def _commit_entry(self) -> None:
        self._normalize(self.start_entry)
        self._normalize(self.end_entry)
        self.close_calendar()

    @staticmethod
    def _initial_stage(start: date | None) -> str:
        """没有指定从哪个框打开时：还没开始日期就先选开始，否则直接改结束日期。"""
        return "start" if start is None else "end"

    def open_calendar(self, source: str | None = None) -> None:
        if self.popup is not None and self.popup.winfo_exists():
            # 弹层已经打开时点另一个日期框：只切换这一步改开始还是结束，不重建弹层。
            if source in ("start", "end") and source != self.stage:
                self.stage = source
                self._render()
            return
        start, end = self.selected_dates()
        self.stage = source if source in ("start", "end") else self._initial_stage(start)
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
        self.status_label = ttk.Label(outer, text="", style="CalendarRangeStatus.TLabel")
        self.status_label.pack(fill="x", pady=(0, 6))
        panels = ttk.Frame(outer)
        panels.pack(fill="both", expand=True)
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(2, weight=1)
        self.left_month_label, self.left_days_frame = self._build_panel(panels, 0, "left")
        ttk.Separator(panels, orient="vertical", style="Calendar.TSeparator").grid(
            row=0, column=1, sticky="ns", padx=10)
        self.right_month_label, self.right_days_frame = self._build_panel(panels, 2, "right")
        self.hint_label = ttk.Label(outer, text="", style="CalendarHint.TLabel")
        self.hint_label.pack(fill="x", pady=(8, 0))
        ttk.Separator(outer, style="Calendar.TSeparator").pack(fill="x", pady=(8, 8))
        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        ttk.Button(footer, text="今天", style="CalendarTodayAction.TButton",
                   command=self.select_today).pack(side="left")
        # side="right" 先放的贴最右，所以先放联动开关、再放“确定”。
        ttk.Checkbutton(footer, text="翻月联动", variable=self.link_var,
                        command=self._apply_link_choice).pack(side="right")
        ttk.Button(footer, text="确定", style="CalendarTodayAction.TButton",
                   command=self.confirm).pack(side="right", padx=(0, 12))
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

    def _build_panel(self, parent, column: int, side: str):
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=column, sticky="nsew")
        navigation = ttk.Frame(panel)
        navigation.pack(fill="x")
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
        if self.popup is None or not self.popup.winfo_exists():
            return
        if self.left_month_label is None or self.right_month_label is None:
            return
        start, end = self.selected_dates()
        self.left_month_label.configure(text=f"{self.left_month.year}年{self.left_month.month}月")
        self.right_month_label.configure(text=f"{self.right_month.year}年{self.right_month.month}月")
        draw_month(self.left_days_frame, self.left_month,
                   day_style=lambda day: self._day_style(day, start, end),
                   on_pick=self.pick, palette=RANGE_PALETTE,
                   on_adjacent_pick=self.pick)
        draw_month(self.right_days_frame, self.right_month,
                   day_style=lambda day: self._day_style(day, start, end),
                   on_pick=self.pick, palette=RANGE_PALETTE,
                   on_adjacent_pick=self.pick)
        self._update_status(start)
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

    def _update_status(self, start: date | None) -> None:
        if self.status_label is None:
            return
        if self.stage == "start":
            text = "第 1 步 · 选择开始日期（左右月历都可点）"
        elif start is None:
            text = "第 2 步 · 选择结束日期"
        else:
            text = f"第 2 步 · 选择结束日期，开始 {start.isoformat()}"
        self.status_label.configure(text=text)

    def _update_hint(self, start: date | None, end: date | None) -> None:
        if self.hint_label is None:
            return
        if start is None and end is None:
            text = "点日期框弹出左右两张月历，先选开始、再选结束"
        elif start is None:
            text = (f"结束 {end.isoformat()}（开始日期可留空）" if self.start_optional
                    else f"结束 {end.isoformat()}，请再选开始日期")
        elif end is None:
            text = f"开始 {start.isoformat()}，请再选结束日期"
        elif end < start:
            text = f"开始 {start.isoformat()} 晚于结束 {end.isoformat()}，请重新选择"
        else:
            text = f"已选 {start.isoformat()} 至 {end.isoformat()}，共 {(end - start).days + 1} 天"
        if not self.link_months:
            text = f"{text}（两月独立翻页）"
        self.hint_label.configure(text=text)

    def _ensure_visible(self, day: date) -> None:
        month = day.replace(day=1)
        if month in (self.left_month, self.right_month):
            return
        self.left_month = month
        if self.link_months:
            self.right_month = shift_month(month, 1)

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

    def pick(self, day: date) -> None:
        """点月历里的任意一天：第一步定开始日期，第二步定结束日期并收起弹层。"""
        start, end = self.selected_dates()
        if self.stage == "start":
            start = day
            if end is None or end < start:
                end = start
            self._set_values(start, end)
            self.stage = "end"
            self._ensure_visible(start)
            self._render()
            return
        end = day
        if start is None:
            # 允许开始日期留空的页面，不因为选了结束日期就把开始日期填上。
            if not self.start_optional:
                start = end
        elif start > end:
            start = end
        self._set_values(start, end)
        self.close_calendar()
        if self.end_entry.winfo_exists():
            self.end_entry.focus_set()

    def select_today(self) -> None:
        today = date.today()
        self._set_values(today, today)
        self._ensure_visible(today)
        self.close_calendar()
        if self.end_entry.winfo_exists():
            self.end_entry.focus_set()

    def confirm(self) -> None:
        """收起弹层；当前输入框里的值就是最终值。"""
        self.close_calendar()
        if self.end_entry.winfo_exists():
            self.end_entry.focus_set()

    def _set_values(self, start: date | None, end: date | None) -> None:
        self.start_variable.set("" if start is None else start.isoformat())
        self.end_variable.set("" if end is None else end.isoformat())

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
        self.status_label = None
        self.left_month_label = None
        self.right_month_label = None
        self.left_days_frame = None
        self.right_days_frame = None
        self.hint_label = None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self.close_calendar()
