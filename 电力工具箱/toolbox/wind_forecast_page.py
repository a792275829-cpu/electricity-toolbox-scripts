from __future__ import annotations

from . import commands

import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk

from .page_support import DateInput, DATE_INPUT_ERROR, normalize_date_input
from .page_support import open_directory
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage


class WindForecastPage(ToolPage):
    tool_id = 'wind-forecast'
    def __init__(self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="风力预测截图",
            description="抓取连续7个预测日期的风力数据，生成日期横向、00:00至23:00纵向的表格截图。",
        )
        self.paths = paths
        self._no_data_reason: str | None = None
        self.run_date = tk.StringVar(
            master=self, value=(date.today() + timedelta(days=1)).isoformat()
        )

        form = ttk.LabelFrame(self.content, text="抓取设置", padding=14)
        form.grid(row=0, column=0, sticky="ew")
        ttk.Label(form, text="运行日期").grid(row=0, column=0, sticky="w")
        DateInput(form, textvariable=self.run_date, width=18).grid(
            row=0, column=1, sticky="w", padx=(8, 14)
        )
        ttk.Label(form, text="点击日期框选择；输出连续7个预测日期", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w"
        )

        self.generate_button = ttk.Button(self.actions, text="生成截图", command=self.start_generate)
        self.generate_button.pack(side="left")
        ttk.Button(
            self.actions,
            text="打开输出目录",
            command=lambda: open_directory(self.paths.wind_forecast_output_dir),
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(self.generate_button)
        self.append_log("选择运行日期后开始抓取；登录态与其他工具相互独立。")

    def build_generate_command(self, run_date: str) -> list[str]:
        return commands.dated_script(self.paths.wind_forecast, run_date)

    def append_log(self, text: str) -> None:
        if text.startswith("无数据："):
            self._no_data_reason = text.removeprefix("无数据：").strip()
        super().append_log(text)

    def _generation_finished(self, _code: int) -> None:
        if self._no_data_reason is not None:
            self.set_busy(False, "无数据")
            messagebox.showinfo("无数据", self._no_data_reason, parent=self)
        else:
            messagebox.showinfo("完成", "7天风力预测截图已生成。", parent=self)

    def start_generate(self) -> None:
        if self.busy:
            return
        run_date = self.run_date.get().strip()
        try:
            run_date = normalize_date_input(run_date)
        except ValueError:
            messagebox.showerror("日期格式错误", DATE_INPUT_ERROR, parent=self)
            return
        self.clear_log()
        self._no_data_reason = None
        self.run_process(
            self.build_generate_command(run_date),
            action='run', cwd=self.paths.wind_forecast_dir,
            status="正在抓取风力预测并生成截图...",
            on_success=self._generation_finished,
        )

    def start_login(self) -> None:
        self.run_process(
            [python_executable(), str(self.paths.wind_forecast), "--login"],
            action='login', cwd=self.paths.wind_forecast_dir,
            status="正在打开风力预测登录窗口...",
        )
