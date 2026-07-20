from __future__ import annotations

import queue
import threading
import inspect
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, TypeVar

from .runtime import TaskRegistry, utf8_environment
from .tasks import TaskSnapshot, TaskState, classify_error


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
        self.current_task_id: str | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._completion_queue: queue.Queue[tuple[TaskSnapshot, Callable | None]] = queue.Queue()
        self._poll_after_id: str | None = None
        self._max_log_lines = 5_000
        self._current_cancellable = True

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

        self.footer = ttk.Frame(self)
        self.footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(self.footer, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.cancel_button = ttk.Button(
            self.footer, text="取消任务", command=self.cancel_current_task, state="disabled"
        )
        self.cancel_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(self.footer, textvariable=self.status_var).grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )

        log_frame = ttk.LabelFrame(self.footer, text="运行日志", padding=8)
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
        self._schedule_poll()

    def set_task_console_visible(self, visible: bool) -> None:
        """Show or hide this module's generic task controls and log console."""
        if visible:
            self.footer.grid()
        else:
            self.footer.grid_remove()

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
        self.cancel_button.configure(state="normal" if busy and self._current_cancellable else "disabled")

    def append_log(self, text: str) -> None:
        self._log_queue.put(text.rstrip("\n") + "\n")
        if threading.current_thread() is threading.main_thread():
            self._drain_log_queue()

    def _schedule_poll(self) -> None:
        if self._poll_after_id is None:
            self._poll_after_id = self.after(50, self._poll_queues)

    def _poll_queues(self) -> None:
        self._poll_after_id = None
        self._drain_log_queue()
        while True:
            try:
                snapshot, callback = self._completion_queue.get_nowait()
            except queue.Empty:
                break
            self._finish_snapshot(snapshot, callback)
        self._schedule_poll()

    def _drain_log_queue(self) -> None:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self._log_queue.get_nowait())
            except queue.Empty:
                break
        if not chunks:
            return
        self.log_text.insert("end", "".join(chunks))
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        excess = max(0, line_count - 1 - self._max_log_lines)
        if excess:
            self.log_text.delete("1.0", f"{excess + 1}.0")
        self.log_text.see("end")

    def clear_log(self) -> None:
        while True:
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                break
        self.log_text.delete("1.0", "end")

    def run_in_thread(
        self,
        worker: Callable[[], T],
        *,
        status: str,
        on_success: Callable[[T], None] | None = None,
        cancellable: bool = True,
    ) -> None:
        if self.busy:
            return
        self._current_cancellable = cancellable
        self.set_busy(True, status)

        def task_worker(token, _emit):
            token.raise_if_cancelled()
            result = worker(token) if len(inspect.signature(worker).parameters) else worker()
            token.raise_if_cancelled()
            return result

        self.current_task_id = self.registry.start_callable(
            status,
            task_worker,
            on_done=lambda snapshot: self._completion_queue.put((snapshot, on_success)),
            cancellable=cancellable,
        )

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

    def cancel_current_task(self) -> None:
        if self.current_task_id and self.registry.cancel(self.current_task_id):
            self.set_busy(True, "正在取消...")

    def _finish_snapshot(self, snapshot: TaskSnapshot, callback: Callable | None) -> None:
        self.current_task_id = None
        if snapshot.state is TaskState.SUCCEEDED:
            self.set_busy(False, "已完成")
            if callback is not None:
                callback(snapshot.result)
            return
        if snapshot.state is TaskState.CANCELLED:
            self.append_log("任务已取消。")
            self.set_busy(False, "已取消")
            return
        error = classify_error(snapshot.error or RuntimeError("未知错误"))
        self.append_log(f"失败：{error.summary}")
        self.append_log(f"建议：{error.advice}")
        if snapshot.error is not None:
            self.append_log("".join(traceback.format_exception(snapshot.error)))
        self.set_busy(False, "执行失败")
        messagebox.showerror(
            "执行失败", f"{error.summary}\n\n{error.advice}", parent=self
        )

    def run_process(
        self,
        command: list[str],
        *,
        cwd: Path,
        status: str,
        on_success: Callable[[int], None] | None = None,
        cancellable: bool = True,
    ) -> None:
        if self.busy:
            return
        self.append_log(f"> {' '.join(command)}")
        self._current_cancellable = cancellable
        self.set_busy(True, status)
        self.current_task_id = self.registry.start_process(
            status,
            command,
            cwd=cwd,
            env=utf8_environment(),
            on_output=self.append_log,
            on_done=lambda snapshot: self._completion_queue.put((snapshot, on_success)),
            cancellable=cancellable,
        )

    def destroy(self) -> None:
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        super().destroy()
