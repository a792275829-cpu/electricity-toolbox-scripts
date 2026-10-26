from __future__ import annotations

import tkinter as tk
import json
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .page_support import DateInput, parse_date_input
from .page_support import add_path_row, open_directory
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage
from .market_editor import MarketEditor, market_module


class MarketReportPage(ToolPage):
    def __init__(self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="南方分公司电力市场日报",
            description="读取 D、D-1、D-2 收益表和清能系统数据，生成手机预览版与打印版 PDF。",
        )
        self.paths = paths
        self.report_date = tk.StringVar(master=self, value=(date.today() + timedelta(days=1)).isoformat())
        self.day_report = tk.StringVar(master=self)
        self.previous_report = tk.StringVar(master=self)
        self.d2_report = tk.StringVar(master=self)
        self.dongguan_operation = tk.StringVar(master=self)
        self.realtime_maintenance = tk.StringVar(master=self)
        self.manual_market = tk.StringVar(master=self, value="")
        self.manual_status = tk.StringVar(master=self, value="未填写；网站数据优先")
        self.market_editor = None
        self._manual_date = self.report_date.get()
        self.report_date.trace_add("write", self._clear_manual_on_date_change)

        form = ttk.LabelFrame(self.content, text="输入", padding=14)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="报告日期").grid(row=0, column=0, sticky="w", pady=5)
        DateInput(form, textvariable=self.report_date, width=18).grid(row=0, column=1, sticky="w", pady=5)

        buttons = []
        for row, label, variable in (
            (1, "d 日收益表", self.day_report),
            (2, "上一日对比表（必选）", self.previous_report),
            (3, "d-2 收益表", self.d2_report),
        ):
            buttons.append(add_path_row(
                form, row=row, label=label, variable=variable,
                command=lambda value=variable: self.choose_file(value), button_text="选择文件",
            ))
        ttk.Label(form, text="东莞运行方式修正（可选）").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.dongguan_operation).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="实时检修容量（可选，万千瓦）").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.realtime_maintenance, width=18).grid(row=5, column=1, sticky="w", pady=5)

        market_form = ttk.LabelFrame(self.content, text="市场情况补录（可选）", padding=14)
        market_form.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        market_form.columnconfigure(1, weight=1)
        self.manual_button = ttk.Button(market_form, text="填写日前及实时市场", command=self.open_market_editor)
        self.manual_button.grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(market_form, textvariable=self.manual_status, style="Muted.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(market_form, text="日前、实时各 9 项的当前值与对比值，共 36 格；自动计算增减。", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(self.content, text="输出：手机预览版 PDF、打印版 PDF。登录使用工具箱现有配置。", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self.run_button = ttk.Button(self.actions, text="生成日报", command=self.start_report)
        self.run_button.pack(side="left")
        ttk.Button(self.actions, text="打开输出目录", command=lambda: open_directory(paths.market_report_output_dir)).pack(
            side="left", padx=(8, 0)
        )
        self.register_busy_widgets(self.run_button, self.manual_button, *buttons)

    def _clear_manual_on_date_change(self, *_):
        if self.report_date.get() == self._manual_date:
            return
        self._manual_date = self.report_date.get()
        self.manual_market.set("")
        self.manual_status.set("报告日期已变更，补录已清空")
        if self.market_editor is not None and self.market_editor.winfo_exists():
            self.market_editor.close()

    def save_manual_market(self, data):
        self.manual_market.set(json.dumps(data, ensure_ascii=False) if data else "")
        count = sum(len(pair) for period in ("day_ahead", "realtime") for pair in data.get(period, {}).values())
        self.manual_status.set(f"已填 {count}/36 格；网站有效值优先" if count else "未填写；网站数据优先")

    def open_market_editor(self):
        try:
            selected = parse_date_input(self.report_date.get())
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)
            return
        if self.market_editor is not None and self.market_editor.winfo_exists():
            self.market_editor.lift()
            return
        initial = json.loads(self.manual_market.get()) if self.manual_market.get() else {}
        self.market_editor = MarketEditor(self, selected, initial, self.save_manual_market)

    def choose_file(self, variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="选择收益表 Excel", filetypes=[("Excel 工作簿", "*.xlsx")]
        )
        if selected:
            variable.set(selected)

    def build_command(self) -> list[str]:
        selected = parse_date_input(self.report_date.get())
        sources = [Path(value.get().strip()) for value in (
            self.day_report, self.previous_report, self.d2_report
        )]
        if any(not variable.get().strip() for variable in (
            self.day_report, self.previous_report, self.d2_report
        )):
            raise ValueError("请选择 d 日收益表、上一日对比表和 d-2 收益表。")
        missing = [str(source) for source in sources if not source.is_file()]
        if missing:
            raise ValueError("文件不存在：" + "、".join(missing))
        command = [
            python_executable(), str(self.paths.market_report_runner), selected.isoformat(),
            "--day-report", str(sources[0]), "--previous-report", str(sources[1]),
            "--d2-report", str(sources[2]),
        ]
        operation = self.dongguan_operation.get().strip()
        if operation:
            command.extend(["--dongguan-operation", operation])
        maintenance = self.realtime_maintenance.get().strip()
        if maintenance:
            value = float(maintenance)
            if not isfinite(value) or value < 0:
                raise ValueError("实时检修容量必须是不小于 0 的有限数值。")
            command.extend(["--realtime-maintenance", str(value)])
        if self.manual_market.get():
            data = market_module().normalize_manual_market(json.loads(self.manual_market.get()), selected)
            command.extend(["--manual-market", json.dumps(data, ensure_ascii=False)])
        return command

    def start_report(self) -> None:
        try:
            command = self.build_command()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)
            return
        self.clear_log()
        self.run_process(
            command, cwd=self.paths.market_report_dir, status="正在生成电力市场日报...",
            on_success=lambda _code: messagebox.showinfo(
                "生成完成", f"两份 PDF 已保存到：\n{self.paths.market_report_output_dir}", parent=self
            ),
        )
