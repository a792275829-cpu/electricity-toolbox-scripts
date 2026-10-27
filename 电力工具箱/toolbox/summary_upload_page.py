from __future__ import annotations

import shlex
import queue
import threading
import tkinter as tk
from toolbox_choices import SegmentedGroup

from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_dialogs import FileDialogState
from .page_support import add_path_row, default_review_folder_for_date, open_directory
from .runtime import TaskRegistry, ToolPaths, load_module, node_executable, utf8_environment
from .review_classification import default_template_folder, missing_review_stations, prepare_review_folder
from .tasks import CancellationToken, ProcessRunner
from .widgets import ToolPage


def _resolve_default_input(results: queue.Queue, today: date, review_root: Path | None) -> None:
    # Keep filesystem access and worker references to Tk objects off the UI thread.
    try:
        if review_root is None and default_template_folder().parent.is_dir():
            review_root = default_template_folder().parent
        results.put((default_review_folder_for_date(today, review_root), None))
    except OSError as exc:
        results.put((None, str(exc)))


class SummaryUploadPage(ToolPage):
    MODE_LABELS = {"summary": "仅汇总", "upload": "仅上传", "both": "汇总并上传"}

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
            title="出清结果汇总上传",
            description="共用复盘文件夹，可生成电量汇总表、上传出清结果，或依次执行两项功能。",
        )
        self.paths = paths
        self._file_dialogs: FileDialogState | None = None
        today = today or date.today()
        target_day = today + timedelta(days=1)
        default_input = (review_root or default_template_folder().parent) / f"{target_day.month}-{target_day.day}"
        self._default_input_results: queue.Queue = queue.Queue()
        self._input_edited = False
        self.input_dir = tk.StringVar(master=self, value=str(default_input))
        self.output_file = tk.StringVar(master=self, value=str(self.default_output_for(default_input)))
        self.mode = tk.StringVar(master=self, value="summary")

        form = ttk.LabelFrame(self.content, text="数据与功能", padding=12)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="执行功能").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 10))
        modes = SegmentedGroup(form, variable=self.mode, choices=self.MODE_LABELS.items())
        modes.grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, 10))
        mode_buttons = modes.buttons
        browse = add_path_row(
            form, row=1, label="复盘文件夹", variable=self.input_dir,
            command=self.choose_input_dir, button_text="选择文件夹",
        )

        self.run_button = ttk.Button(self.actions, command=self.start_selected)
        self.run_button.pack(side="left")
        self.preview_button = ttk.Button(self.actions, text="预览上传文件", command=self.preview_upload)
        self.open_button = ttk.Button(self.actions, text="打开输出目录", command=self.open_output_dir)
        self.register_busy_widgets(
            browse, self.run_button, self.preview_button, self.open_button,
            *mode_buttons,
            *(widget for widget in form.winfo_children() if isinstance(widget, ttk.Entry)),
        )
        self._previous_default_output = str(self.default_output_for(default_input))
        self.input_dir.trace_add("write", self._input_changed)
        self.mode.trace_add("write", self._update_mode)
        self._update_mode()
        self._defaults_thread = threading.Thread(
            target=_resolve_default_input,
            args=(self._default_input_results, today, review_root),
            name="summary-folder-default", daemon=True,
        )
        self._defaults_thread.start()

    @property
    def file_dialogs(self) -> FileDialogState:
        if self._file_dialogs is None:
            self._file_dialogs = FileDialogState(self.paths.file_dialog_settings)
        return self._file_dialogs

    def _poll_queues(self) -> None:
        self._apply_default_input()
        super()._poll_queues()

    def _has_pending_work(self) -> bool:
        return (super()._has_pending_work() or self._defaults_thread.is_alive()
                or not self._default_input_results.empty())

    def _apply_default_input(self) -> None:
        try:
            folder, error = self._default_input_results.get_nowait()
        except queue.Empty:
            pass
        else:
            if error:
                self.append_log(f"默认目录读取失败，可手动选择复盘文件夹：{error}")
            elif not self._input_edited and not self.busy:
                self.input_dir.set(str(folder))

    @staticmethod
    def default_output_for(input_dir: Path) -> Path:
        return input_dir / f"{input_dir.name}_汇总.xlsx"

    def _input_changed(self, *_args) -> None:
        self._input_edited = True
        default_output = str(self.default_output_for(Path(self.input_dir.get().strip()).expanduser()))
        if self.output_file.get() in ("", self._previous_default_output):
            self.output_file.set(default_output)
        self._previous_default_output = default_output

    def _update_mode(self, *_args) -> None:
        mode = self.mode.get()
        self.preview_button.pack_forget()
        self.open_button.pack_forget()
        if mode != "summary":
            self.preview_button.pack(side="left", padx=(8, 0))
        if mode != "upload":
            self.open_button.pack(side="left", padx=(8, 0))
        self.run_button.configure(text={
            "summary": "开始汇总", "upload": "开始上传", "both": "开始汇总并上传",
        }[mode])

    def choose_input_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="选择复盘文件夹",
            initialdir=str(self.file_dialogs.initial_directory("summary_upload_input", self.input_dir.get())),
            parent=self,
        )
        if selected:
            self.input_dir.set(selected)
            self.output_file.set(str(self.default_output_for(Path(selected))))
            self.file_dialogs.remember_directory("summary_upload_input", selected)

    def open_output_dir(self) -> None:
        if self.output_file.get().strip():
            open_directory(Path(self.output_file.get().strip()).expanduser().parent)

    def _validated_source(self) -> Path | None:
        value = self.input_dir.get().strip()
        source = Path(value).expanduser()
        if not value or not source.is_dir():
            messagebox.showerror("目录无效", "请选择有效的复盘文件夹。", parent=self)
            return None
        return source.resolve()

    def build_upload_command(self, mode: str, source: Path) -> list[str]:
        return [node_executable(), str(self.paths.private_uploader), mode, "--source", str(source)]

    def preview_upload(self) -> None:
        if self.busy:
            return
        source = self._validated_source()
        if source is None:
            return
        excluded = (Path(self.output_file.get()).expanduser(),)
        command = self.build_upload_command("--plan", source)
        self.clear_log()

        def worker(token: CancellationToken) -> None:
            prepared = prepare_review_folder(source, logger=self.append_log, token=token, excluded=excluded)
            preview_command = [*command[:-1], str(prepared)]
            self.append_log(f"> {shlex.join(preview_command)}")
            ProcessRunner().run(
                preview_command, cwd=self.paths.private_uploader_dir, env=utf8_environment(),
                token=token, on_output=self.append_log,
            )

        self.run_in_thread(
            worker, status="正在归类并预览上传文件...",
        )

    def start_selected(self) -> None:
        if self.busy:
            return
        source = self._validated_source()
        if source is None:
            return
        mode = self.mode.get()
        output = None
        if mode != "upload":
            output = (Path(self.output_file.get().strip()).expanduser()
                      if self.output_file.get().strip() else self.default_output_for(source))
            if output.suffix.lower() != ".xlsx":
                messagebox.showerror("输出无效", "汇总输出文件必须使用 .xlsx 扩展名。", parent=self)
                return
            if mode == "both" and source in output.resolve().parents and output.resolve().parent != source:
                messagebox.showerror(
                    "输出位置无效", "汇总文件请保存在复盘目录根目录或目录之外，避免被当作原始文件上传。", parent=self,
                )
                return
            self.output_file.set(str(output))
        if mode != "summary" and not messagebox.askokcancel(
            "确认上传",
            f"确认{self.MODE_LABELS[mode]}？\n\n复盘文件夹：\n{source}\n\n混放的日前、实时文件会先移入六个单元文件夹，再上传匹配文件。",
            parent=self,
        ):
            return
        # Resolve Node only for an upload operation; summary remains Python-only.
        try:
            command = self.build_upload_command("--execute", source) if mode != "summary" else None
        except FileNotFoundError as exc:
            messagebox.showerror("上传不可用", str(exc), parent=self)
            return
        self.clear_log()
        excluded = (output,) if output is not None else (Path(self.output_file.get()).expanduser(),)

        missing: tuple[str, ...] = ()

        def worker(token: CancellationToken) -> Path | None:
            nonlocal missing
            prepared = prepare_review_folder(source, logger=self.append_log, token=token, excluded=excluded)
            missing = missing_review_stations(prepared)
            result = None
            if output is not None:
                token.raise_if_cancelled()
                self.append_log("开始汇总日前、实时交易结果...")
                module = load_module("toolbox_summary", self.paths.summary)
                result = module.run_summary(input_dir=prepared, output_path=output, logger=self.append_log)
                self.append_log(f"汇总已完成：{result}")
            if command is not None:
                token.raise_if_cancelled()
                self.append_log("开始上传原始匹配文件...")
                upload_command = [*command[:-1], str(prepared)]
                self.append_log(f"> {shlex.join(upload_command)}")
                ProcessRunner().run(
                    upload_command, cwd=self.paths.private_uploader_dir, env=utf8_environment(),
                    token=token, on_output=self.append_log,
                )
                self.append_log("出清结果上传已完成。")
            return result

        def finished(path: Path | None) -> None:
            if path is not None:
                self.output_file.set(str(path))
            detail = f"\n汇总文件：\n{path}" if path is not None else ""
            completed_missing = getattr(self, "_remote_missing", missing)
            if completed_missing:
                detail += "\n缺少单元数据：" + "、".join(completed_missing)
            messagebox.showinfo("完成", f"{self.MODE_LABELS[mode]}已完成。{detail}", parent=self)

        self.run_in_thread(
            worker, status={"summary": "正在汇总...", "upload": "正在上传...", "both": "正在汇总并上传..."}[mode],
            on_success=finished,
            cancellable=mode == "summary",
        )
