from __future__ import annotations

import re
import tkinter as tk
from toolbox_choices import SegmentedGroup

from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_dialogs import FileDialogState
from .page_support import add_path_row, open_directory
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage


def default_output_for_pdf(pdf_path: str | Path) -> Path:
    source = Path(pdf_path).expanduser()
    year = re.search(r"(20\d{2})", source.name)
    month_day = re.search(r"[\(（](\d{2})\.(\d{2})[\)）]", source.name)
    if year and month_day:
        suffix = f"{year.group(1)}{month_day.group(1)}{month_day.group(2)}"
    else:
        suffix = source.stem
    return source.parent / f"现货运行日报报价容量区间{suffix}.xlsx"


class DailyPdfParserPage(ToolPage):
    def __init__(
        self,
        parent: tk.Misc,
        paths: ToolPaths,
        registry: TaskRegistry,
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="广东运行日报解析上传",
            description="解析运行日报，按报告日期将源 PDF 和报价容量 Excel 上传到清能网站数据中心。",
        )
        self.paths = paths
        self.file_dialogs = FileDialogState(paths.file_dialog_settings)
        self.pdf_path = tk.StringVar(master=self)
        self.output_path = tk.StringVar(master=self)
        self.mode = tk.StringVar(master=self, value="both")

        self.run_button = ttk.Button(self.actions, text="解析并上传", command=self.start_parse)
        self.run_button.pack(side="left")
        self.open_button = ttk.Button(
            self.actions, text="打开输出目录", command=self.open_output_directory
        )
        self.open_button.pack(side="left", padx=(8, 0))

        settings = ttk.LabelFrame(self.content, text="文件与解析设置", padding=12)
        settings.grid(row=0, column=0, sticky="ew")
        pdf_button = add_path_row(
            settings,
            row=0,
            label="运行日报 PDF",
            variable=self.pdf_path,
            command=self.choose_pdf,
            button_text="选择 PDF",
        )
        output_button = add_path_row(
            settings,
            row=1,
            label="输出 Excel",
            variable=self.output_path,
            command=self.choose_output,
            button_text="选择位置",
        )
        ttk.Label(settings, text="执行功能").grid(row=2, column=0, sticky="w", pady=8)
        modes = SegmentedGroup(settings, variable=self.mode, choices=(("both", "解析并上传"), ("parse", "仅解析"), ("upload", "仅上传")))
        modes.grid(row=2, column=1, columnspan=2, sticky="w")
        mode_buttons = modes.buttons
        self.mode.trace_add("write", self._update_mode)

        notes = ttk.LabelFrame(self.content, text="输出说明", padding=10)
        notes.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            notes,
            text=(
                "从日前申报信息表读取汇总值，按报价分布图的颜色覆盖率估算燃煤、燃气、新能源容量。"
                "已针对矮柱和薄堆叠段优化；不足 3 像素的细小分段会在日志中提示，仍受原图分辨率限制。"
                "默认将源 PDF 上传至“现货交易日报”，Excel 上传至“报价容量数据”，两项均按 PDF 报告日期匹配。"
                "仅上传可重试已有文件；上传失败时保留本地解析结果。账号使用任务中心的清能网站配置。"
            ),
            wraplength=820,
        ).pack(anchor="w")

        self.register_busy_widgets(self.run_button, pdf_button, output_button, *mode_buttons)
        self.append_log("请选择广东运行日报 PDF。")

    def _update_mode(self, *_args) -> None:
        self.run_button.configure(text={"both": "解析并上传", "parse": "开始解析", "upload": "上传已有文件"}[self.mode.get()])

    def choose_pdf(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择广东运行日报 PDF",
            initialdir=str(
                self.file_dialogs.initial_directory("daily_pdf_input", self.pdf_path.get())
            ),
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            source = Path(selected)
            self.pdf_path.set(str(source))
            self.output_path.set(str(default_output_for_pdf(source)))
            self.file_dialogs.remember_file("daily_pdf_input", source)

    def choose_output(self) -> None:
        current = self.output_path.get().strip()
        selected = filedialog.asksaveasfilename(
            title="保存解析结果",
            initialdir=str(self.file_dialogs.initial_directory("daily_pdf_output", current)),
            initialfile=Path(current).name if current else "现货运行日报报价容量区间.xlsx",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            parent=self,
        )
        if selected:
            self.output_path.set(selected)
            self.file_dialogs.remember_file("daily_pdf_output", selected)

    def build_command(
        self,
        pdf_path: Path,
        output_path: Path,
    ) -> list[str]:
        command = [
            python_executable(),
            str(self.paths.daily_pdf_parser),
            str(pdf_path),
            "--output",
            str(output_path),
            "--mode",
            self.mode.get(),
        ]
        return command

    def start_parse(self) -> None:
        source = Path(self.pdf_path.get().strip()).expanduser()
        if not source.is_file() or source.suffix.lower() != ".pdf":
            messagebox.showerror("文件无效", "请选择有效的 PDF 文件。", parent=self)
            return

        output_text = self.output_path.get().strip()
        output = Path(output_text).expanduser() if output_text else default_output_for_pdf(source)
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        self.output_path.set(str(output))
        mode = self.mode.get()
        if mode == "upload" and not output.is_file():
            messagebox.showerror("文件无效", "仅上传需要选择已生成的报价容量 Excel。", parent=self)
            return
        if mode != "upload" and output.exists() and not messagebox.askyesno(
            "确认覆盖",
            f"输出文件已存在：\n{output}\n\n是否覆盖？",
            parent=self,
        ):
            return

        output.parent.mkdir(parents=True, exist_ok=True)
        self.clear_log()

        def on_success(_code: int) -> None:
            if not output.is_file():
                messagebox.showerror(
                    "输出失败",
                    f"解析进程已结束，但未找到输出文件：\n{output}",
                    parent=self,
                )
                return
            title = {"both": "解析并上传完成", "parse": "解析完成", "upload": "上传完成"}[mode]
            detail = "\n源 PDF 和报价容量 Excel 已上传并核对网站状态。" if mode != "parse" else ""
            messagebox.showinfo(title, f"本地 Excel：\n{output}{detail}", parent=self)

        self.run_process(
            self.build_command(source, output),
            cwd=self.paths.daily_pdf_parser_dir,
            status={"both": "正在解析运行日报并上传 PDF、Excel...", "parse": "正在解析运行日报并生成 Excel...", "upload": "正在上传源 PDF 和报价容量 Excel..."}[mode],
            on_success=on_success,
        )

    def open_output_directory(self) -> None:
        output_text = self.output_path.get().strip()
        output = Path(output_text).expanduser() if output_text else Path.home() / "Downloads"
        open_directory(output.parent if output.suffix else output)
