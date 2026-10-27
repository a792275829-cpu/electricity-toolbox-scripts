from __future__ import annotations

from . import commands, file_rules
from .upload_service import copy_file_with_finder_fallback, run_group_upload_with_staging

import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from toolbox_choices import OptionCheckbutton, SettingCheckbutton
from toolbox_file_button import FileSelectButton

from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from toolbox_storage import data_path
from .file_dialogs import FileDialogState
from .runtime import (
    TaskRegistry,
    ToolPaths,
    load_module,
    python_executable,
    utf8_environment,
)
from .widgets import ToolPage
from .tasks import CancellationToken, ProcessRunner
from .section_analysis_page import SectionAnalysisPage
from .daily_pdf_page import DailyPdfParserPage
from .wind_forecast_page import WindForecastPage
from .spot_price_page import SpotPriceUpdatePage
from .transaction_info_page import TransactionInfoUpdatePage
from .power_benefit_page import PowerBenefitUpdatePage
from .market_report_page import MarketReportPage
from .summary_upload_page import SummaryUploadPage
from .page_support import DateInput, DATE_INPUT_ERROR, DATE_INPUT_HINT, normalize_date_input, parse_date_input
from .page_support import (
    add_path_row as _add_path_row,
    clearing_file_date,
    default_review_folder_for_date,
    default_review_root,
    open_directory as _open_directory,
)


def _file_dialog_state(paths: ToolPaths) -> FileDialogState:
    """Keep lightweight test doubles compatible with the shared dialog state."""
    settings_path = getattr(paths, "file_dialog_settings", None)
    if settings_path is None:
        settings_path = Path(getattr(paths, "group_upload_dir", Path.cwd())) / "file_dialog_settings.json"
    return FileDialogState(settings_path)






