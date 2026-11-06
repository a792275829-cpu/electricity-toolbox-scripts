from __future__ import annotations

from . import commands

import webbrowser
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from toolbox_shared.file_button import FileSelectButton
from toolbox_shared.section_topology import applied_path

from .file_dialogs import FileDialogState
from .page_support import add_path_row, open_directory
from .runtime import TaskRegistry, ToolPaths
from .widgets import ToolPage


def result_html_name(source: Path) -> str:
    return f"{Path(source).stem}_500kV分析.html"


class SectionAnalysisPage(ToolPage):
    tool_id = 'section-analysis'
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
        )
        self.paths = paths
        self.file_dialogs = FileDialogState(paths.file_dialog_settings)
        self.files: list[Path] = []
        self.output_root = tk.StringVar(
            master=self,
            value=str(Path.home() / "Downloads"),
        )
        self.topology_path = tk.StringVar(
            master=self,
            value=str(applied_path() if applied_path().exists() else paths.section_analysis_topology),
        )

        self.add_button = FileSelectButton(self.actions, text="添加电价文件", command=self.add_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(self.actions, text="移除选中", command=self.remove_selected)
        self.remove_button.pack(side="left", padx=(8, 0))
        self.clear_button = ttk.Button(self.actions, text="清空列表", command=self.clear_files)
        self.clear_button.pack(side="left", padx=(8, 0))
        self.editor_button = ttk.Button(self.actions, text="拓扑编辑", command=self.open_topology_editor)
        self.editor_button.pack(side="left", padx=(8, 0))
        self.browser_editor_button = ttk.Button(self.actions, text="浏览器编辑", command=self.open_browser_editor)
        self.browser_editor_button.pack(side="left", padx=(8, 0))
        self.run_button = ttk.Button(self.actions, text="开始分析", command=self.start_analysis)
        self.run_button.pack(side="left", padx=(16, 0))
        self.open_output_button = ttk.Button(
            self.actions,
            text="打开结果目录",
            command=lambda: open_directory(Path(self.output_root.get()).expanduser()),
        )
        self.open_output_button.pack(side="left", padx=(8, 0))

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
        self.register_busy_widgets(
            self.add_button,
            self.remove_button,
            self.clear_button,
            self.run_button,
            self.editor_button,
            self.browser_editor_button,
            topology_button,
            output_button,
        )
        self.append_log("请添加日前或实时节点电价 Excel 后开始分析。")

    def open_topology_editor(self) -> None:
        from .topology_editor import TopologyEditor
        editor = getattr(self, '_topology_editor', None)
        if editor is not None and editor.winfo_exists():
            editor.lift()
            return
        try:
            self._topology_editor = TopologyEditor(
                self, input_path=self.files[0] if len(self.files) == 1 else None,
                on_apply=lambda path: self.topology_path.set(str(path)),
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror('拓扑编辑', str(exc), parent=self)

    def confirm_close(self) -> bool:
        editor = getattr(self, '_topology_editor', None)
        if editor is not None and editor.winfo_exists() and not editor.close():
            return False
        browser = getattr(self, '_browser_editor', None)
        if browser is not None and browser.dirty:
            return messagebox.askyesno('浏览器编辑尚未保存',
                                      '请在网页保存草稿或导出备份。仍要关闭工具箱及本机编辑服务吗？', parent=self)
        return True

    def open_browser_editor(self) -> None:
        from toolbox_shared.topology_web import BrowserEditor
        browser = getattr(self, '_browser_editor', None)
        try:
            if browser is None or browser.closed:
                native = getattr(self, '_topology_editor', None)
                initial = native.data if native is not None and native.winfo_exists() else None
                browser = self._browser_editor = BrowserEditor(
                    input_path=self.files[0] if len(self.files) == 1 else None, initial_data=initial)
                self._browser_poll_id = self.after(250, self._poll_browser_editor)
            if not webbrowser.open_new_tab(browser.url):
                messagebox.showerror('浏览器编辑', '无法打开默认浏览器，请检查系统默认浏览器设置。', parent=self)
        except (OSError, ValueError) as exc:
            messagebox.showerror('浏览器编辑', str(exc), parent=self)

    def _poll_browser_editor(self) -> None:
        browser = getattr(self, '_browser_editor', None)
        if browser is None or browser.closed:
            return
        while True:
            try:
                path = browser.applied.get_nowait()
            except queue.Empty:
                break
            self.topology_path.set(str(path))
            self.append_log('浏览器编辑的拓扑已应用，后续分析使用该快照。')
        self._browser_poll_id = self.after(250, self._poll_browser_editor)

    def destroy(self) -> None:
        poll_id = getattr(self, '_browser_poll_id', None)
        if poll_id is not None:
            self.after_cancel(poll_id)
        browser = getattr(self, '_browser_editor', None)
        if browser is not None:
            browser.close()
        super().destroy()

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
            title="选择拓扑 Markdown 或已应用的编辑拓扑 JSON",
            initialdir=str(Path(self.topology_path.get()).expanduser().parent),
            filetypes=[("拓扑文件", "*.md *.json"), ("所有文件", "*.*")],
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
        threshold: float = 80.0,
    ) -> list[str]:
        return commands.section_analysis(self.paths, files, output_root, topology_path, threshold)

    def start_analysis(self) -> None:
        if not self.files:
            messagebox.showwarning("缺少文件", "请先添加至少一个节点电价 Excel。", parent=self)
            return
        topology_path = Path(self.topology_path.get().strip()).expanduser()
        if not topology_path.is_file():
            messagebox.showerror("拓扑文件不存在", str(topology_path), parent=self)
            return
        output_root = Path(self.output_root.get().strip()).expanduser()
        output_root.mkdir(parents=True, exist_ok=True)
        selected_files = list(self.files)
        self.clear_log()
        command = self.build_command(
            selected_files,
            output_root,
            topology_path,
        )

        def on_success(_code: int) -> None:
            self.append_log(f"已生成 {len(selected_files)} 个分析网页。")
            for source in selected_files:
                html = output_root / result_html_name(source)
                if not html.is_file():
                    self.append_log(f"未找到分析网页，无法自动打开：{html}")
                    continue
                try:
                    opened = webbrowser.open_new_tab(html.resolve().as_uri())
                except (OSError, webbrowser.Error) as exc:
                    self.append_log(f"自动打开网页失败：{exc}；网页位置：{html}")
                else:
                    if not opened:
                        self.append_log(f"浏览器未能打开网页，请从结果目录访问：{html}")

        self.run_process(
            command,
            action='run', cwd=self.paths.section_analysis_dir,
            status="正在生成500kV断面分析...",
            on_success=on_success,
        )
