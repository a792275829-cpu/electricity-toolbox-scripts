"""Original Tk update UI with signed native download, replacement and restart."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from github_update import GitHubUpdater


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
        self.status = tk.StringVar(self, "下载完成后自动验签、更新并重新启动；有运行任务时暂停安装。")
        ttk.Label(body, textvariable=self.status, wraplength=490).grid(sticky="w", pady=(0, 16))
        actions = ttk.Frame(body)
        actions.grid(sticky="ew")
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="发布页面", command=lambda: webbrowser.open(release.page)).grid(row=0, column=0)
        self.cancel_button = ttk.Button(actions, text="稍后", command=self.dismiss)
        self.cancel_button.grid(row=0, column=2, padx=(8, 8))
        self.primary = ttk.Button(actions, text="下载并更新", command=self.perform, style="Primary.TButton")
        self.primary.grid(row=0, column=3)
        if not release.asset:
            self.primary.state(["disabled"])
            self.status.set("此版本尚未提供 Apple 芯片安装包，请查看发布页面。")

    def dismiss(self):
        if self.installing:
            return
        if self.downloading:
            self.controller.service.call("cancel_update", {})
            self.status.set("正在取消下载…")
            return
        self.destroy()

    def perform(self):
        if self.downloading or self.installing:
            return
        self.downloading = True
        self.primary.configure(text="下载中…", state="disabled")
        self.cancel_button.configure(text="取消下载")
        self.status.set("正在连接 GitHub…")
        try:
            self.controller.service.call("begin_update", {"version": self.release.version})
        except Exception as exc:
            self.update_event("update_failed", {"error": str(exc)})

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
