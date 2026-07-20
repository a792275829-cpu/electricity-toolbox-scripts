from __future__ import annotations

import os
import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_dialogs import FileDialogState
from .page_support import add_path_row, open_directory
from .runtime import TaskRegistry, ToolPaths, python_executable
from .widgets import ToolPage


def open_path(path: Path) -> None:
    path = Path(path)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def result_folder_name(source: Path) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", Path(source).stem).strip()
    return cleaned or "500kV断面分析结果"


def result_html_name(source: Path) -> str:
    return f"{Path(source).stem}_500kV分析.html"


class SectionAnalysisPage(ToolPage):
    def __init__(
        self,
        parent: tk.Misc,
        paths: ToolPaths,
        registry: TaskRegistry,
    ) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="500kV断面分析",
            description="批量清洗节点电价，按500kV拓扑识别价格分化断面并生成交互网页。",
        )
        self.paths = paths
        self.file_dialogs = FileDialogState(paths.file_dialog_settings)
        self.files: list[Path] = []
        self.output_root = tk.StringVar(
            master=self,
            value=str(paths.section_analysis_dir / "result"),
        )
        self.topology_path = tk.StringVar(
            master=self,
            value=str(paths.section_analysis_topology),
        )
        self.threshold = tk.StringVar(master=self, value="100")
        self.latest_html: Path | None = None

        self.add_button = ttk.Button(self.actions, text="添加电价文件", command=self.add_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(self.actions, text="移除选中", command=self.remove_selected)
        self.remove_button.pack(side="left", padx=(8, 0))
        self.clear_button = ttk.Button(self.actions, text="清空列表", command=self.clear_files)
        self.clear_button.pack(side="left", padx=(8, 0))
        self.run_button = ttk.Button(self.actions, text="开始分析", command=self.start_analysis)
        self.run_button.pack(side="left", padx=(16, 0))

        files_frame = ttk.LabelFrame(self.content, text="节点电价文件（支持批量）", padding=10)
        files_frame.grid(row=0, column=0, sticky="nsew")
        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(files_frame, selectmode=tk.EXTENDED, height=6)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(files_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        settings = ttk.LabelFrame(self.content, text="分析设置", padding=12)
        settings.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        topology_button = add_path_row(
            settings,
            row=0,
            label="拓扑名单",
            variable=self.topology_path,
            command=self.choose_topology,
            button_text="选择文件",
        )
        output_button = add_path_row(
            settings,
            row=1,
            label="结果目录",
            variable=self.output_root,
            command=self.choose_output_root,
            button_text="选择目录",
        )
        ttk.Label(settings, text="价差阈值").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.threshold, width=12).grid(
            row=2, column=1, sticky="w", pady=5
        )
        ttk.Label(settings, text="元/MWh（严格大于）", style="Muted.TLabel").grid(
            row=2, column=1, sticky="w", padx=(105, 0), pady=5
        )
        result_actions = ttk.Frame(settings)
        result_actions.grid(row=3, column=1, sticky="w", pady=(10, 0))
        ttk.Button(
            result_actions,
            text="打开结果目录",
            command=lambda: open_directory(Path(self.output_root.get()).expanduser()),
        ).pack(side="left")
        self.open_latest_button = ttk.Button(
            result_actions,
            text="打开最近网页",
            command=self.open_latest,
            state="disabled",
        )
        self.open_latest_button.pack(side="left", padx=(8, 0))

        rules = ttk.LabelFrame(self.content, text="当前判定规则", padding=10)
        rules.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.rule_summary = tk.StringVar(master=self)
        self._sync_rule_summary()
        self.threshold.trace_add("write", self._sync_rule_summary)
        ttk.Label(
            rules,
            textvariable=self.rule_summary,
        ).pack(anchor="w")
        ttk.Label(
            rules,
            text="网页颜色由累计触发时点和最大价差共同决定；颜色越深代表越持久、价差越大。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        self.register_busy_widgets(
            self.add_button,
            self.remove_button,
            self.clear_button,
            self.run_button,
            topology_button,
            output_button,
        )
        self.append_log("请添加日前或实时节点电价 Excel 后开始分析。")

    def _sync_rule_summary(self, *_args) -> None:
        threshold = self.threshold.get().strip() or "100"
        self.rule_summary.set(
            f"相邻节点一涨一跌  ＋  价差 > {threshold}元/MWh  ＋  连续至少4个时点"
        )

    def set_files(self, paths: list[Path]) -> None:
        self.files = list(paths)
        self.listbox.delete(0, "end")
        for path in self.files:
            self.listbox.insert("end", str(path))

    def add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择节点电价 Excel",
            initialdir=str(self.file_dialogs.initial_directory("section_analysis_input")),
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
            self.file_dialogs.remember_file("section_analysis_input", selected[0])

    def remove_selected(self) -> None:
        for index in reversed(self.listbox.curselection()):
            del self.files[index]
            self.listbox.delete(index)

    def clear_files(self) -> None:
        self.files.clear()
        self.listbox.delete(0, "end")

    def choose_topology(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择500kV拓扑 Markdown",
            initialdir=str(Path(self.topology_path.get()).expanduser().parent),
            filetypes=[("Markdown 文件", "*.md"), ("所有文件", "*.*")],
            parent=self,
        )
        if selected:
            self.topology_path.set(selected)

    def choose_output_root(self) -> None:
        selected = filedialog.askdirectory(
            title="选择结果根目录",
            initialdir=str(
                self.file_dialogs.initial_directory(
                    "section_analysis_output",
                    self.output_root.get(),
                )
            ),
            parent=self,
        )
        if selected:
            self.output_root.set(selected)
            self.file_dialogs.remember_directory("section_analysis_output", selected)

    def build_command(
        self,
        files: list[Path],
        output_root: Path,
        topology_path: Path,
        threshold: float,
    ) -> list[str]:
        command = [
            python_executable(),
            str(self.paths.section_analysis_runner),
            "--output-root",
            str(output_root),
            "--topology",
            str(topology_path),
            "--threshold",
            str(threshold),
        ]
        command.extend(str(path) for path in files)
        return command

    def start_analysis(self) -> None:
        if not self.files:
            messagebox.showwarning("缺少文件", "请先添加至少一个节点电价 Excel。", parent=self)
            return
        topology_path = Path(self.topology_path.get().strip()).expanduser()
        if not topology_path.is_file():
            messagebox.showerror("拓扑文件不存在", str(topology_path), parent=self)
            return
        try:
            threshold = float(self.threshold.get().strip())
            if threshold < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("阈值错误", "价差阈值必须是大于或等于0的数字。", parent=self)
            return

        output_root = Path(self.output_root.get().strip()).expanduser()
        output_root.mkdir(parents=True, exist_ok=True)
        selected_files = list(self.files)
        self.latest_html = None
        self.open_latest_button.configure(state="disabled")
        self.clear_log()
        command = self.build_command(
            selected_files,
            output_root,
            topology_path,
            threshold,
        )

        def on_success(_code: int) -> None:
            self.latest_html = (
                output_root
                / result_folder_name(selected_files[-1])
                / result_html_name(selected_files[-1])
            )
            if self.latest_html.is_file():
                self.open_latest_button.configure(state="normal")
            messagebox.showinfo(
                "分析完成",
                f"已生成 {len(selected_files)} 个分析网页。",
                parent=self,
            )

        self.run_process(
            command,
            cwd=self.paths.section_analysis_dir,
            status="正在生成500kV断面分析...",
            on_success=on_success,
        )

    def open_latest(self) -> None:
        if self.latest_html is None or not self.latest_html.is_file():
            messagebox.showwarning("尚无网页", "请先成功运行一次分析。", parent=self)
            return
        open_path(self.latest_html)
