from __future__ import annotations

from . import commands

import tkinter as tk
from toolbox_shared.choices import OptionCheckbutton, SettingCheckbutton

from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_dialogs import FileDialogState
from .page_support import DATE_INPUT_ERROR, add_date_row, add_path_row, open_directory, parse_date_input
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage


DEFAULT_DIRECTORY = Path.home() / "Desktop" / "日常工作"
DEFAULT_NAMES = ("功率预测调整收益统计-归湖.xlsx", "功率预测调整收益统计-鮀莲(2)(2).xlsx", "海风博弈收益统计-新.xlsx")


class PowerBenefitUpdatePage(ToolPage):
    tool_id = 'power-benefit'
    def __init__(self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry,
                 *, today: date | None = None) -> None:
        super().__init__(parent, registry=registry, title="功率调整收益统计表更新")
        self.paths = paths
        self.file_dialogs = FileDialogState(paths.file_dialog_settings)
        self.workbooks = [tk.StringVar(master=self, value=str(DEFAULT_DIRECTORY / name))
                          for name in DEFAULT_NAMES]
        self.selected = [tk.BooleanVar(master=self, value=False) for _ in DEFAULT_NAMES]
        self.start_date = tk.StringVar(master=self, value="")
        self.end_date = tk.StringVar(master=self, value=(today or date.today()).isoformat())
        self.overwrite = tk.BooleanVar(master=self, value=True)
        self.create_backup = tk.BooleanVar(master=self, value=False)
        form = ttk.LabelFrame(self.content, text="更新设置", padding=8)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        add_date_row(
            form, row=0, start_variable=self.start_date, end_variable=self.end_date,
            label="日期范围", hint="结束日期为 D", start_optional=True)
        buttons = []
        for index, station in enumerate(("归湖", "鮀莲", "海风")):
            buttons.append(add_path_row(
                form, row=1 + index, label=f"{station}工作簿", variable=self.workbooks[index],
                command=lambda i=index: self.choose_workbook(i), button_text="选择文件"))
        stations = ttk.Frame(form)
        stations.grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(stations, text="更新场站：").pack(side="left")
        self.station_checkbuttons = []
        for index, station in enumerate(("归湖", "鮀莲", "海风")):
            button = OptionCheckbutton(stations, text=station, variable=self.selected[index])
            button.pack(side="left", padx=(0, 12))
            self.station_checkbuttons.append(button)
        ttk.Label(stations, text="海风覆盖必须填写开始日期；其他场站留空检查历史空白",
                  style="Muted.TLabel").pack(side="left", padx=(16, 0))
        options = ttk.Frame(form)
        options.grid(row=5, column=0, columnspan=3, sticky="w", pady=(5, 0))
        SettingCheckbutton(options, text="覆盖非空单元格", variable=self.overwrite).pack(side="left")
        SettingCheckbutton(options, text="写入前生成时间戳备份",
                           variable=self.create_backup).pack(side="left", padx=(16, 0))
        mapping = ttk.LabelFrame(self.content, text="更新口径", padding=8)
        mapping.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(mapping, text=(
            "归湖/鮀莲：C 原始功率；J 申报功率；K 日前电量；L 日前电价；M 上网电量；N 实时电价。\n"
            "海风：C 原始功率；R 申报功率；N 上网电量；M 日前电价；O 实时电价。覆盖所选日期内五列的值及公式。\n"
            "均按运行日期匹配 0–23 点，每列 24 个数。归湖、鮀莲默认只补空白；勾选“覆盖非空单元格”后覆盖已有数值，公式单元格仍跳过。\n"
            "模板缺少所选日期或月份时，列出缺失日期并停止，不会仅更新部分日期。\n"
            "保留模板格式及其他收益公式；未发布字段保留原值，后续运行继续更新。"),
            justify="left").grid(row=0, column=0, sticky="w")
        self.update_button = ttk.Button(self.actions, text="抓取并更新", command=self.start_update)
        self.update_button.pack(side="left")
        ttk.Button(self.actions, text="打开工作簿目录", command=lambda: open_directory(
            Path(self.workbooks[0].get()).expanduser().parent)).pack(side="left", padx=(8, 0))
        # A page taller than the viewport sends every switch through Tk's
        # clipped-canvas repaint (measured ~3x): 10 log lines push this form to
        # 816 px against ~790 px of space. Two fewer lines keep it in view.
        self.log_text.configure(height=8)
        self.register_busy_widgets(*buttons, *self.station_checkbuttons, self.update_button)

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
        return commands.power_benefit(
            self.paths, workbooks, end_date, start_date=start_date,
            create_backup=create_backup, overwrite=self.overwrite.get(),
        )

    def start_update(self) -> None:
        workbooks = [Path(value.get().strip()).expanduser()
                     for value, selected in zip(self.workbooks, self.selected) if selected.get()]
        try:
            if not workbooks:
                raise ValueError("请至少选择一个工作簿。")
            for path in workbooks:
                if not path.is_file() or path.suffix.lower() != ".xlsx":
                    raise ValueError(f"请选择有效的 Excel 工作簿：\n{path}")
                if sum(station in path.name for station in ("归湖", "鮀莲", "海风")) != 1:
                    raise ValueError("文件名必须包含且仅包含归湖、鮀莲或海风中的一个名称。")
            try:
                start = parse_date_input(self.start_date.get()) if self.start_date.get().strip() else None
                end = parse_date_input(self.end_date.get())
            except ValueError:
                raise ValueError(DATE_INPUT_ERROR) from None
            if any("海风" in path.name for path in workbooks) and start is None:
                raise ValueError("海风覆盖更新必须填写开始日期。")
            if start and start > end:
                raise ValueError("截止日期不能早于开始日期。")
        except ValueError as exc:
            messagebox.showerror("更新设置无效", str(exc), parent=self)
            return
        self.clear_log()
        self.run_process(self.build_update_command(
            workbooks, end, start_date=start, create_backup=self.create_backup.get()),
            action='run', cwd=self.paths.power_benefit_update_dir, status="正在补齐功率调整收益统计表...",
            cancellable=False,
            on_success=lambda _: messagebox.showinfo("完成", "任务已完成，请查看日志中的写入及待发布数据。", parent=self))
