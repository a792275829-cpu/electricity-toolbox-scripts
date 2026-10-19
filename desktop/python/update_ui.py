"""Original Tk update UI: background badge, release notes, download, Installer."""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
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

    def activate(self):
        self.check()


class UpdateWindow(tk.Toplevel):
    def __init__(self, controller, release):
        super().__init__(controller.app)
        self.controller, self.release = controller, release
        self.cancel = threading.Event()
        self.downloading = self.installing = False
        self.installer = None
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
        self.status = tk.StringVar(self, "准备好后即可下载更新。")
        ttk.Label(body, textvariable=self.status, wraplength=490).grid(sticky="w", pady=(0, 16))
        actions = ttk.Frame(body)
        actions.grid(sticky="ew")
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="发布页面", command=lambda: webbrowser.open(release.page)).grid(row=0, column=0)
        self.cancel_button = ttk.Button(actions, text="稍后", command=self.dismiss)
        self.cancel_button.grid(row=0, column=2, padx=(8, 8))
        self.primary = ttk.Button(actions, text="下载更新", command=self.perform, style="Primary.TButton")
        self.primary.grid(row=0, column=3)
        if not release.asset:
            self.primary.state(["disabled"])
            self.status.set("此版本尚未提供 Apple 芯片安装包，请查看发布页面。")

    def dismiss(self):
        if self.installing:
            return
        self.cancel.set()
        if self.installer:
            shutil.rmtree(self.installer.parent, ignore_errors=True)
        self.destroy()

    def perform(self):
        if self.downloading or self.installing:
            return
        if self.installer:
            self.install()
            return
        self.cancel.clear()
        self.downloading = True
        self.primary.configure(text="下载中…", state="disabled")
        self.cancel_button.configure(text="取消下载")
        self.status.set("正在连接 GitHub…")
        def progress(fraction):
            # Coalesce progress so a fast download cannot flood the Tk queue.
            percent = int(fraction * 100)
            if percent != getattr(self, "_percent", -1):
                self._percent = percent
                self.controller.messages.put((self.progressed, percent, None))
        self._percent = -1
        self.controller.worker(lambda: self.controller.updater.download(self.release, self.cancel, progress), self.downloaded)

    def progressed(self, percent, _error):
        if self.winfo_exists():
            self.progress["value"] = percent
            self.status.set("正在校验安装包…" if percent == 100 else f"正在下载 · {percent}%")

    def downloaded(self, path, error):
        if not self.winfo_exists():
            if path:
                shutil.rmtree(path.parent, ignore_errors=True)
            return
        self.downloading = False
        self.primary.configure(state="normal", text="重新下载" if error else "安装更新")
        self.cancel_button.configure(text="稍后")
        if error:
            self.progress["value"] = 0
            self.status.set("已取消下载。" if isinstance(error, Cancelled) else str(error))
        else:
            self.installer = path
            self.status.set("校验通过。点击安装更新，在系统安装器中完成升级。")

    def install(self):
        try:
            self.controller.service.call("prepare_update", {})
            # A per-user marker lets the installer retire a moved running copy.
            current = os.environ.get("TOOLBOX_APP_BUNDLE")
            if current:
                marker = Path.home() / "Library/Application Support/com.electricity.toolbox/update-source.txt"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(current + "\n", encoding="utf-8")
                marker.chmod(0o600)
        except Exception as exc:
            self.controller.service.call("abort_update", {})
            self.status.set(str(exc))
            return
        self.installing = True
        self.primary.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.status.set("请在系统安装器中完成升级；取消安装后工具箱会恢复可用。")
        self.controller.worker(
            lambda: subprocess.run(["/usr/bin/open", "-W", "-a", "Installer", str(self.installer)], check=True),
            self.installed,
        )

    def installed(self, _result, error):
        self.controller.service.call("abort_update", {})
        self.installing = False
        if self.winfo_exists():
            self.primary.configure(state="normal")
            self.cancel_button.configure(state="normal")
            self.status.set(f"无法打开安装器：{error}" if error else "安装器已关闭。若取消了安装，可再次点击安装更新。")