class OnlineEnergyPage(ToolPage):
    tool_id = 'online-energy'
    def __init__(
        self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="导出上网电量",
            description="按日期抓取各公司、各机组上网电量并生成 Excel 汇总。",
        )
        self.paths = paths
        self.run_date = tk.StringVar(
            master=self, value=(date.today() - timedelta(days=1)).isoformat()
        )

        form = ttk.LabelFrame(self.content, text="导出设置", padding=14)
        form.grid(row=0, column=0, sticky="ew")
        ttk.Label(form, text="导出日期").grid(row=0, column=0, sticky="w")
        DateInput(form, textvariable=self.run_date, width=18).grid(
            row=0, column=1, sticky="w", padx=(8, 14)
        )
        ttk.Label(form, text=DATE_INPUT_HINT, style="Muted.TLabel").grid(
            row=0, column=2, sticky="w"
        )

        actions = self.actions
        self.export_button = ttk.Button(
            actions, text="开始导出", command=self.start_export
        )
        self.export_button.pack(side="left")
        ttk.Button(
            actions,
            text="打开输出目录",
            command=lambda: _open_directory(self.paths.online_energy_output_dir),
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(self.export_button)
        self.append_log("请选择日期后开始导出。")

    def build_export_command(self, run_date: str) -> list[str]:
        return commands.dated_script(self.paths.online_energy, run_date)

    def start_export(self) -> None:
        run_date = self.run_date.get().strip()
        try:
            run_date = normalize_date_input(run_date)
        except ValueError:
            messagebox.showerror(
                "日期格式错误", DATE_INPUT_ERROR, parent=self
            )
            return
        self.clear_log()
        self.run_process(
            self.build_export_command(run_date),
            action='run', cwd=self.paths.online_energy_dir,
            status="正在导出上网电量...",
            on_success=lambda _code: messagebox.showinfo(
                "完成", "上网电量导出已完成。", parent=self
            ),
        )

    def start_login(self) -> None:
        self.run_process(
            [python_executable(), str(self.paths.online_energy), "--login"],
            action='login', cwd=self.paths.online_energy_dir,
            status="正在打开登录窗口...",
        )


class TradeAnalysisPage(ToolPage):
    tool_id = 'trade-analysis'
    def __init__(
        self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="出力受限分析",
            description="选择一个或多个出清 Excel，批量生成 HTML 分析报告。",
        )
        self.paths = paths
        self.file_dialogs = _file_dialog_state(paths)
        self.files: list[Path] = []
        self.output_dir = tk.StringVar(master=self, value=str(Path.home() / "Downloads"))

        toolbar = self.actions
        FileSelectButton(toolbar, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(toolbar, text="自动匹配D和D-2", command=self.auto_match_files).pack(
            side="left", padx=(8, 0)
        )
        self.run_button = ttk.Button(
            toolbar, text="开始分析", command=self.start_analysis
        )
        self.run_button.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="清空列表", command=self.clear_files).pack(
            side="left", padx=(8, 0)
        )

        list_frame = ttk.Frame(self.content)
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 8))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, height=8)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.listbox.yview
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        output = ttk.Frame(self.content)
        output.grid(row=2, column=0, sticky="ew")
        browse = _add_path_row(
            output,
            row=0,
            label="输出目录",
            variable=self.output_dir,
            command=self.choose_output_dir,
            button_text="选择目录",
        )

        actions = self.actions
        ttk.Button(actions, text="移除选中", command=self.remove_selected).pack(side="left")
        ttk.Button(
            actions,
            text="打开输出目录",
            command=lambda: _open_directory(Path(self.output_dir.get())),
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(self.run_button, browse)

    @staticmethod
    def find_clearing_files_for_dates(
        today: date,
        downloads_dir: Path | None = None,
    ) -> list[Path]:
        return file_rules.find_clearing_files_for_dates(today, downloads_dir)

    def set_files(self, paths: list[Path]) -> None:
        self.files = list(paths)
        self.listbox.delete(0, "end")
        for path in self.files:
            self.listbox.insert("end", str(path))

    def auto_match_files(self) -> None:
        matches = self.find_clearing_files_for_dates(date.today())
        if not matches:
            messagebox.showwarning(
                "未找到文件",
                "Downloads 中未找到 D 和 D-2 的出清情况 Excel。",
                parent=self,
            )
            return
        self.set_files(matches)
        self.append_log("自动匹配文件：")
        for path in matches:
            self.append_log(str(path))

    def add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择出清 Excel 文件",
            initialdir=str(self.file_dialogs.initial_directory("trade_analysis_input")),
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            parent=self,
        )
        existing = {path.resolve() for path in self.files}
        for item in selected:
            path = Path(item)
            if path.resolve() not in existing:
                self.files.append(path)
                self.listbox.insert("end", str(path))
                existing.add(path.resolve())
        if selected:
            self.file_dialogs.remember_file("trade_analysis_input", selected[0])

    def remove_selected(self) -> None:
        for index in reversed(self.listbox.curselection()):
            del self.files[index]
            self.listbox.delete(index)

    def clear_files(self) -> None:
        self.files.clear()
        self.listbox.delete(0, "end")

    def choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="选择输出目录",
            initialdir=str(
                self.file_dialogs.initial_directory(
                    "trade_analysis_output", self.output_dir.get()
                )
            ),
            parent=self,
        )
        if selected:
            self.output_dir.set(selected)
            self.file_dialogs.remember_directory("trade_analysis_output", selected)

    def start_analysis(self) -> None:
        if not self.files:
            messagebox.showwarning(
                "缺少文件", "请先添加至少一个 Excel 文件。", parent=self
            )
            return
        output_dir = Path(self.output_dir.get().strip())
        output_dir.mkdir(parents=True, exist_ok=True)
        selected_files = list(self.files)
        self.clear_log()

        def worker() -> list[Path]:
            from toolbox_trade_report import batch_reports
            outputs: list[Path] = []
            for source, output, _, _ in batch_reports(selected_files, output_dir):
                outputs.append(output)
                self.append_log(f"完成：{source.name} -> {output.name}")
            return outputs

        self.run_in_thread(
            worker,
            action='run', status="正在生成分析报告...",
            on_success=lambda outputs: messagebox.showinfo(
                "完成", f"已生成 {len(outputs)} 个报告。", parent=self
            ),
        )


