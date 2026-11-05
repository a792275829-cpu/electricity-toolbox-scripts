from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from toolbox_shared.choices import SegmentedGroup, SettingCheckbutton
from toolbox_shared.file_button import FileSelectButton

from . import commands
from .file_dialogs import FileDialogState
from .runtime import TaskRegistry, ToolPaths
from .widgets import ToolPage

# 与网站一致：单批最多 12 个文件、合计不超过 10MB；超出部分由脚本自动拆批。
MAX_FILES = 12
MAX_BYTES = 10 * 1024 * 1024
FILE_TYPES = (".xlsx", ".xls", ".csv", ".pdf", ".zip")


class PrivateBatchUploadPage(ToolPage):
    tool_id = 'private-batch-upload'
    UNITS = (("海风", "海风"), ("鮀莲", "鮀莲"), ("归湖", "归湖"),
             ("东莞", "东莞"), ("汕头", "汕头"), ("海门", "海门"))
    SUMMARY_TYPES = (("none", "不适用"), ("formal", "正式账单"), ("temporary", "临时账单"))

    def __init__(self, parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry) -> None:
        super().__init__(
            parent,
            registry=registry,
            title="私有数据批量上传",
            description="按单元选择文件，直连网站“批量上传”接口；每批最多 12 个文件、合计 10MB，超出自动拆批。",
        )
        self.paths = paths
        self._file_dialogs: FileDialogState | None = None
        self.selected_paths: list[Path] = []
        self.unit = tk.StringVar(master=self, value="海风")
        self.summary_type = tk.StringVar(master=self, value="none")
        self.verify = tk.BooleanVar(master=self, value=True)
        self.selection_var = tk.StringVar(master=self, value="未选择文件")
        self.summary_var = tk.StringVar(master=self, value="请选择要上传到该单元的文件。")

        form = ttk.LabelFrame(self.content, text="上传单元与文件", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="上传单元").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        units = SegmentedGroup(form, variable=self.unit, choices=self.UNITS)
        units.grid(row=0, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, text="月结算单").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        summary_types = SegmentedGroup(form, variable=self.summary_type, choices=self.SUMMARY_TYPES)
        summary_types.grid(row=1, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, textvariable=self.summary_var).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(
            form, textvariable=self.selection_var, justify="left", style="Muted.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.choose_button = FileSelectButton(self.actions, text="选择文件", command=self.choose_files)
        self.choose_button.pack(side="left")
        self.run_button = ttk.Button(self.actions, text="开始上传", command=self.confirm_upload)
        self.run_button.pack(side="left", padx=(8, 0))
        SettingCheckbutton(self.actions, text="上传后回读数据状态", variable=self.verify).pack(side="right")
        self.register_busy_widgets(self.choose_button, self.run_button, *units.buttons, *summary_types.buttons)

    @property
    def file_dialogs(self) -> FileDialogState:
        if self._file_dialogs is None:
            self._file_dialogs = FileDialogState(self.paths.file_dialog_settings)
        return self._file_dialogs

    def choose_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择要上传的私有数据文件",
            initialdir=str(self.file_dialogs.initial_directory("private_batch_upload")),
            filetypes=[("表格与压缩包", "*.xlsx *.xls *.csv *.pdf *.zip"), ("所有文件", "*.*")],
            parent=self,
        )
        if not selected:
            return
        self.apply_files([Path(item) for item in selected])
        self.file_dialogs.remember_file("private_batch_upload", selected[0])

    def apply_files(self, paths: list[Path]) -> None:
        files: list[Path] = []
        skipped: list[str] = []
        for path in paths:
            if not path.is_file() or path.name.startswith("~$") or path.suffix.lower() not in FILE_TYPES:
                skipped.append(path.name)
                continue
            files.append(path)
        self.selected_paths = files
        total = sum(path.stat().st_size for path in files)
        self.selection_var.set("\n".join(path.name for path in files) if files else "未选择文件")
        batches = self.batch_count(files)
        self.summary_var.set(
            f"已选择 {len(files)} 个文件，合计 {total / 1048576:.2f}MB，将分 {batches} 批上传。"
            + (f"\n已跳过不支持的 {len(skipped)} 个文件：{'、'.join(skipped)}" if skipped else "")
        )

    @staticmethod
    def batch_count(paths: list[Path]) -> int:
        if not paths:
            return 0
        batches, count, size = 1, 0, 0
        for path in paths:
            current = path.stat().st_size
            if count >= MAX_FILES or size + current > MAX_BYTES:
                batches += 1
                count, size = 0, 0
            count += 1
            size += current
        return batches

    def build_upload_command(self) -> list[str]:
        return commands.private_batch(
            self.paths, self.unit.get(), self.selected_paths,
            summary_type=self.summary_type.get(), verify=self.verify.get(),
        )

    def confirm_upload(self) -> None:
        if self.busy:
            return
        if not self.selected_paths:
            messagebox.showwarning("缺少文件", "请先选择要上传的文件。", parent=self)
            return
        oversized = [path.name for path in self.selected_paths if path.stat().st_size > MAX_BYTES]
        if oversized:
            messagebox.showerror(
                "文件过大", "网站不接受超过 10MB 的单个文件：\n" + "、".join(oversized), parent=self,
            )
            return
        unit = self.unit.get()
        count = self.batch_count(self.selected_paths)
        if not messagebox.askokcancel(
            "确认上传",
            f"确认把 {len(self.selected_paths)} 个文件上传到「{unit}」？\n\n"
            + "\n".join(path.name for path in self.selected_paths)
            + f"\n\n将分 {count} 批调用批量上传接口。",
            parent=self,
        ):
            return
        self.clear_log()
        self.run_process(
            self.build_upload_command(), cwd=self.paths.private_uploader_dir,
            status="正在上传私有数据...", cancellable=False, action="run",
        )

    def start_login(self) -> None:
        self.run_process(
            commands.login(self.paths, self.tool_id), cwd=self.paths.private_uploader_dir,
            status="正在打开登录窗口...", action="login",
        )
