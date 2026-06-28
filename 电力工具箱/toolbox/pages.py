from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .runtime import (
    TaskRegistry,
    ToolPaths,
    load_module,
    node_executable,
    python_executable,
    utf8_environment,
)
from .widgets import ToolPage


def _open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def default_review_root() -> Path:
    return Path.home() / "Desktop" / "复盘"


def default_review_folder_for_date(today: date, review_root: Path | None = None) -> Path:
    root = review_root or default_review_root()
    target_day = today + timedelta(days=1)
    preferred = root / f"{target_day.month}-{target_day.day}"
    if preferred.is_dir():
        return preferred
    if root.is_dir():
        directories = [path for path in root.iterdir() if path.is_dir()]
        if directories:
            return max(directories, key=lambda path: path.stat().st_mtime)
    return preferred


def clearing_file_date(path: Path) -> date | None:
    name = path.name
    patterns = [
        r"(20\d{2})[.\-_/年](\d{1,2})[.\-_/月](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, name)
        if not match:
            continue
        year, month, day = (int(value) for value in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _add_path_row(
    parent: ttk.Frame,
    *,
    row: int,
    label: str,
    variable: tk.StringVar,
    command,
    button_text: str = "浏览",
) -> ttk.Button:
    ttk.Label(parent, text=label).grid(
        row=row, column=0, sticky="w", padx=(0, 8), pady=5
    )
    ttk.Entry(parent, textvariable=variable).grid(
        row=row, column=1, sticky="ew", pady=5
    )
    button = ttk.Button(parent, text=button_text, command=command)
    button.grid(row=row, column=2, padx=(8, 0), pady=5)
    parent.columnconfigure(1, weight=1)
    return button


class OnlineEnergyPage(ToolPage):
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
        ttk.Label(form, text="运行日期").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.run_date, width=18).grid(
            row=0, column=1, sticky="w", padx=(8, 14)
        )
        ttk.Label(form, text="格式：YYYY-MM-DD", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w"
        )

        actions = self.actions
        self.export_button = ttk.Button(
            actions, text="开始导出", command=self.start_export
        )
        self.export_button.pack(side="left")
        self.login_button = ttk.Button(
            actions, text="登录/刷新登录状态", command=self.start_login
        )
        self.login_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="打开输出目录",
            command=lambda: _open_directory(self.paths.online_energy_dir / "输出"),
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(self.export_button, self.login_button)
        self.append_log("请选择日期后开始导出。")

    def build_export_command(self, run_date: str) -> list[str]:
        return [python_executable(), str(self.paths.online_energy), run_date]

    def start_export(self) -> None:
        run_date = self.run_date.get().strip()
        try:
            datetime.strptime(run_date, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror(
                "日期格式错误", "请输入 YYYY-MM-DD 格式日期。", parent=self
            )
            return
        self.clear_log()
        self.run_process(
            self.build_export_command(run_date),
            cwd=self.paths.online_energy_dir,
            status="正在导出上网电量...",
            on_success=lambda _code: messagebox.showinfo(
                "完成", "上网电量导出已完成。", parent=self
            ),
        )

    def start_login(self) -> None:
        self.run_process(
            [python_executable(), str(self.paths.online_energy), "--login"],
            cwd=self.paths.online_energy_dir,
            status="正在打开登录窗口...",
        )


class TradeAnalysisPage(ToolPage):
    def __init__(
        self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="电力交易分析",
            description="选择一个或多个出清 Excel，批量生成 HTML 分析报告。",
        )
        self.paths = paths
        self.files: list[Path] = []
        self.output_dir = tk.StringVar(master=self, value=str(Path.home() / "Downloads"))

        toolbar = self.actions
        ttk.Button(toolbar, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(toolbar, text="自动匹配D和D-2", command=self.auto_match_files).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(toolbar, text="移除选中", command=self.remove_selected).pack(
            side="left", padx=(8, 0)
        )
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
        self.run_button = ttk.Button(
            actions, text="开始分析", command=self.start_analysis
        )
        self.run_button.pack(side="left")
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
        downloads = downloads_dir or (Path.home() / "Downloads")
        wanted_dates = [today, today - timedelta(days=2)]
        matches: list[Path] = []
        for wanted in wanted_dates:
            candidates = [
                path
                for path in downloads.glob("*.xlsx")
                if not path.name.startswith("~$")
                and "出清情况" in path.name
                and clearing_file_date(path) == wanted
            ]
            if candidates:
                matches.append(max(candidates, key=lambda path: path.stat().st_mtime))
        return matches

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
            initialdir=self.output_dir.get(),
            parent=self,
        )
        if selected:
            self.output_dir.set(selected)

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
            module = load_module("toolbox_trade_analysis", self.paths.trade_analysis)
            outputs: list[Path] = []
            for source in selected_files:
                output = output_dir / f"{source.stem}_分析报告.html"
                module.generate_report(source, out_path=output)
                outputs.append(output)
                self.append_log(f"完成：{source.name} -> {output.name}")
            return outputs

        self.run_in_thread(
            worker,
            status="正在生成分析报告...",
            on_success=lambda outputs: messagebox.showinfo(
                "完成", f"已生成 {len(outputs)} 个报告。", parent=self
            ),
        )


class SummaryPage(ToolPage):
    def __init__(
        self,
        parent: tk.Misc,
        paths: ToolPaths,
        registry: TaskRegistry,
        *,
        today: date | None = None,
        review_root: Path | None = None,
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="电量汇总",
            description="汇总日前和实时交易结果，并保留原始 Excel 的显示精度。",
        )
        self.paths = paths
        default_input = default_review_folder_for_date(today or date.today(), review_root)
        self.input_dir = tk.StringVar(master=self, value=str(default_input))
        self.output_file = tk.StringVar(
            master=self,
            value=str(self.default_output_for(default_input)) if default_input else "",
        )

        form = ttk.LabelFrame(self.content, text="汇总设置", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        input_button = _add_path_row(
            form,
            row=0,
            label="原始数据文件夹",
            variable=self.input_dir,
            command=self.choose_input_dir,
            button_text="选择文件夹",
        )
        output_button = _add_path_row(
            form,
            row=1,
            label="输出文件",
            variable=self.output_file,
            command=self.choose_output_file,
            button_text="选择位置",
        )

        actions = self.actions
        self.run_button = ttk.Button(
            actions, text="开始汇总", command=self.start_summary
        )
        self.run_button.pack(side="left")
        ttk.Button(
            actions, text="打开输出目录", command=self.open_output_dir
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(self.run_button, input_button, output_button)

    @staticmethod
    def default_output_for(input_dir: Path) -> Path:
        return input_dir / f"{input_dir.name}_汇总.xlsx"

    def choose_input_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择原始数据文件夹", parent=self)
        if selected:
            path = Path(selected)
            self.input_dir.set(str(path))
            self.output_file.set(str(self.default_output_for(path)))

    def choose_output_file(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
            parent=self,
        )
        if selected:
            self.output_file.set(selected)

    def open_output_dir(self) -> None:
        output = self.output_file.get().strip()
        if not output:
            messagebox.showinfo("提示", "还没有输出文件路径。", parent=self)
            return
        _open_directory(Path(output).parent)

    def start_summary(self) -> None:
        input_dir = Path(self.input_dir.get().strip())
        if not self.input_dir.get().strip() or not input_dir.is_dir():
            messagebox.showerror(
                "输入无效", "请选择有效的原始数据文件夹。", parent=self
            )
            return
        output = (
            Path(self.output_file.get().strip())
            if self.output_file.get().strip()
            else self.default_output_for(input_dir)
        )
        self.output_file.set(str(output))
        self.clear_log()

        def worker() -> Path:
            module = load_module("toolbox_summary", self.paths.summary)
            return module.run_summary(
                input_dir=input_dir,
                output_path=output,
                logger=self.append_log,
            )

        def finished(path: Path) -> None:
            self.output_file.set(str(path))
            messagebox.showinfo("完成", f"汇总文件已生成：\n{path}", parent=self)

        self.run_in_thread(worker, status="正在汇总...", on_success=finished)


class ReportPage(ToolPage):
    def __init__(
        self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="生成报告",
            description="抓取每日数据并根据三个 Excel 数据源生成生产经营情况 Word 报告。",
        )
        self.paths = paths
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
        ttk.Entry(form, textvariable=self.report_date, width=18).grid(
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
        ttk.Checkbutton(
            options, text="抓取网站数据并汇总 Excel", variable=self.fetch_data
        ).pack(side="left")
        ttk.Checkbutton(
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
            command=lambda: _open_directory(self.paths.report_root / "输出"),
        ).pack(side="left", padx=(8, 0))
        self.register_busy_widgets(
            self.run_button, auto_button, *browse_buttons
        )

    @staticmethod
    def source_dates(value: str) -> tuple[date, date, date]:
        report_day = datetime.strptime(value, "%Y-%m-%d").date()
        return report_day - timedelta(days=1), report_day, report_day - timedelta(days=2)

    def build_word_command(
        self,
        report_date: str,
        online: Path,
        day_ahead: Path,
        daily: Path,
        template: Path | None,
    ) -> list[str]:
        command = [
            python_executable(),
            str(self.paths.report_scripts_dir / "generate_red_marked_report.py"),
            report_date,
            "--online-workbook",
            str(online),
            "--day-ahead-workbook",
            str(day_ahead),
            "--daily-clearing-workbook",
            str(daily),
        ]
        if template is not None:
            command.extend(["--template", str(template)])
        return command

    @staticmethod
    def find_workbook_for_day(target_day: date) -> Path | None:
        downloads = Path.home() / "Downloads"
        date_token = f"{target_day.year}.{target_day.month}.{target_day.day}"
        candidates = [
            path
            for path in downloads.glob("*.xlsx")
            if not path.name.startswith("~$")
            and "出清情况" in path.name
            and date_token in path.name
        ]
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    def auto_fill(self) -> None:
        try:
            target_dates = self.source_dates(self.report_date.get().strip())
        except ValueError:
            messagebox.showerror(
                "日期格式错误", "请输入 YYYY-MM-DD 格式日期。", parent=self
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
            initialdir=str(Path.home() / "Downloads"),
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            variable.set(selected)

    def choose_template(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 Word 模板",
            initialdir=str(self.paths.report_root / "输出"),
            filetypes=[("Word 文件", "*.docx"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            self.template_report.set(selected)

    def _execute_command(self, command: list[str]) -> None:
        self.append_log(f"> {' '.join(command)}")
        process = subprocess.Popen(
            command,
            cwd=str(self.paths.report_scripts_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=utf8_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.registry.register_process(process)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self.append_log(line)
            code = process.wait()
        finally:
            self.registry.unregister_process(process)
        if code != 0:
            raise RuntimeError(f"命令退出码 {code}")

    def start_report(self) -> None:
        report_date = self.report_date.get().strip()
        try:
            self.source_dates(report_date)
        except ValueError:
            messagebox.showerror(
                "日期格式错误", "请输入 YYYY-MM-DD 格式日期。", parent=self
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

        def worker() -> int:
            for command in commands:
                self._execute_command(command)
            return len(commands)

        self.run_in_thread(
            worker,
            status="正在生成报告...",
            on_success=lambda count: messagebox.showinfo(
                "完成", f"已完成 {count} 个任务。", parent=self
            ),
        )


class PrivateUploadPage(ToolPage):
    def __init__(
        self,
        parent: tk.Misc,
        paths: ToolPaths,
        registry: TaskRegistry,
        *,
        today: date | None = None,
        review_root: Path | None = None,
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="私有数据上传",
            description="选择复盘目录，先预览匹配结果，再确认上传私有数据。",
        )
        self.paths = paths
        default = default_review_folder_for_date(today or date.today(), review_root)
        self.source_folder = tk.StringVar(master=self, value=str(default))

        form = ttk.LabelFrame(self.content, text="上传目录", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        browse = _add_path_row(
            form,
            row=0,
            label="复盘文件夹",
            variable=self.source_folder,
            command=self.choose_folder,
            button_text="选择文件夹",
        )
        actions = self.actions
        self.preview_button = ttk.Button(
            actions, text="预览", command=lambda: self.start_command("--plan")
        )
        self.preview_button.pack(side="left")
        self.upload_button = ttk.Button(
            actions, text="开始上传", command=self.confirm_upload
        )
        self.upload_button.pack(side="left", padx=(8, 0))
        self.register_busy_widgets(browse, self.preview_button, self.upload_button)
        self.append_log("操作流程：选择文件夹 -> 预览 -> 确认无误后开始上传。")

    def build_command(self, mode: str, source: Path) -> list[str]:
        return [
            node_executable(),
            str(self.paths.private_uploader),
            mode,
            "--source",
            str(source),
        ]

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="选择复盘文件夹",
            initialdir=self.source_folder.get(),
            parent=self,
        )
        if selected:
            self.source_folder.set(selected)

    def confirm_upload(self) -> None:
        folder = self.source_folder.get().strip()
        if not messagebox.askokcancel(
            "确认上传",
            f"确认上传此文件夹下匹配到的文件？\n{folder}",
            parent=self,
        ):
            return
        self.start_command("--execute")

    def start_command(self, mode: str) -> None:
        source = Path(self.source_folder.get().strip())
        if not source.is_dir():
            messagebox.showerror("目录无效", f"请选择有效文件夹：\n{source}", parent=self)
            return
        self.clear_log()
        self.run_process(
            self.build_command(mode, source),
            cwd=self.paths.private_uploader_dir,
            status="正在预览..." if mode == "--plan" else "正在上传...",
        )


class GroupUploadPage(ToolPage):
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
            description="上传能销和省内日报数据；只选择能销文件时沿用原脚本的省内模板自动生成规则。",
        )
        self.paths = paths
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
        self.choose_button = ttk.Button(
            actions, text="选择文件", command=self.choose_files
        )
        self.choose_button.pack(side="left")
        self.upload_button = ttk.Button(
            actions, text="开始上传", command=self.confirm_upload
        )
        self.upload_button.pack(side="left", padx=(8, 0))
        self.login_button = ttk.Button(
            actions, text="登录/刷新登录状态", command=self.start_login
        )
        self.login_button.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            actions, text="已上传数据也覆盖", variable=self.force
        ).pack(side="right")
        self.register_busy_widgets(
            self.choose_button, self.upload_button, self.login_button
        )
        self.auto_select_energy_file(today or date.today())

    @staticmethod
    def find_latest_energy_file_for_date(
        today: date,
        upload_dir: Path,
    ) -> Path | None:
        target_day = today - timedelta(days=2)
        candidates = [
            path
            for path in upload_dir.glob("*.xls*")
            if not path.name.startswith("~$")
            and "能销" in path.name
            and clearing_file_date(path) == target_day
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def apply_selected_paths(self, paths: list[Path]) -> None:
        try:
            module = load_module("toolbox_group_upload", self.paths.group_upload)
            target_date, uploads = module.prepare_upload_files(paths)
        except Exception as exc:
            self.selected_paths = paths
            self.selection_var.set("\n".join(path.name for path in paths))
            self.summary_var.set(f"文件校验失败：{exc}")
            raise
        self.selected_paths = paths
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
            initialdir=str(self.paths.group_upload_dir),
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
        self.run_process(
            self.build_upload_command(self.selected_paths, force=self.force.get()),
            cwd=self.paths.group_upload_dir,
            status="正在上传集团每日数据...",
        )

    def start_login(self) -> None:
        self.run_process(
            [python_executable(), str(self.paths.group_upload), "--login"],
            cwd=self.paths.group_upload_dir,
            status="正在打开登录窗口...",
        )


class WpsWriterPage(ToolPage):
    def __init__(
        self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="WPS Writer",
            description="Use the WPS/KDocs writer directly inside the toolbox.",
        )
        self.paths = paths
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        if not self.paths.wps_writer.is_file():
            self._show_load_error(f"Script file not found:\n{self.paths.wps_writer}")
            return

        try:
            module = load_module("toolbox_wps_writer_gui", self.paths.wps_writer)
            frame_class = getattr(module, "WpsWriterFrame")
            self.writer_frame = frame_class(self.content)
            self.writer_frame.grid(row=0, column=0, sticky="nsew")
        except Exception as exc:
            self._show_load_error(str(exc))

    def _show_load_error(self, message: str) -> None:
        panel = ttk.LabelFrame(self.content, text="WPS writer load failed", padding=12)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text=message, foreground="#b00020", wraplength=760).grid(
            row=0, column=0, sticky="ew"
        )
        actions = self.actions
        ttk.Button(
            actions,
            text="Open config folder",
            command=lambda: _open_directory(self.paths.wps_writer_dir),
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Open log folder",
            command=lambda: _open_directory(self.paths.wps_writer_logs_dir),
        ).pack(side="left", padx=(8, 0))

def page_factories() -> Mapping[str, object]:
    return {
        "导出上网电量": OnlineEnergyPage,
        "电力交易分析": TradeAnalysisPage,
        "电量汇总": SummaryPage,
        "生成报告": ReportPage,
        "私有数据上传": PrivateUploadPage,
        "上传集团每日数据": GroupUploadPage,
        "WPS写入工具": WpsWriterPage,
    }
