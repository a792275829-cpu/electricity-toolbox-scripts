from __future__ import annotations

import tkinter as tk
import json
import queue
import threading
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .page_support import parse_date_input
from .page_support import add_path_row, open_directory
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage
from .market_editor import MarketEditor, market_module
from .market_report_dates import report_date_from_day_report


class MarketReportPage(ToolPage):
    tool_id = 'market-report'
    def __init__(self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="南方分公司电力市场日报",
        )
        self.paths = paths
        self.report_date = tk.StringVar(master=self)
        self.day_report = tk.StringVar(master=self)
        self.previous_report = tk.StringVar(master=self)
        self.d2_report = tk.StringVar(master=self)
        self.manual_market = tk.StringVar(master=self, value="")
        self.market_editor = None
        self._manual_date = self.report_date.get()
        self.report_date.trace_add("write", self._clear_manual_on_date_change)
        # The report date is never typed: it comes from the D workbook, read off
        # the Tk thread so a large workbook cannot freeze the window.
        self._date_token = 0
        self._date_source = ""
        self._date_error = ""
        self._date_polls: dict[int, str] = {}

        form = ttk.LabelFrame(self.content, text="输入", padding=14)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="报告日期").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        date_row = ttk.Frame(form)
        date_row.grid(row=0, column=1, columnspan=2, sticky="w", pady=5)
        ttk.Entry(date_row, textvariable=self.report_date, width=18, state="readonly").pack(side="left")
        self.date_hint = ttk.Label(date_row, text="选择 D 日收益表后自动识别", style="Muted.TLabel")
        self.date_hint.pack(side="left", padx=(10, 0))

        buttons = []
        for row, label, variable, hook in (
            (1, "D 日收益表", self.day_report, self.read_report_date),
            (2, "D-1 日收益表", self.previous_report, None),
            (3, "D-2 日收益表", self.d2_report, None),
        ):
            buttons.append(add_path_row(
                form, row=row, label=label, variable=variable,
                command=lambda value=variable, callback=hook: self.choose_file(value, callback),
                button_text="选择文件",
            ))

        self.run_button = ttk.Button(self.actions, text="生成日报", command=self.start_report)
        self.run_button.pack(side="left")
        ttk.Button(self.actions, text="打开输出目录", command=lambda: open_directory(paths.market_report_output_dir)).pack(
            side="left", padx=(8, 0)
        )
        self.manual_button = ttk.Button(self.actions, text="手动填写数据", command=self.open_market_editor)
        self.manual_button.pack(side="left", padx=(8, 0))
        self.register_busy_widgets(self.run_button, self.manual_button, *buttons)

    def _clear_manual_on_date_change(self, *_):
        if self.report_date.get() == self._manual_date:
            return
        self._manual_date = self.report_date.get()
        self.manual_market.set("")
        if self.market_editor is not None and self.market_editor.winfo_exists():
            self.market_editor.close()

    def save_manual_market(self, data):
        self.manual_market.set(json.dumps(data, ensure_ascii=False) if data else "")

    def read_report_date(self, path: Path) -> None:
        """Identify the report date from the chosen D workbook without blocking Tk."""
        self._date_token += 1
        token = self._date_token
        self._date_error = ""
        self.date_hint.configure(text="正在识别报告日期…")
        results: queue.Queue = queue.Queue()

        def read() -> None:
            try:
                results.put((path, report_date_from_day_report(path), ""))
            except Exception as exc:  # 读取失败必须回到界面提示，不能静默吞掉
                results.put((path, None, str(exc) or exc.__class__.__name__))

        threading.Thread(target=read, name="market-report-date", daemon=True).start()
        self._poll_report_date(results, token)

    def _poll_report_date(self, results: queue.Queue, token: int) -> None:
        try:
            path, detected, error = results.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self._date_polls[token] = self.after(80, lambda: self._poll_report_date(results, token))
            return
        self._date_polls.pop(token, None)
        if token != self._date_token:
            return  # 用户已重新选择文件，丢弃过期结果
        if detected is None:
            self._date_error = error
            self._date_source = ""
            self.report_date.set("")
            self.date_hint.configure(text=f"识别失败：{error}")
            return
        self._date_source = str(path)
        self.report_date.set(detected.isoformat())
        self.date_hint.configure(text="已按 D 日收益表识别")

    def derive_report_date(self, path: Path) -> date:
        try:
            return report_date_from_day_report(path)
        except Exception as exc:
            raise ValueError(f"无法从 D 日收益表识别报告日期：{exc}") from exc

    def current_report_date(self) -> date:
        """Report date bound to the selected D workbook; a stale value is re-read."""
        text = self.report_date.get().strip()
        day_path = self.day_report.get().strip()
        if text and (not day_path or self._date_source in ("", day_path)):
            return parse_date_input(text)
        if not day_path:
            if self._date_error:
                raise ValueError(f"无法从 D 日收益表识别报告日期：{self._date_error}")
            raise ValueError("请先选择 D 日收益表，报告日期将自动识别。")
        detected = self.derive_report_date(Path(day_path))
        self._date_source = day_path
        self._date_error = ""
        self.report_date.set(detected.isoformat())
        self.date_hint.configure(text="已按 D 日收益表识别")
        return detected

    def open_market_editor(self):
        try:
            selected = self.current_report_date()
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)
            return
        if self.market_editor is not None and self.market_editor.winfo_exists():
            self.market_editor.lift()
            return
        initial = json.loads(self.manual_market.get()) if self.manual_market.get() else {}
        self.market_editor = MarketEditor(self, selected, initial, self.save_manual_market)

    def choose_file(self, variable: tk.StringVar, on_selected=None) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="选择收益表 Excel", filetypes=[("Excel 工作簿", "*.xlsx")]
        )
        if not selected:
            return
        variable.set(selected)
        if on_selected is not None:
            on_selected(Path(selected))

    def build_command(self) -> list[str]:
        variables = (self.day_report, self.previous_report, self.d2_report)
        if any(not variable.get().strip() for variable in variables):
            raise ValueError("请选择 D 日收益表、D-1 日收益表和 D-2 日收益表。")
        sources = [Path(variable.get().strip()) for variable in variables]
        missing = [str(source) for source in sources if not source.is_file()]
        if missing:
            raise ValueError("文件不存在：" + "、".join(missing))
        selected = self.current_report_date()
        command = [
            python_executable(), str(self.paths.market_report_runner), selected.isoformat(),
            "--day-report", str(sources[0]), "--previous-report", str(sources[1]),
            "--d2-report", str(sources[2]),
        ]
        if self.manual_market.get():
            data = market_module().normalize_manual_market(json.loads(self.manual_market.get()), selected)
            command.extend(["--manual-market", json.dumps(data, ensure_ascii=False)])
        return command

    def destroy(self) -> None:
        for after_id in self._date_polls.values():
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        self._date_polls.clear()
        super().destroy()

    def start_report(self) -> None:
        try:
            command = self.build_command()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)
            return
        self.clear_log()
        self.run_process(
            command, action='run', cwd=self.paths.market_report_dir, status="正在生成电力市场日报...",
            on_success=lambda _code: messagebox.showinfo(
                "生成完成", f"两份 PDF 已保存到：\n{self.paths.market_report_output_dir}", parent=self
            ),
        )