class ReportPage(ToolPage):
    tool_id = 'report'
    def __init__(
        self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="早报生成",
            description="抓取每日数据并根据三个 Excel 数据源生成生产经营情况 Word 报告。",
        )
        self.paths = paths
        self.file_dialogs = _file_dialog_state(paths)
        self.report_date = tk.StringVar(master=self, value=date.today().isoformat())
        self.online_workbook = tk.StringVar(master=self)
        self.day_ahead_workbook = tk.StringVar(master=self)
        self.daily_workbook = tk.StringVar(master=self)
        self.template_report = tk.StringVar(master=self)
        self.fetch_data = tk.BooleanVar(master=self, value=True)
        self.generate_word = tk.BooleanVar(master=self, value=True)

        form = ttk.Frame(self.content)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="报告日期").grid(row=0, column=0, sticky="w", pady=4)
        DateInput(form, textvariable=self.report_date, width=18).grid(
            row=0, column=1, sticky="w", pady=4
        )
        auto_button = ttk.Button(
            form, text="按日期自动匹配 Excel", command=self.auto_fill
        )
        auto_button.grid(row=0, column=2, padx=(8, 0), pady=4)
        browse_buttons = [
            _add_path_row(
                form,
                row=1,
                label="前一日 Excel（上网电量）",
                variable=self.online_workbook,
                command=lambda: self.choose_excel(self.online_workbook),
            ),
            _add_path_row(
                form,
                row=2,
                label="报告日 Excel（日前电量/价格）",
                variable=self.day_ahead_workbook,
                command=lambda: self.choose_excel(self.day_ahead_workbook),
            ),
            _add_path_row(
                form,
                row=3,
                label="前两日 Excel（日清数据）",
                variable=self.daily_workbook,
                command=lambda: self.choose_excel(self.daily_workbook),
            ),
            _add_path_row(
                form,
                row=4,
                label="Word 模板（可选）",
                variable=self.template_report,
                command=self.choose_template,
            ),
        ]

        options = ttk.Frame(self.content)
        options.grid(row=1, column=0, sticky="w", pady=(8, 0))
        OptionCheckbutton(
            options, text="抓取网站数据并汇总 Excel", variable=self.fetch_data
        ).pack(side="left")
        OptionCheckbutton(
            options, text="生成 Word 报告", variable=self.generate_word
        ).pack(side="left", padx=(16, 0))

        actions = self.actions
        self.run_button = ttk.Button(
            actions, text="开始生成", command=self.start_report
        )
        self.run_button.pack(side="left")
        ttk.Button(
            actions,
            text="打开输出目录",
            command=lambda: _open_directory(data_path(self.paths.report_root / "输出")),
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(
            self.run_button, auto_button, *browse_buttons
        )

    @staticmethod
    def source_dates(value: str) -> tuple[date, date, date]:
        report_day = parse_date_input(value)
        return report_day - timedelta(days=1), report_day, report_day - timedelta(days=2)

    def build_word_command(
        self,
        report_date: str,
        online: Path,
        day_ahead: Path,
        daily: Path,
        template: Path | None,
    ) -> list[str]:
        return commands.word_report(self.paths, report_date, online, day_ahead, daily, template)

    @staticmethod
    def find_workbook_for_day(target_day: date) -> Path | None:
        return file_rules.find_workbook_for_day(target_day)

    def auto_fill(self) -> None:
        try:
            target_dates = self.source_dates(self.report_date.get().strip())
        except ValueError:
            messagebox.showerror(
                "日期格式错误", DATE_INPUT_ERROR, parent=self
            )
            return
        variables = (
            self.online_workbook,
            self.day_ahead_workbook,
            self.daily_workbook,
        )
        missing: list[str] = []
        for variable, target_day in zip(variables, target_dates):
            workbook = self.find_workbook_for_day(target_day)
            if workbook is None:
                missing.append(target_day.isoformat())
            else:
                variable.set(str(workbook))
        if missing:
            messagebox.showwarning(
                "部分文件未找到",
                "Downloads 中未找到日期：" + "、".join(missing),
                parent=self,
            )

    def choose_excel(self, variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(
            title="选择 Excel 文件",
            initialdir=str(
                self.file_dialogs.initial_directory("report_excel", variable.get())
            ),
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            variable.set(selected)
            self.file_dialogs.remember_file("report_excel", selected)

    def choose_template(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 Word 模板",
            initialdir=str(
                self.file_dialogs.initial_directory(
                    "report_template", self.template_report.get()
                )
            ),
            filetypes=[("Word 文件", "*.docx"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            self.template_report.set(selected)
            self.file_dialogs.remember_file("report_template", selected)

    def _execute_command(self, command: list[str], token: CancellationToken) -> None:
        self.append_log(f"> {' '.join(command)}")
        ProcessRunner().run(
            command,
            cwd=self.paths.report_scripts_dir,
            env=utf8_environment(),
            token=token,
            on_output=self.append_log,
        )

    def start_report(self) -> None:
        report_date = self.report_date.get().strip()
        try:
            report_date = normalize_date_input(report_date)
        except ValueError:
            messagebox.showerror(
                "日期格式错误", DATE_INPUT_ERROR, parent=self
            )
            return
        if not self.fetch_data.get() and not self.generate_word.get():
            messagebox.showerror("未选择任务", "请至少选择一个任务。", parent=self)
            return

        commands: list[list[str]] = []
        if self.fetch_data.get():
            commands.append(
                [
                    python_executable(),
                    str(self.paths.report_scripts_dir / "fetch_daily_data.py"),
                    report_date,
                ]
            )
        if self.generate_word.get():
            raw_paths = [
                self.online_workbook.get().strip(),
                self.day_ahead_workbook.get().strip(),
                self.daily_workbook.get().strip(),
            ]
            if any(not item for item in raw_paths):
                messagebox.showerror(
                    "缺少数据源", "生成 Word 报告需要选择三个 Excel 文件。", parent=self
                )
                return
            source_paths = [Path(item) for item in raw_paths]
            missing = [path for path in source_paths if not path.is_file()]
            template_text = self.template_report.get().strip()
            template = Path(template_text) if template_text else None
            if template is not None and not template.is_file():
                missing.append(template)
            if missing:
                messagebox.showerror(
                    "文件不存在",
                    "\n".join(str(path) for path in missing),
                    parent=self,
                )
                return
            commands.append(
                self.build_word_command(
                    report_date,
                    source_paths[0],
                    source_paths[1],
                    source_paths[2],
                    template,
                )
            )
        self.clear_log()

        def worker(token: CancellationToken) -> int:
            for command in commands:
                token.raise_if_cancelled()
                self._execute_command(command, token)
            return len(commands)

        self.run_in_thread(
            worker,
            action='run', status="正在生成报告...",
            on_success=lambda count: messagebox.showinfo(
                "完成", f"已完成 {count} 个任务。", parent=self
            ),
        )


class GroupUploadPage(ToolPage):
    tool_id = 'group-upload'
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
            title="上传集团每日数据",
            description="上传能销和省内日报数据；上传完成后自动新建集团日报。",
        )
        self.paths = paths
        self.file_dialogs = _file_dialog_state(paths)
        self.selected_paths: list[Path] = []
        self.selection_var = tk.StringVar(master=self, value="未选择文件")
        self.summary_var = tk.StringVar(
            master=self, value="请选择能销数据，或同时选择省内和能销数据。"
        )
        self.force = tk.BooleanVar(master=self, value=False)

        panel = ttk.LabelFrame(self.content, text="待上传文件", padding=12)
        panel.grid(row=0, column=0, sticky="ew")
        ttk.Label(panel, textvariable=self.summary_var).pack(anchor="w")
        ttk.Label(
            panel,
            textvariable=self.selection_var,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

        actions = self.actions
        self.choose_button = FileSelectButton(
            actions, text="选择文件", command=self.choose_files
        )
        self.choose_button.pack(side="left")
        self.upload_button = ttk.Button(
            actions, text="开始上传", command=self.confirm_upload
        )
        self.upload_button.pack(side="left", padx=(8, 0))
        SettingCheckbutton(
            actions, text="已上传数据也覆盖", variable=self.force
        ).pack(side="right")
        self.register_busy_widgets(
            self.choose_button, self.upload_button
        )
        self.auto_select_energy_file(today or date.today())

    @staticmethod
    def find_latest_energy_file_for_date(
        today: date,
        upload_dir: Path,
    ) -> Path | None:
        return file_rules.find_latest_energy_file_for_date(today, upload_dir)

    def apply_selected_paths(self, paths: list[Path]) -> None:
        try:
            module = load_module("toolbox_group_upload", self.paths.group_upload)
            target_date, uploads = module.prepare_upload_files(paths)
        except Exception as exc:
            self.selected_paths = paths
            self.selection_var.set("\n".join(path.name for path in paths))
            self.summary_var.set(f"文件校验失败：{exc}")
            raise
        self.selected_paths = [item.path for item in uploads]
        self.selection_var.set(
            "\n".join(
                f"{module.UPLOAD_TYPES[item.kind]['name']}：{item.path.name}"
                for item in uploads
            )
        )
        self.summary_var.set(f"已识别日期：{target_date}；目标省份：广东省")

    def auto_select_energy_file(self, today: date) -> None:
        match = self.find_latest_energy_file_for_date(today, self.paths.group_upload_dir)
        if match is None:
            return
        try:
            self.apply_selected_paths([match])
        except Exception as exc:
            self.append_log(f"自动匹配能销文件失败：{exc}")
            return
        self.append_log(f"已自动匹配D-2能销文件：{match}")

    def build_upload_command(
        self, paths: list[Path], *, force: bool
    ) -> list[str]:
        command = [
            python_executable(),
            str(self.paths.group_upload),
            *(str(path) for path in paths),
        ]
        if force:
            command.append("--force")
        return command

    def choose_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择能销数据；也可同时选择省内数据",
            initialdir=str(
                self.file_dialogs.initial_directory("group_upload_input")
            ),
            filetypes=[("Excel 文件", "*.xlsx *.xls"), ("所有文件", "*.*")],
            parent=self,
        )
        if not selected:
            return
        paths = [Path(item) for item in selected]
        try:
            self.apply_selected_paths(paths)
        except Exception as exc:
            messagebox.showerror("文件校验失败", str(exc), parent=self)
            return
        self.file_dialogs.remember_file("group_upload_input", selected[0])

    def confirm_upload(self) -> None:
        if not self.selected_paths:
            messagebox.showwarning("缺少文件", "请先选择上传文件。", parent=self)
            return
        if not messagebox.askokcancel(
            "确认上传",
            "确认上传当前选择的数据文件？\n\n" + "\n".join(
                path.name for path in self.selected_paths
            ),
            parent=self,
        ):
            return
        self.clear_log()
        selected_paths = list(self.selected_paths)
        force = self.force.get()

        def upload() -> None:
            module = load_module("toolbox_group_upload", self.paths.group_upload)
            old_sink = module.LOG_SINK
            module.LOG_SINK = self.append_log
            try:
                run_group_upload_with_staging(
                    module,
                    selected_paths,
                    force=force,
                    on_log=self.append_log,
                )
            finally:
                module.LOG_SINK = old_sink

        self.run_in_thread(
            upload,
            action='run', status="正在上传集团每日数据...",
            cancellable=False,
        )

    def start_login(self) -> None:
        self.run_process(
            [python_executable(), str(self.paths.group_upload), "--login"],
            action='login', cwd=self.paths.group_upload_dir,
            status="正在打开登录窗口...",
        )


class MarketTableUpdatePage(ToolPage):
    tool_id = 'market-table'
    MAPPING_DESCRIPTIONS = (
        "现货运行日报 > 市场负荷信息 > 日前：来源日期为基准日期 D；写入 2026年市场情况 工作表对应日期行 B:U。",
        "现货运行日报 > 日前市场 > 发电侧申报均价起 12 项：来源日期为基准日期 D-1；写入 2026年市场情况 工作表对应日期行 V:AG。",
        "现货运行日报 > 市场负荷信息 > 实际：来源日期为基准日期 D-2；写入 2026年市场情况 工作表对应日期行 AH:BA。",
        "现货运行日报 > 实时市场 > 发电侧成交电量、发电侧加权均价、燃煤平均成交电价、燃气平均成交电价：来源日期为基准日期 D-2；写入 2026年市场情况 工作表对应日期行 BB:BE。",
        "现货运行日报 > 发电侧结算 > 12 项：来源日期为基准日期 D-6；写入 2026年市场情况 工作表对应日期行 BF:BQ。",
        "现货运行日报 > 用电侧结算 > 7 项：来源日期为基准日期 D-6；写入 2026年市场情况 工作表对应日期行 BR:BX。",
        "现货基础信息报送 > 机组成本信息 > 单位变动成本对应电价：来源日期为基准日期 D；写入 2026年运行方式及成本 工作表对应日期行 N:X。",
        "省内现货交易 > 市场行情看板 > 在线机组数、省内机组预测检修容量：来源日期为基准日期 D；写入 2026年运行方式及成本 工作表对应日期行 AA、AE。",
        "公有数据管理 > 现货分时分类型出清电量 > 日前：燃煤取 23:00 开机台数，燃气取全天开机台数最大值；来源日期为基准日期 D；写入 2026年运行方式及成本 工作表对应日期行 AB:AC。",
        "2026年运行方式及成本 > 机组运行方式：来源日期为基准日期 D-1；复制 B:M 到基准日期 D 对应日期行 B:M。",
    )

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
            title="市场表更新",
            description="按一个基准运行日期抓取网页数据并写入指定市场 Excel。",
        )
        self.paths = paths
        self.file_dialogs = _file_dialog_state(paths)
        self.run_date = tk.StringVar(
            master=self, value=(today or date.today()).isoformat()
        )
        self.workbook = tk.StringVar(
            master=self,
            value=str(
                Path.home()
                / "Desktop"
                / "日常工作"
                / "现货统调市场出清、结算数据2026.xlsx"
            ),
        )

        form = ttk.LabelFrame(self.content, text="更新设置", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="基准运行日期").grid(row=0, column=0, sticky="w", pady=5)
        DateInput(form, textvariable=self.run_date, width=18).grid(
            row=0, column=1, sticky="w", pady=5
        )
        ttk.Label(form, text=DATE_INPUT_HINT, style="Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=5
        )
        browse = _add_path_row(
            form,
            row=1,
            label="市场 Excel",
            variable=self.workbook,
            command=self.choose_workbook,
            button_text="选择文件",
        )

        self.content.rowconfigure(1, weight=1)
        mapping = ttk.LabelFrame(self.content, text="当前已实现映射", padding=8)
        mapping.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        mapping.columnconfigure(0, weight=1)
        mapping.rowconfigure(0, weight=1)

        self.mapping_text = tk.Text(
            mapping,
            height=8,
            wrap="word",
            font="TkTextFont",
            borderwidth=0,
            padx=4,
            pady=4,
        )
        self.mapping_text.grid(row=0, column=0, sticky="nsew")
        self.mapping_scrollbar = ttk.Scrollbar(
            mapping, orient="vertical", command=self.mapping_text.yview
        )
        self.mapping_scrollbar.grid(row=0, column=1, sticky="ns")
        self.mapping_text.configure(yscrollcommand=self.mapping_scrollbar.set)
        for index, description in enumerate(self.MAPPING_DESCRIPTIONS, start=1):
            self.mapping_text.insert("end", f"{index}. {description}\n")
        self.mapping_text.configure(state="disabled")

        actions = self.actions
        self.update_button = ttk.Button(
            actions, text="开始更新", command=self.start_update
        )
        self.update_button.pack(side="left")
        self.register_busy_widgets(browse, self.update_button)
        self.append_log("请选择日期和市场 Excel 后开始更新。")

    def build_update_command(self, run_date: date, workbook: Path) -> list[str]:
        return commands.market_table(self.paths, run_date, workbook)

    def choose_workbook(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择市场 Excel",
            initialdir=str(
                self.file_dialogs.initial_directory("market_workbook", self.workbook.get())
            ),
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            self.workbook.set(selected)
            self.file_dialogs.remember_file("market_workbook", selected)

    def start_update(self) -> None:
        try:
            run_date = parse_date_input(self.run_date.get())
        except ValueError:
            messagebox.showerror(
                "日期格式错误", DATE_INPUT_ERROR, parent=self
            )
            return
        workbook = Path(self.workbook.get().strip()).expanduser()
        if not workbook.is_file():
            messagebox.showerror(
                "文件无效", f"请选择有效 Excel 文件：\n{workbook}", parent=self
            )
            return
        self.clear_log()
        self.run_process(
            self.build_update_command(run_date, workbook),
            action='run', cwd=self.paths.market_table_update_dir,
            status="正在更新市场表...",
            cancellable=False,
            on_success=lambda _code: messagebox.showinfo(
                "完成", "市场表更新完成。", parent=self
            ),
        )

    def start_login(self) -> None:
        self.run_process(
            [
                python_executable(),
                str(self.paths.market_table_update),
                "--login",
                "--headed",
            ],
            action='login', cwd=self.paths.market_table_update_dir,
            status="正在打开登录窗口...",
        )


class GuangdongPricePage(ToolPage):
    tool_id = 'guangdong-price'
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
            title="广东电价预测",
            description="登录华能广东现货系统，按 96 个十五分钟时点更新数据，并运行 D+1 日前电价预测。",
        )
        self.paths = paths
        self.file_dialogs = _file_dialog_state(paths)
        current = today or date.today()
        self.run_date = tk.StringVar(master=self, value=current.isoformat())
        self.master_workbook = tk.StringVar(
            master=self,
            value=str(paths.guangdong_price_master_workbook),
        )
        self.prediction_input = self.master_workbook
        self.prediction_output_dir = tk.StringVar(
            master=self, value=str(paths.guangdong_prediction_output_dir)
        )
        self.training_months = tk.IntVar(master=self, value=0)

        form = ttk.LabelFrame(self.content, text="1. 补齐历史数据总表", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="运行日 D").grid(row=0, column=0, sticky="w", pady=4)
        DateInput(form, textvariable=self.run_date, width=16).grid(
            row=0, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text=DATE_INPUT_HINT, style="Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=4
        )
        master_button = _add_path_row(
            form,
            row=1,
            label="数据总表",
            variable=self.master_workbook,
            command=lambda: self.open_business_directory(self.master_workbook.get(), workbook=True),
            button_text="打开目录",
        )
        ttk.Label(
            form,
            text="自动检查并补齐截至 D 日缺少 96 个时点的日期；账号密码读取本地配置。",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))

        prediction = ttk.LabelFrame(self.content, text="2. 使用总表预测 D+1", padding=12)
        prediction.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        prediction.columnconfigure(1, weight=1)
        ttk.Label(prediction, text="建模输入表").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Label(prediction, textvariable=self.prediction_input).grid(
            row=0, column=1, columnspan=4, sticky="w", pady=4
        )
        prediction_dir_button = _add_path_row(
            prediction,
            row=1,
            label="预测输出目录",
            variable=self.prediction_output_dir,
            command=self.choose_prediction_output_dir,
            button_text="选择目录",
        )
        ttk.Label(prediction, text="滚动训练月数").grid(
            row=3, column=0, sticky="w", pady=4
        )
        ttk.Spinbox(
            prediction, from_=0, to=24, textvariable=self.training_months, width=8
        ).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(
            prediction,
            text="0 为自动：当日供需齐全用 12 个月，否则用 3 个月历史信息回退。",
            style="Muted.TLabel",
        ).grid(row=3, column=2, columnspan=3, sticky="w", padx=(8, 0), pady=4)
        ttk.Label(
            prediction,
            text="自动核验预测日供需发布时间；固定输出 D+1 的 96 点电价。",
            style="Muted.TLabel",
        ).grid(row=2, column=2, columnspan=3, sticky="w", padx=(8, 0), pady=4)

        self.update_button = ttk.Button(
            self.actions, text="补齐数据总表", command=self.start_update
        )
        self.update_button.pack(side="left")
        self.predict_button = ttk.Button(
            self.actions, text="运行 D+1 预测", command=self.start_prediction
        )
        self.predict_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            self.actions,
            text="打开预测目录",
            command=lambda: self.open_business_directory(self.prediction_output_dir.get()),
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(
            self.update_button,
            self.predict_button,
            master_button,
            prediction_dir_button,
        )
        self.append_log("先补齐总表，再运行 D+1 预测；账号使用右侧统一账号。")

    def resolve_business_path(self, text: str) -> Path:
        if not text.strip():
            raise ValueError("路径不能为空")
        path = Path(text.strip()).expanduser()
        if not path.is_absolute():
            path = self.paths.guangdong_price_dir / path
        return path.resolve()

    def open_business_directory(self, text: str, *, workbook: bool = False) -> None:
        try:
            path = self.resolve_business_path(text)
            _open_directory(path.parent if workbook else path)
        except (ValueError, OSError) as exc:
            messagebox.showerror("目录无效", str(exc), parent=self)

    @staticmethod
    def validate_date_range(start_text: str, end_text: str) -> tuple[date, date]:
        start = parse_date_input(start_text)
        end = parse_date_input(end_text)
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
        return start, end

    def choose_prediction_output_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="选择预测输出目录",
            initialdir=str(
                self.file_dialogs.initial_directory(
                    "guangdong_prediction_output", self.prediction_output_dir.get()
                )
            ),
            parent=self,
        )
        if selected:
            self.prediction_output_dir.set(selected)
            self.file_dialogs.remember_directory("guangdong_prediction_output", selected)

    def build_prediction_command(
        self,
        input_path: Path,
        output_dir: Path,
        forecast_days: int,
        training_months: int | None = None,
    ) -> list[str]:
        months = training_months if training_months is not None else self.training_months.get()
        return commands.prediction(self.paths, input_path, output_dir, forecast_days, months, normalize_date_input(self.run_date.get()))

    def start_update(self) -> None:
        try:
            run_date = normalize_date_input(self.run_date.get())
            master_text = self.master_workbook.get().strip()
            if not master_text:
                raise ValueError("数据总表路径不能为空")
            master_path = self.resolve_business_path(master_text)
            if master_path.suffix.lower() != ".xlsx":
                raise ValueError("数据总表必须是 .xlsx 文件")
        except ValueError as exc:
            messagebox.showerror("参数无效", str(exc), parent=self)
            return
        self.clear_log()
        self.append_log(f"检查截至 {run_date} 的空缺数据")

        def worker() -> dict[str, object]:
            module = load_module("toolbox_guangdong_data_update", self.paths.guangdong_data_update)
            return module.update_master_workbook(run_date=run_date, master_workbook=master_path)

        def finished(outputs: dict[str, Path]) -> None:
            self.master_workbook.set(str(outputs["master"]))
            for name, path in outputs.items():
                self.append_log(f"{name}：{path}")
            messagebox.showinfo(
                "更新完成",
                f"数据总表已就绪，也是默认建模输入表：\n{outputs['master']}",
                parent=self,
            )

        self.run_in_thread(
            worker,
            action='update', status="正在抓取并合并广东现货数据...",
            on_success=finished,
            cancellable=False,
        )

    def start_prediction(self) -> None:
        try:
            normalize_date_input(self.run_date.get())
            input_text = self.prediction_input.get().strip()
            output_text = self.prediction_output_dir.get().strip()
            if not input_text or not output_text:
                raise ValueError("数据总表与预测输出目录不能为空")
            input_path = self.resolve_business_path(input_text)
            output_dir = self.resolve_business_path(output_text)
            training_months = self.training_months.get()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("参数无效", str(exc), parent=self)
            return
        if not input_path.is_file():
            messagebox.showerror("文件不存在", "请选择有效的建模输入表。", parent=self)
            return
        if training_months != 0 and training_months < 2:
            messagebox.showerror("训练窗口无效", "填 0 自动选择，或填写至少 2 个月。", parent=self)
            return
        self.clear_log()
        self.run_process(
            self.build_prediction_command(
                input_path, output_dir, 1, training_months
            ),
            action='predict', cwd=self.paths.guangdong_price_dir,
            status="正在训练并生成 D+1 日前电价预测...",
            on_success=lambda _code: messagebox.showinfo(
                "预测完成", f"预测结果已保存到：\n{output_dir}", parent=self
            ),
        )


class WpsWriterPage(ToolPage):
    tool_id = "wps-writer"
    def __init__(
        self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="WPS 写入工具",
            description="将本地或在线表格中的指定区域写入金山文档。",
        )
        self.paths = paths
        # WpsWriterFrame owns its browser/write lifecycle and its own module log.
        # The generic ToolPage task console is unused here and would show a
        # second, misleading empty log and cancel button.
        self.set_task_console_visible(False)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.actions.grid_remove()
        self._writer_load_after_id: str | None = None
        self._writer_load_queue: queue.Queue = queue.Queue()
        self._navigation_paused = False
        self._writer_load_done = False

        if not self.paths.wps_writer.is_file():
            self._show_load_error(f"找不到写入工具脚本：\n{self.paths.wps_writer}")
            return

        self.loading_label = ttk.Label(self.content, text="正在加载 WPS 写入工具…", style="Muted.TLabel")
        self.loading_label.grid(row=0, column=0, sticky="nw", pady=12)
        # Importing openpyxl/Playwright may take hundreds of milliseconds on a
        # cold start. Only import Python code in the worker; all Tk operations,
        # including creating WpsWriterFrame, stay on the main thread.
        result_queue = self._writer_load_queue
        script = self.paths.wps_writer

        def load_writer() -> None:
            try:
                module = load_module("toolbox_wps_writer_gui", script)
                result_queue.put((module.WpsWriterFrame, None))
            except Exception as exc:
                result_queue.put((None, exc))

        self._writer_loader = threading.Thread(target=load_writer, name="wps-page-loader", daemon=True)
        # Return to Tk so the shell/loading label can paint before imports start.
        # after_idle alone runs among Tk's own redraw handlers; use a short timer.
        self._writer_load_after_id = self.after(50, self._start_writer_load)

    def _start_writer_load(self) -> None:
        if self._writer_load_after_id is not None:
            self.after_cancel(self._writer_load_after_id)
        self._writer_load_after_id = None
        if self._navigation_paused or self._writer_load_done:
            return
        if self._writer_loader.ident is None:
            self._writer_loader.start()
        self._writer_load_after_id = self.after(20, self._finish_writer_load)

    def on_navigation_hidden(self) -> None:
        self._navigation_paused = True
        if self._writer_load_after_id is not None:
            self.after_cancel(self._writer_load_after_id)
            self._writer_load_after_id = None

    def on_navigation_shown(self) -> None:
        self._navigation_paused = False
        if hasattr(self, "writer_frame") and not self.writer_frame.winfo_manager():
            self.writer_frame.grid()
        if not self._writer_load_done and self._writer_load_after_id is None:
            self._writer_load_after_id = self.after(20, self._start_writer_load)

    def _finish_writer_load(self) -> None:
        if self._writer_load_after_id is not None:
            self.after_cancel(self._writer_load_after_id)
        self._writer_load_after_id = None
        if self._navigation_paused or self._writer_load_done:
            return
        try:
            frame_class, error = self._writer_load_queue.get_nowait()
        except queue.Empty:
            self._writer_load_after_id = self.after(20, self._finish_writer_load)
            return
        self.loading_label.destroy()
        self._writer_load_done = True
        if error is not None:
            self._show_load_error(str(error))
            return
        try:
            self.writer_frame = frame_class(self.content, task_registry=self.registry)
            self.writer_frame.grid(row=0, column=0, sticky="nsew")
            if self._navigation_paused:
                self.writer_frame.grid_remove()
        except Exception as exc:
            self._show_load_error(str(exc))

    def destroy(self) -> None:
        if self._writer_load_after_id is not None:
            self.after_cancel(self._writer_load_after_id)
            self._writer_load_after_id = None
        super().destroy()

    def _show_load_error(self, message: str) -> None:
        self._writer_load_done = True
        self.actions.grid()
        panel = ttk.LabelFrame(self.content, text="写入工具加载失败", padding=12)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text=message, foreground="#b00020", wraplength=760).grid(
            row=0, column=0, sticky="ew"
        )
        actions = self.actions
        ttk.Button(
            actions,
            text="打开配置目录",
            command=lambda: _open_directory(self.paths.wps_writer_dir),
        ).pack(side="left")
        ttk.Button(
            actions,
            text="打开日志目录",
            command=lambda: _open_directory(self.paths.wps_writer_logs_dir),
        ).pack(side="left", padx=(8, 0))

def page_factories() -> Mapping[str, object]:
    return {
        "导出上网电量": OnlineEnergyPage,
        "风力预测截图": WindForecastPage,
        "出力受限分析": TradeAnalysisPage,
        "500kV断面分析": SectionAnalysisPage,
        "广东运行日报解析上传": DailyPdfParserPage,
        "南方分公司电力市场日报": MarketReportPage,
        "出清结果汇总上传": SummaryUploadPage,
        "早报生成": ReportPage,
        "上传集团每日数据": GroupUploadPage,
        "市场表更新": MarketTableUpdatePage,
        "现货价格分析表更新": SpotPriceUpdatePage,
        "交易系统信息更新": TransactionInfoUpdatePage,
        "功率调整收益统计表更新": PowerBenefitUpdatePage,
        "广东电价预测": GuangdongPricePage,
        "WPS写入工具": WpsWriterPage,
    }
