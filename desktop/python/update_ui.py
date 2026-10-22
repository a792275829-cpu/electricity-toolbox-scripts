"""Tk update UI for digest-verified DMG downloads."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from github_update import Cancelled, GitHubUpdater


class UpdateController:
    def __init__(self, app, service, updater=None):
        self.app, self.service = app, service
        self.updater = updater or GitHubUpdater(os.environ.get("TOOLBOX_APP_VERSION", "0.1.3"))
        self.release = None
        self.checking = False
        self.manual = False
        self.window = None
        self.messages = queue.Queue()
        self.app.after(40, self.poll)
        self.app.after(300, self.check)

    def worker(self, operation, done):
        def run():
            try:
                result = operation()
            except Exception as exc:
                self.messages.put((done, None, exc))
            else:
                self.messages.put((done, result, None))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        for _ in range(64):
            try:
                callback, result, error = self.messages.get_nowait()
            except queue.Empty:
                break
            callback(result, error)
        self.app.after(40, self.poll)

    def check(self, manual=False):
        self.manual = self.manual or manual
        if self.checking:
            return
        self.checking = True
        self.worker(self.updater.check, self.checked)

    def checked(self, release, error):
        self.checking = False
        manual, self.manual = self.manual, False
        if error:
            if manual:
                messagebox.showerror("无法检查更新", str(error), parent=self.app)
            return
        self.release = release
        self.app.set_update_available(release is not None)
        if manual:
            if release:
                self.show()
            else:
                messagebox.showinfo("已是最新版本", f"当前版本 v{self.updater.current}", parent=self.app)

    def show(self):
        if not self.release:
            return
        if self.window and self.window.winfo_exists():
            self.window.lift()
            return
        self.window = UpdateWindow(self, self.release)

    def update_event(self, method, params):
        window = self.window
        if window and window.winfo_exists():
            window.update_event(method, params)
        elif method == "update_failed":
            messagebox.showerror("更新未完成", params.get("error", "请重试更新"), parent=self.app)

    def activate(self):
        self.check()


class UpdateWindow(tk.Toplevel):
    def __init__(self, controller, release):
        super().__init__(controller.app)
        self.controller, self.release = controller, release
        self.downloading = self.installing = False
        self.cancel_download = threading.Event()
        self.downloaded_path = None
        self.title("软件更新")
        self.transient(controller.app)
        self.geometry("560x510")
        self.minsize(480, 420)
        self.protocol("WM_DELETE_WINDOW", self.dismiss)
        self.bind("<Escape>", lambda _e: self.dismiss())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body = ttk.Frame(self, padding=24)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text=f"发现新版本 {release.version}", font=("Helvetica", 20, "bold")).grid(sticky="w")
        ttk.Label(body, text=f"当前 v{controller.updater.current}  →  {release.version}  ·  Apple 芯片").grid(sticky="w", pady=(8, 16))
        ttk.Separator(body).grid(sticky="ew", pady=(0, 12))
        notes_frame = ttk.Frame(body)
        notes_frame.grid(sticky="nsew")
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(0, weight=1)
        notes = tk.Text(notes_frame, wrap="word", relief="flat", height=12, padx=8, pady=8)
        notes.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(notes_frame, command=notes.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        notes.configure(yscrollcommand=scrollbar.set)
        notes.insert("1.0", release.notes)
        notes.configure(state="disabled")
        self.progress = ttk.Progressbar(body, maximum=100)
        self.progress.grid(sticky="ew", pady=(12, 8))
        self.status = tk.StringVar(self, "开始更新后将自动下载、校验、退出并安装，再重新启动工具箱；配置和数据保留。日常优先使用增量补丁。")
        ttk.Label(body, textvariable=self.status, wraplength=490).grid(sticky="w", pady=(0, 16))
        actions = ttk.Frame(body)
        actions.grid(sticky="ew")
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="发布页面", command=lambda: webbrowser.open(release.page)).grid(row=0, column=0)
        self.cancel_button = ttk.Button(actions, text="稍后", command=self.dismiss)
        self.cancel_button.grid(row=0, column=2, padx=(8, 8))
        self.primary = ttk.Button(actions, text="开始更新", command=self.perform, style="Primary.TButton")
        self.primary.grid(row=0, column=3)
        if not release.asset:
            self.primary.state(["disabled"])
            self.status.set("此版本尚未提供 Apple 芯片安装包，请查看发布页面。")

    def dismiss(self):
        if self.installing:
            return
        if self.downloading:
            self.cancel_download.set()
            self.status.set("正在取消下载…")
            return
        self.destroy()

    def perform(self):
        if self.downloading or self.installing:
            return
        try:
            self.controller.service.call('prepare_update', {})
        except Exception as exc:
            self.status.set(str(exc))
            return
        self.cancel_download.clear()
        self.downloading = True
        self.primary.configure(text="下载中…", state="disabled")
        self.cancel_button.configure(text="取消下载")
        self.status.set("正在连接 GitHub…")
        def progress(fraction):
            self.controller.messages.put((self.download_progress, fraction, None))
        def download():
            try:
                self.controller.updater.prepare_update()
                return self.controller.updater.download(self.release, self.cancel_download, progress)
            except Exception as error:
                try:
                    self.controller.updater.abort_update()
                except Exception as cleanup:
                    error.add_note(f'清理更新状态失败：{cleanup}')
                raise
        self.controller.worker(download, self.download_finished)

    def download_progress(self, fraction, error):
        if self.winfo_exists():
            self.progress["value"] = fraction * 100
            self.status.set(f"正在下载并准备更新 · {fraction:.0%}")

    def download_finished(self, path, error):
        if not self.winfo_exists():
            return
        self.downloading = False
        if isinstance(error, Cancelled):
            self.controller.service.call('abort_update', {})
            self.destroy()
            return
        if self.cancel_download.is_set():
            def cancelled(_result, _error):
                self.controller.service.call('abort_update', {})
                self.destroy()
            self.controller.worker(self.controller.updater.abort_update, cancelled)
            return
        self.cancel_button.configure(text="关闭")
        self.primary.configure(state="normal", text="重试下载")
        if error:
            self.controller.service.call('abort_update', {})
            self.status.set(str(error))
            return
        self.downloaded_path = path
        self.installing = True
        self.primary.configure(text="安装准备中…", state='disabled')
        self.cancel_button.configure(state='disabled')
        self.status.set('正在校验镜像和应用，准备自动安装…')
        def prepare():
            try:
                return self.controller.updater.prepare_install(self.release, path)
            except Exception as error:
                try:
                    self.controller.updater.abort_update()
                except Exception as cleanup:
                    error.add_note(f'清理更新状态失败：{cleanup}')
                raise
        self.controller.worker(prepare, self.install_prepared)

    def install_prepared(self, result, error):
        if error:
            self.controller.service.call('update_failed', dict(error=str(error)))
            return
        self.controller.worker(self.controller.updater.commit_install, self.install_committed)

    def install_committed(self, result, error):
        if error:
            self.abort_install(error)
            return
        self.status.set('校验通过，正在退出工具箱并自动安装；完成后将自动重新打开。')
        self._close_deadline = time.monotonic() + 30
        self.finish_update()

    def finish_update(self):
        try:
            self.controller.service.call('finish_update', {})
        except Exception as exc:
            if '等待' in str(exc) and time.monotonic() < self._close_deadline:
                self.after(200, self.finish_update)
            else:
                self.abort_install(exc)

    def abort_install(self, error):
        def finished(_result, _error):
            self.controller.service.call('update_failed', dict(error=str(error)))
        self.controller.worker(self.controller.updater.abort_update, finished)

    def update_event(self, method, params):
        if method == "update_cancelled":
            self.downloading = False
            self.destroy()
        elif method == "update_failed":
            self.downloading = self.installing = False
            self.primary.configure(state="normal", text="重试更新")
            self.cancel_button.configure(state="normal", text="稍后")
            self.status.set(params.get("error", "更新失败，请重试。"))
        elif method == "update_progress":
            stage = params.get("stage", "downloading")
            self.progress["value"] = params.get("percent", 0)
            if stage == "installing":
                self.downloading = False
                self.installing = True
                self.cancel_button.configure(state="disabled")
                self.status.set("签名校验通过，正在自动更新并重新启动…")
            else:
                self.status.set(f"正在下载及校验签名 · {params.get('percent', 0)}%")
