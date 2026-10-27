from __future__ import annotations

from . import commands

import tkinter as tk
from toolbox_shared.choices import OptionCheckbutton, SettingCheckbutton

from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_dialogs import FileDialogState
from .page_support import DateInput, DATE_INPUT_ERROR, DATE_INPUT_HINT, parse_date_input
from .page_support import add_path_row
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage


DEFAULT_WORKBOOK_DIR = Path.home() / "Desktop" / "日常工作"
DEFAULT_GUIHU_WORKBOOK = DEFAULT_WORKBOOK_DIR / "2026年现货价格分析（2026.8.14更新）-归湖.xlsx"
DEFAULT_TUOLIAN_WORKBOOK = DEFAULT_WORKBOOK_DIR / "2026年现货价格分析（2026.8.14更新）-鮀莲.xlsx"
DEFAULT_HAIFENG_WORKBOOK = DEFAULT_WORKBOOK_DIR / "2026年现货价格分析-海风.xlsx"


class SpotPriceUpdatePage(ToolPage):
    tool_id = 'spot-price'
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
            title="现货价格分析表更新",
            description=(
                "勾选归湖、鮀莲、海风中的一个或多个场站，抓取日前和实时出清电价，"
                "按日期写入对应工作簿的 0时至23时。"
            ),
        )
        self.paths = paths
        self.file_dialogs = FileDialogState(paths.file_dialog_settings)
        current_date = today or date.today()
        self.start_date = tk.StringVar(
            master=self, value=(current_date - timedelta(days=1)).isoformat()
        )
        self.end_date = tk.StringVar(master=self, value=current_date.isoformat())
        self.guihu_workbook = tk.StringVar(master=self, value=str(DEFAULT_GUIHU_WORKBOOK))
        self.tuolian_workbook = tk.StringVar(master=self, value=str(DEFAULT_TUOLIAN_WORKBOOK))
        self.haifeng_workbook = tk.StringVar(master=self, value=str(DEFAULT_HAIFENG_WORKBOOK))
        self.station_workbooks = {
            "归湖": self.guihu_workbook,
            "鮀莲": self.tuolian_workbook,
            "海风": self.haifeng_workbook,
        }
        self.selected_stations = {
            label: tk.BooleanVar(master=self, value=True) for label in self.station_workbooks
        }
        self.overwrite = tk.BooleanVar(master=self, value=True)
        self.create_backup = tk.BooleanVar(master=self, value=False)

        form = ttk.LabelFrame(self.content, text="更新设置", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="开始日期").grid(row=0, column=0, sticky="w", pady=4)
        DateInput(form, textvariable=self.start_date, width=16).grid(
            row=0, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text="结束日期").grid(row=0, column=2, sticky="w", padx=(16, 0), pady=4)
        DateInput(form, textvariable=self.end_date, width=16).grid(
            row=0, column=3, sticky="w", padx=(8, 0), pady=4
        )
        ttk.Label(form, text=DATE_INPUT_HINT, style="Muted.TLabel").grid(
            row=0, column=4, sticky="w", padx=(8, 0), pady=4
        )

        self.guihu_button = add_path_row(
            form,
            row=1,
            label="归湖工作簿",
            variable=self.guihu_workbook,
            command=lambda: self.choose_workbook("归湖", self.guihu_workbook, "spot_price_guihu"),
            button_text="选择文件",
        )
        self.tuolian_button = add_path_row(
            form,
            row=2,
            label="鮀莲工作簿",
            variable=self.tuolian_workbook,
            command=lambda: self.choose_workbook("鮀莲", self.tuolian_workbook, "spot_price_tuolian"),
            button_text="选择文件",
        )
        self.haifeng_button = add_path_row(
            form,
            row=3,
            label="海风工作簿",
            variable=self.haifeng_workbook,
            command=lambda: self.choose_workbook("海风", self.haifeng_workbook, "spot_price_haifeng"),
            button_text="选择文件",
        )

        stations = ttk.Frame(form)
        stations.grid(row=4, column=0, columnspan=5, sticky="w", pady=(7, 0))
        ttk.Label(stations, text="更新场站：").pack(side="left")
        self.station_checkbuttons = []
        for label, variable in self.selected_stations.items():
            button = OptionCheckbutton(stations, text=label, variable=variable)
            button.pack(side="left", padx=(0, 12))
            self.station_checkbuttons.append(button)

        options = ttk.Frame(form)
        options.grid(row=5, column=0, columnspan=5, sticky="w", pady=(7, 0))
        SettingCheckbutton(options, text="覆盖已有非空价格", variable=self.overwrite).pack(anchor="w")
        SettingCheckbutton(options, text="写入前生成时间戳备份", variable=self.create_backup).pack(
            anchor="w", pady=(4, 0)
        )

        mapping = ttk.LabelFrame(self.content, text="固定映射", padding=12)
        mapping.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            mapping,
            text=(
                "华能（潮州潮安）新能源有限责任公司 → 归湖；"
                "华能（汕头金平）新能源有限责任公司 → 鮀莲。\n"
                "华能广东汕头海上风电有限责任公司（广澳海风场）→ 海风。\n"
                "日前出清电价 → 日前价格 B:Y；实时出清电价 → 实时价格 B:Y。\n"
                "超出表尾时连续接续日期行并沿用格式；已有日期按覆盖设置处理。\n"
                "日前、实时逐日独立更新；缺少完整 24 点的数据跳过，详情见日志。"
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        self.update_button = ttk.Button(self.actions, text="抓取并更新", command=self.start_update)
        self.update_button.pack(side="left")
        self.register_busy_widgets(
            self.guihu_button,
            self.tuolian_button,
            self.haifeng_button,
            *self.station_checkbuttons,
            self.update_button,
        )
        self.append_log("登录态、锁文件和浏览器 profile 与其他工具相互独立。")

    def choose_workbook(self, label: str, variable: tk.StringVar, key: str) -> None:
        selected = filedialog.askopenfilename(
            title=f"选择{label}现货价格分析工作簿",
            initialdir=str(self.file_dialogs.initial_directory(key, variable.get())),
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            variable.set(selected)
            self.file_dialogs.remember_file(key, selected)

    def build_update_command(
        self,
        start_date: date,
        end_date: date,
        guihu_workbook: Path,
        tuolian_workbook: Path,
        haifeng_workbook: Path = DEFAULT_HAIFENG_WORKBOOK,
        *,
        overwrite: bool,
        create_backup: bool,
        selected_stations: tuple[str, ...] | None = None,
    ) -> list[str]:
        selected = selected_stations if selected_stations is not None else tuple(
            label for label, variable in self.selected_stations.items() if variable.get()
        )
        return commands.spot_price(self.paths, start_date, end_date,
            {"归湖": guihu_workbook, "鮀莲": tuolian_workbook, "海风": haifeng_workbook},
            overwrite=overwrite, create_backup=create_backup, selected=selected)

    def selected_workbooks(self) -> dict[str, Path]:
        workbooks: dict[str, Path] = {}
        for label, selected in self.selected_stations.items():
            if not selected.get():
                continue
            workbook = Path(self.station_workbooks[label].get().strip()).expanduser()
            if not workbook.is_file():
                raise ValueError(f"请选择有效的{label}工作簿：\n{workbook}")
            workbooks[label] = workbook
        if not workbooks:
            raise ValueError("请至少选择一个更新场站。")
        if len({path.resolve() for path in workbooks.values()}) != len(workbooks):
            raise ValueError("已选场站不能选择同一个工作簿。")
        return workbooks

    def start_update(self) -> None:
        try:
            start_date = parse_date_input(self.start_date.get())
            end_date = parse_date_input(self.end_date.get())
        except ValueError:
            messagebox.showerror("日期格式错误", DATE_INPUT_ERROR, parent=self)
            return
        if end_date < start_date:
            messagebox.showerror("日期范围错误", "结束日期不能早于开始日期。", parent=self)
            return
        try:
            workbooks = self.selected_workbooks()
        except ValueError as exc:
            messagebox.showerror("场站设置错误", str(exc), parent=self)
            return

        self.clear_log()
        self.run_process(
            self.build_update_command(
                start_date,
                end_date,
                Path(self.guihu_workbook.get().strip()).expanduser(),
                Path(self.tuolian_workbook.get().strip()).expanduser(),
                Path(self.haifeng_workbook.get().strip()).expanduser(),
                overwrite=self.overwrite.get(),
                create_backup=self.create_backup.get(),
                selected_stations=tuple(workbooks),
            ),
            action='run', cwd=self.paths.spot_price_update_dir,
            status="正在抓取并更新现货价格工作簿...",
            cancellable=False,
            on_success=lambda _code: messagebox.showinfo(
                "完成",
                f"{'、'.join(workbooks)}处理完成，已写入数据通过回读验证。\n"
                "缺失或不足 24 点的数据已跳过，请查看日志。",
                parent=self,
            ),
        )

    def start_login(self) -> None:
        self.run_process(
            [python_executable(), str(self.paths.spot_price_update), "--login", "--headed"],
            action='login', cwd=self.paths.spot_price_update_dir,
            status="正在打开现货价格登录窗口...",
        )
