from __future__ import annotations

import subprocess
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, TypeVar

from .runtime import TaskRegistry, utf8_environment


T = TypeVar("T")


class ToolPage(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        registry: TaskRegistry,
        title: str = "",
        description: str = "",
    ) -> None:
        super().__init__(parent, padding=18)
        self.registry = registry
        self.busy = False
        self.status_var = tk.StringVar(master=self, value="就绪")
        self._busy_widgets: list[tk.Widget] = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        if title:
            ttk.Label(
                header,
                text=title,
                style="PageTitle.TLabel",
            ).pack(anchor="w")
        if description:
            ttk.Label(
                header,
                text=description,
                style="Muted.TLabel",
                wraplength=820,
            ).pack(anchor="w", pady=(4, 0))

        self.actions = ttk.Frame(self)
        self.actions.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        self.content = ttk.Frame(self)
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)

        footer = ttk.Frame(self)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(footer, textvariable=self.status_var).grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )

        log_frame = ttk.LabelFrame(footer, text="运行日志", padding=8)
        log_frame.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=9,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            borderwidth=0,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def register_busy_widgets(self, *widgets: tk.Widget) -> None:
        self._busy_widgets.extend(widgets)

    def set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in self._busy_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()
        if status:
            self.status_var.set(status)

    def append_log(self, text: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.after(0, self.append_log, text)
            return
        self.log_text.insert("end", text.rstrip("\n") + "\n")
        self.log_text.see("end")

    def clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    def run_in_thread(
        self,
        worker: Callable[[], T],
        *,
        status: str,
        on_success: Callable[[T], None] | None = None,
    ) -> None:
        if self.busy:
            return
        self.set_busy(True, status)

        def target() -> None:
            current = threading.current_thread()
            try:
                result = worker()
            except Exception as exc:
                details = traceback.format_exc()
                self.after(0, self._finish_error, exc, details)
            else:
                self.after(0, self._finish_success, result, on_success)
            finally:
                self.registry.unregister_thread(current)

        thread = threading.Thread(target=target, daemon=True)
        self.registry.register_thread(thread)
        thread.start()

    def _finish_success(
        self,
        result: T,
        callback: Callable[[T], None] | None,
    ) -> None:
        self.set_busy(False, "已完成")
        if callback is not None:
            callback(result)

    def _finish_error(self, exc: Exception, details: str) -> None:
        self.append_log(f"失败：{exc}")
        self.append_log(details)
        self.set_busy(False, "执行失败")
        messagebox.showerror("执行失败", str(exc), parent=self)

    def run_process(
        self,
        command: list[str],
        *,
        cwd: Path,
        status: str,
        on_success: Callable[[int], None] | None = None,
    ) -> None:
        def worker() -> int:
            self.append_log(f"> {' '.join(command)}")
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
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
            return code

        self.run_in_thread(worker, status=status, on_success=on_success)
