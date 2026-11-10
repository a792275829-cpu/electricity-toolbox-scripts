from __future__ import annotations

import queue
import threading
import inspect
import traceback
import shlex
from datetime import datetime
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, TypeVar

from toolbox_shared.log_console import LogConsole

from .runtime import TaskRegistry, utf8_environment
from .tasks import TaskSnapshot, TaskState, classify_error
from .tasks.errors import RemoteTaskError


T = TypeVar("T")


class ToolPage(ttk.Frame):
    def start_login(self):
        from .commands import login
        command = login(self.paths, self.tool_id)
        self.run_process(command, cwd=self.paths.workspace, action="login", status="正在打开登录窗口...")

    def _save_login_draft(self, action):
        from .page_contracts import requires_qingneng
        values = {key: getattr(self, key).get() for key in ("mode", "fetch_data", "maintenance_json") if hasattr(self, key)}
        if not requires_qingneng(getattr(self, "tool_id", None), action, values):
            return True
        panel = getattr(self.winfo_toplevel(), "credentials_panel", None)
        if panel is not None and not panel.save_if_dirty():
            messagebox.showwarning("账号尚未保存", panel.status_var.get(), parent=self)
            return False
        return True

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
        self.status_var = tk.StringVar(master=self, value="")
        self._busy_widgets: list[tk.Widget] = []
        self.current_task_id: str | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._completion_queue: queue.Queue[tuple[TaskSnapshot, Callable | None]] = queue.Queue()
        self._poll_after_id: str | None = None
        self._current_cancellable = True
        self._log_fit_after_id: str | None = None
        self._log_fit_binding: str | None = None
        self._fitting_log = False

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
            self._description_wraplength = 820
            self.description_label = ttk.Label(
                header,
                text=description,
                style="Muted.TLabel",
                wraplength=820,
            )
            self.description_label.pack(anchor="w", pady=(6, 0))
            header.bind("<Configure>", self._resize_description)

        self.actions = ttk.Frame(self)
        self.actions.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        self.content = ttk.Frame(self)
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)

        self.footer = ttk.Frame(self)
        self.footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(self.footer, mode="indeterminate")
        self.progress.bind("<Map>", self._sync_progress_animation)
        self.progress.bind("<Unmap>", self._sync_progress_animation)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress.grid_remove()
        self.cancel_button = ttk.Button(
            self.footer, text="取消任务", command=self.cancel_current_task, state="disabled"
        )
        self.cancel_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(self.footer, textvariable=self.status_var).grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )

        self.log_console = LogConsole(self.footer, module=title or "工具箱")
        self.log_console.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.log_text = self.log_console.text
        self.log_text.configure(height=10)
        self._schedule_poll()

    def _resize_description(self, event) -> None:
        # Wrap against the space actually available, not the page's own width:
        # a description wrapped to the page width feeds back into that width and
        # leaves the page exactly one horizontal scrollbar wide.
        viewport = getattr(self.winfo_toplevel(), "page_viewport", None)
        available = viewport.canvas.winfo_width() if viewport is not None else event.width
        wraplength = max(180, min(820, available - 44))
        if wraplength != self._description_wraplength:
            self._description_wraplength = wraplength
            self.description_label.configure(wraplength=wraplength)

    LOG_MIN_LINES = 3
    LOG_MAX_LINES = 10
    LOG_PANEL_GAP = 8

    def prepare_for_display(self) -> None:
        """Size the log panel before the page is mapped.

        A page taller than the viewport has to scroll, and every switch then
        repaints the whole clipped canvas window (measured about three times
        the cost of a page that fits). The log panel gives up lines first so
        the form itself never has to.
        """
        self._fit_description()
        self._fit_log_console(force=True)
        # 首次适配发生在页面还没排完版的时候，描述换行和行高会再变一次；
        # 等这一轮布局结束再收缩一遍，避免字体／主题差异留下几像素溢出。
        self._queue_log_fit()
        if self._log_fit_binding is None:
            self._log_fit_binding = self.winfo_toplevel().bind(
                "<Configure>", self._queue_log_fit, add="+")

    def _fit_description(self) -> None:
        if not hasattr(self, "description_label"):
            return
        viewport = getattr(self.winfo_toplevel(), "page_viewport", None)
        if viewport is None:
            return
        available = viewport.canvas.winfo_width()
        if available <= 1:
            return
        wraplength = max(180, min(820, available - 44))
        if wraplength != self._description_wraplength:
            self._description_wraplength = wraplength
            self.description_label.configure(wraplength=wraplength)

    def _queue_log_fit(self, _event=None) -> None:
        if self._log_fit_after_id is None:
            self._log_fit_after_id = self.after_idle(self._run_log_fit)

    def _run_log_fit(self) -> None:
        self._log_fit_after_id = None
        # 拖拽窗口时只收缩不放大：放大需要重新排版，留到下次进入页面时再做。
        self._fit_log_console(allow_grow=False)

    def _fit_log_console(self, *, force: bool = False, allow_grow: bool = True) -> None:
        if self._fitting_log:
            return
        if not force and not self.winfo_ismapped():
            # 窗口尺寸变化时只有当前页面需要重新适配，隐藏页面进入时再算。
            return
        viewport = getattr(self.winfo_toplevel(), "page_viewport", None)
        if viewport is None:
            return
        # 留 2 像素余量：字体回退或主题差异会让实际行高比估算略大，正好卡在
        # 视口高度上时多余的一点高度会触发整页滚动。
        available = viewport.canvas.winfo_height() - 2
        if available <= 1:
            return
        if self.winfo_reqheight() <= 1:
            # A fresh page reports no size until it has been laid out; a layout
            # pass draws nothing, unlike the repaint this avoids.
            self.update_idletasks()
        text = self.log_text
        lines = int(text.cget("height"))
        text_height = text.winfo_reqheight()
        if lines <= 0 or text_height <= 0:
            return
        line_height = max(1.0, text_height / lines)
        fixed = self.winfo_reqheight() - text_height - self.LOG_PANEL_GAP
        desired = int((available - fixed) / line_height)
        desired = max(self.LOG_MIN_LINES, min(self.LOG_MAX_LINES, desired))
        if desired > lines and not allow_grow:
            return
        if desired == lines and self.winfo_reqheight() <= available:
            return
        self._fitting_log = True
        try:
            # 逐次收敛：滚动态和日志行数会互相影响，算出来仍超出时再让一行。
            for _ in range(6):
                text.configure(height=desired)
                self.update_idletasks()
                if desired <= self.LOG_MIN_LINES:
                    break
                overflow = self.winfo_reqheight() - available
                if overflow <= 0:
                    break
                desired = max(self.LOG_MIN_LINES,
                              desired - max(1, int(overflow / line_height + 0.999)))
        finally:
            self._fitting_log = False

    def set_task_console_visible(self, visible: bool) -> None:
        """Show or hide this module's generic task controls and log console."""
        if visible:
            self.footer.grid()
        else:
            self.footer.grid_remove()

    def destroy(self) -> None:
        if self._log_fit_after_id is not None:
            try:
                self.after_cancel(self._log_fit_after_id)
            except tk.TclError:
                pass
            self._log_fit_after_id = None
        if self._log_fit_binding is not None:
            try:
                self.winfo_toplevel().unbind("<Configure>", self._log_fit_binding)
            except tk.TclError:
                pass
            self._log_fit_binding = None
        super().destroy()

    def register_busy_widgets(self, *widgets: tk.Widget) -> None:
        self._busy_widgets.extend(widgets)
        primary = tuple(getattr(self, name, None) for name in (
            "run_button", "export_button", "generate_button", "update_button",
            "upload_button", "predict_button",
        ))
        for widget in widgets:
            if widget in primary and isinstance(widget, ttk.Button):
                widget.configure(style="Primary.TButton")

    def set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in self._busy_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        if busy:
            self._schedule_poll()
            self.progress.grid()
            self._sync_progress_animation()
        else:
            self.progress.stop()
            self.progress.grid_remove()
        if status:
            self.status_var.set(status)
            if busy:
                self.log_console.status.set(status)
            elif status == "无数据":
                self.log_console.finish(status)
        self.cancel_button.configure(state="normal" if busy and self._current_cancellable else "disabled")

    def _sync_progress_animation(self, _event=None) -> None:
        # A 10 ms timer can redraw faster than macOS can paint the page, keeping
        # the shared event loop busy. Hidden tasks need no animation at all.
        if self.busy and self.progress.winfo_ismapped():
            self.progress.start(80)
        else:
            self.progress.stop()

    def append_log(self, text: str) -> None:
        self._log_queue.put(f"[{datetime.now():%H:%M:%S}] " + text.rstrip("\n") + "\n")
        if threading.current_thread() is threading.main_thread():
            self._drain_log_queue()

    def _schedule_poll(self) -> None:
        if self._poll_after_id is None:
            self._poll_after_id = self.after(50, self._poll_queues)

    def _has_pending_work(self) -> bool:
        # Cached pages must keep servicing hidden tasks, but an idle page needs
        # no timer. Otherwise visiting more modules adds permanent UI wakeups.
        return (self.busy or self.current_task_id is not None
                or not self._log_queue.empty() or not self._completion_queue.empty())

    def _poll_queues(self) -> None:
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
        self._poll_after_id = None
        # Yield to navigation between batches, including logs from hidden pages.
        self._drain_log_queue(limit=200)
        while self._log_queue.empty():
            try:
                snapshot, callback = self._completion_queue.get_nowait()
            except queue.Empty:
                break
            self._finish_snapshot(snapshot, callback)
        if self._has_pending_work():
            self._schedule_poll()

    def _drain_log_queue(self, *, limit: int | None = None, render: bool = True) -> None:
        chunks: list[str] = []
        size = 0
        while limit is None or (len(chunks) < limit and size < 64_000):
            try:
                chunk = self._log_queue.get_nowait()
                chunks.append(chunk)
                size += len(chunk)
            except queue.Empty:
                break
        if not chunks:
            return
        self.log_console.append("".join(chunks), render=render)

    def clear_log(self) -> None:
        self._drain_log_queue()
        self.log_console.begin()

    def run_in_thread(
        self,
        worker: Callable[[], T],
        *,
        status: str,
        on_success: Callable[[T], None] | None = None,
        cancellable: bool = True,
        action: str = "run",
    ) -> None:
        if self.busy:
            return
        if not self._save_login_draft(action):
            return
        if getattr(self.registry, "remote_business", False) is True and not getattr(worker, "_remote_business", False):
            worker = self.registry.prepare_page_worker(self, status, action=action)
        if not self.log_console._initialized or self.log_console.finished:
            self.log_console.begin(status)
        self._current_cancellable = cancellable
        self.log_console.status.set(status)
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
        self.log_console.finish("已完成")
        self.set_busy(False, "已完成")
        if callback is not None:
            callback(result)

    def _finish_error(self, exc: Exception, details: str) -> None:
        error = classify_error(exc, details)
        self.log_console.finish("执行失败", error.summary, error.advice)
        self.append_log(details)
        self.set_busy(False, "执行失败")
        messagebox.showerror("执行失败", f"{error.summary}\n\n{error.advice}", parent=self)

    def cancel_current_task(self) -> None:
        if self.current_task_id and self.registry.cancel(self.current_task_id):
            self.set_busy(True, "正在取消...")

    def _finish_snapshot(self, snapshot: TaskSnapshot, callback: Callable | None) -> None:
        self.current_task_id = None
        self._drain_log_queue()
        if snapshot.state is TaskState.SUCCEEDED:
            output = self.log_console.contents()
            warning = any(marker in output for marker in ("跳过", "回读不一致", "读取失败", "数据缺失", "无数据："))
            state = "完成（有提示）" if warning else "已完成"
            self.log_console.finish(state)
            self.set_busy(False, state)
            if callback is not None:
                callback(snapshot.result)
            return
        if snapshot.state is TaskState.CANCELLED:
            self.log_console.finish("已取消")
            self.set_busy(False, "已取消")
            return
        error = classify_error(snapshot.error or RuntimeError("未知错误"), self.log_console.contents())
        self.log_console.finish("执行失败", error.summary, error.advice)
        if snapshot.error is not None:
            details = (snapshot.error.log_details if isinstance(snapshot.error, RemoteTaskError)
                       else "".join(traceback.format_exception(snapshot.error)))
            self.append_log(details)
        self.set_busy(False, "执行失败")
        kind = getattr(snapshot.error, "kind", None) or getattr(snapshot.error, "error_detail", {}).get("login_kind")
        panel = getattr(self.winfo_toplevel(), "credentials_panel", None)
        if kind == "verification" and panel is not None and callable(getattr(self, "start_login", None)):
            panel.offer_manual_login(self.start_login)
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
        action: str = "run",
    ) -> None:
        if self.busy:
            return
        if not self._save_login_draft(action):
            return
        if getattr(self.registry, "remote_business", False) is True:
            worker = self.registry.prepare_page_worker(self, status, command=command, process=True, action=action)
            self.append_log(f"工作目录：{cwd}\n> {shlex.join(command)}")
            self.run_in_thread(worker, status=status, on_success=on_success, cancellable=cancellable, action=action)
            return
        if not self.log_console._initialized or self.log_console.finished:
            self.log_console.begin(status)
        self.append_log(f"工作目录：{cwd}\n> {shlex.join(command)}")
        self._current_cancellable = cancellable
        self.log_console.status.set(status)
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
        # The app has stopped task producers before destroying pages. Persist
        # their queued tail without rebuilding either log view during shutdown.
        while not self._log_queue.empty():
            self._drain_log_queue(limit=200, render=False)
        self.progress.stop()
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        super().destroy()
