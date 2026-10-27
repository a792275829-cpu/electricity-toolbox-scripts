from __future__ import annotations

from . import commands

import tkinter as tk
from toolbox_choices import SettingCheckbutton

from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_dialogs import FileDialogState
from .page_support import DateInput, DATE_INPUT_ERROR, DATE_INPUT_HINT, parse_date_input
from .page_support import add_path_row, open_directory
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage


DEFAULT_WORKBOOK = (
    Path.home() / "Desktop" / "日常工作" / "交易系统信息收集20260901.xlsx"
)


class TransactionInfoUpdatePage(ToolPage):
    tool_id = 'transaction-info'
    def __init__(
        self,
        parent: tk.Misc,
        paths: ToolPaths,
        registry: TaskRegistry,
        *,
        today: date | None = None,
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="交易系统信息更新",
            description=(
                "抓取现货运行日报的市场负荷信息，按日前 D、实际 D-3 "
                "接续更新 Sheet1。"
            ),
        )
        self.paths = paths
        self.file_dialogs = FileDialogState(paths.file_dialog_settings)
        self.workbook = tk.StringVar(master=self, value=str(DEFAULT_WORKBOOK))
        self.start_date = tk.StringVar(master=self, value="")
        self.end_date = tk.StringVar(
            master=self, value=(today or date.today()).isoformat()
        )
        self.create_backup = tk.BooleanVar(master=self, value=False)

        form = ttk.LabelFrame(self.content, text="更新设置", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        self.workbook_button = add_path_row(
            form,
            row=0,
            label="目标工作簿",
            variable=self.workbook,
            command=self.choose_workbook,
            button_text="选择文件",
        )
        ttk.Label(form, text="日前开始日期").grid(row=1, column=0, sticky="w", pady=4)
        DateInput(form, textvariable=self.start_date, width=16).grid(
            row=1, column=1, sticky="w", pady=4
        )
        ttk.Label(
            form,
            text="留空时自动从 Sheet1 最后日前日期+1接续",
            style="Muted.TLabel",
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 0), pady=4)
        ttk.Label(form, text="日前结束日期").grid(row=2, column=0, sticky="w", pady=4)
        DateInput(form, textvariable=self.end_date, width=16).grid(
            row=2, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text=DATE_INPUT_HINT, style="Muted.TLabel").grid(
            row=2, column=2, sticky="w", padx=(8, 0), pady=4
        )
        SettingCheckbutton(
            form, text="写入前生成时间戳备份", variable=self.create_backup
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(7, 0))

        mapping = ttk.LabelFrame(self.content, text="更新口径", padding=12)
        mapping.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            mapping,
            text=(
                "左侧日前：统调、B类、A类、地方电、粤港联络线；西电行保留但数值留空。\n"
                "日前 B 类直接取网站数值，不扣减实际西电。\n"
                "右侧实际：写入六类数据。每类依次为最大、最小、平均、总电量。"
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        self.update_button = ttk.Button(
            self.actions, text="抓取并更新", command=self.start_update
        )
        self.update_button.pack(side="left")
        ttk.Button(
            self.actions,
            text="打开工作簿目录",
            command=self.open_workbook_directory,
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(
            self.workbook_button, self.update_button
        )
        self.append_log("本模块使用独立的登录态、锁文件和浏览器 profile。")

    def choose_workbook(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择交易系统信息收集工作簿",
            initialdir=str(
                self.file_dialogs.initial_directory(
                    "transaction_info_workbook", self.workbook.get()
                )
            ),
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            self.workbook.set(selected)
            self.file_dialogs.remember_file("transaction_info_workbook", selected)

    def build_update_command(
        self,
        workbook: Path,
        end_date: date,
        *,
        start_date: date | None,
        create_backup: bool,
    ) -> list[str]:
        return commands.transaction_info(self.paths, workbook, end_date, start_date=start_date, create_backup=create_backup)

    def start_update(self) -> None:
        workbook = Path(self.workbook.get().strip()).expanduser()
        if not workbook.is_file():
            messagebox.showerror(
                "文件无效", f"请选择有效的 Excel 工作簿：\n{workbook}", parent=self
            )
            return
        start_text = self.start_date.get().strip()
        try:
            start_date = (
                parse_date_input(start_text) if start_text else None
            )
            end_date = parse_date_input(self.end_date.get())
        except ValueError:
            messagebox.showerror(
                "日期格式错误", DATE_INPUT_ERROR, parent=self
            )
            return
        if start_date is not None and end_date < start_date:
            messagebox.showerror(
                "日期范围错误", "结束日期不能早于开始日期。", parent=self
            )
            return
        self.clear_log()
        self.run_process(
            self.build_update_command(
                workbook,
                end_date,
                start_date=start_date,
                create_backup=self.create_backup.get(),
            ),
            action='run', cwd=self.paths.transaction_info_update_dir,
            status="正在抓取并更新交易系统信息收集表...",
            cancellable=False,
            on_success=lambda _code: messagebox.showinfo(
                "完成", "任务已完成，请查看日志确认写入结果或无需更新。", parent=self
            ),
        )

    def start_login(self) -> None:
        self.run_process(
            [
                python_executable(),
                str(self.paths.transaction_info_update),
                "--login",
                "--headed",
            ],
            action='login', cwd=self.paths.transaction_info_update_dir,
            status="正在打开交易系统登录窗口...",
        )

    def open_workbook_directory(self) -> None:
        workbook = Path(self.workbook.get().strip()).expanduser()
        open_directory(workbook.parent if workbook.parent.exists() else DEFAULT_WORKBOOK.parent)
