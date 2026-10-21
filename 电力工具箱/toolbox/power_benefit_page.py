from __future__ import annotations

from . import commands

import tkinter as tk
from toolbox_choices import OptionCheckbutton, SettingCheckbutton

from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_dialogs import FileDialogState
from .page_support import DateInput, DATE_INPUT_ERROR, DATE_INPUT_HINT, parse_date_input
from .page_support import add_path_row, open_directory
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage


DEFAULT_DIRECTORY = Path.home() / "Desktop" / "日常工作"
DEFAULT_NAMES = ("功率预测调整收益统计-归湖.xlsx", "功率预测调整收益统计-鮀莲(2)(2).xlsx")


class PowerBenefitUpdatePage(ToolPage):
    def __init__(self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry,
                 *, today: date | None = None) -> None:
        super().__init__(parent, registry=registry, title="功率调整收益统计表更新",
                         description="识别文件名中的归湖、鮀莲，补齐截至 D 日的六列小时数据。")
        self.paths = paths
        self.file_dialogs = FileDialogState(paths.file_dialog_settings)
        self.workbooks = [tk.StringVar(master=self, value=str(DEFAULT_DIRECTORY / name))
                          for name in DEFAULT_NAMES]
        self.selected = [tk.BooleanVar(master=self, value=True) for _ in DEFAULT_NAMES]
        self.start_date = tk.StringVar(master=self, value="")
        self.end_date = tk.StringVar(master=self, value=(today or date.today()).isoformat())
        self.create_backup = tk.BooleanVar(master=self, value=True)
        form = ttk.LabelFrame(self.content, text="更新设置", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        buttons = []
        for index, station in enumerate(("归湖", "鮀莲")):
            buttons.append(add_path_row(
                form, row=index, label=f"{station}工作簿", variable=self.workbooks[index],
                command=lambda i=index: self.choose_workbook(i), button_text="选择文件"))
            OptionCheckbutton(form, text="更新", variable=self.selected[index]).grid(
                row=index, column=3, padx=(8, 0))
        for row, label, variable in ((2, "开始日期", self.start_date), (3, "截止日期 D", self.end_date)):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4)
            DateInput(form, textvariable=variable, width=16).grid(row=row, column=1, sticky="w", pady=4)
        ttk.Label(form, text="开始日期留空：检查模板所有历史空白；" + DATE_INPUT_HINT,
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=4)
        SettingCheckbutton(form, text="写入前生成时间戳备份", variable=self.create_backup).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=4)
        mapping = ttk.LabelFrame(self.content, text="更新口径", padding=12)
        mapping.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(mapping, text=(
            "C 原始功率；J 申报功率；K 日前电量；L 日前电价；M 上网电量；N 实时电价。\n"
            "六列均按运行日期匹配 0–23 点，每列 24 个数。非空单元格（含 0、公式）跳过。\n"
            "保留模板格式及收益公式；网站未发布的日期或字段留空，后续运行继续补齐。"),
            justify="left").grid(row=0, column=0, sticky="w")
        self.update_button = ttk.Button(self.actions, text="抓取并更新", command=self.start_update)
        self.update_button.pack(side="left")
        ttk.Button(self.actions, text="打开工作簿目录", command=lambda: open_directory(
            Path(self.workbooks[0].get()).expanduser().parent)).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(*buttons, self.update_button)

    def choose_workbook(self, index: int) -> None:
        key = f"power_benefit_{index}"
        selected = filedialog.askopenfilename(
            title="选择功率预测调整收益统计工作簿", parent=self,
            initialdir=str(self.file_dialogs.initial_directory(key, self.workbooks[index].get())),
            filetypes=[("Excel 文件", "*.xlsx")])
        if selected:
            self.workbooks[index].set(selected)
            self.file_dialogs.remember_file(key, selected)

    def build_update_command(self, workbooks: list[Path], end_date: date, *,
                             start_date: date | None, create_backup: bool) -> list[str]:
        return commands.power_benefit(self.paths, workbooks, end_date, start_date=start_date, create_backup=create_backup)

    def start_update(self) -> None:
        workbooks = [Path(value.get().strip()).expanduser()
                     for value, selected in zip(self.workbooks, self.selected) if selected.get()]
        try:
            if not workbooks:
                raise ValueError("请至少选择一个工作簿。")
            for path in workbooks:
                if not path.is_file() or path.suffix.lower() != ".xlsx":
                    raise ValueError(f"请选择有效的 Excel 工作簿：\n{path}")
                if sum(station in path.name for station in ("归湖", "鮀莲")) != 1:
                    raise ValueError("文件名必须包含且仅包含归湖或鮀莲中的一个名称。")
            try:
                start = parse_date_input(self.start_date.get()) if self.start_date.get().strip() else None
                end = parse_date_input(self.end_date.get())
            except ValueError:
                raise ValueError(DATE_INPUT_ERROR) from None
            if start and start > end:
                raise ValueError("截止日期不能早于开始日期。")
        except ValueError as exc:
            messagebox.showerror("更新设置无效", str(exc), parent=self)
            return
        self.clear_log()
        self.run_process(self.build_update_command(
            workbooks, end, start_date=start, create_backup=self.create_backup.get()),
            cwd=self.paths.power_benefit_update_dir, status="正在补齐功率调整收益统计表...",
            cancellable=False,
            on_success=lambda _: messagebox.showinfo("完成", "任务已完成，请查看日志中的写入及待发布数据。", parent=self))
