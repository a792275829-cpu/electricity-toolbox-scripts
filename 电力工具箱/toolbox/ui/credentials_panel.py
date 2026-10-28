from __future__ import annotations

import tkinter as tk
import queue
import threading

from pathlib import Path
from tkinter import ttk

from toolbox_shared.credentials import CredentialStore
from toolbox_shared.choices import SettingCheckbutton
from toolbox_shared.qingneng_login import LoginError, verify_credentials


class CredentialsPanel(ttk.Frame):
    """Keep edits as a draft until save, verification, or task launch."""

    def __init__(self, parent: tk.Misc, workspace: Path) -> None:
        super().__init__(parent, padding=(12, 10))
        self.store = CredentialStore(workspace)
        self.dirty = False
        self._verification_queue = queue.Queue()
        self._verification_id = None
        self.username_var = tk.StringVar(self)
        self.password_var = tk.StringVar(self)
        self.show_password_var = tk.BooleanVar(self, False)
        self.status_var = tk.StringVar(self)
        ttk.Label(self, text="清能网站账号", style="PanelTitle.TLabel").pack(anchor="w")
        fields = ttk.Frame(self)
        fields.pack(fill="x", pady=(8, 4))
        fields.columnconfigure(1, weight=1)
        ttk.Label(fields, text="账号").grid(row=0, column=0, padx=(0, 8), pady=(0, 5))
        self.username_entry = ttk.Entry(fields, textvariable=self.username_var, width=22)
        self.username_entry.grid(row=0, column=1, sticky="ew", pady=(0, 5))
        ttk.Label(fields, text="密码").grid(row=1, column=0, padx=(0, 8))
        self.password_entry = ttk.Entry(fields, textvariable=self.password_var, width=22, show="•")
        self.password_entry.grid(row=1, column=1, sticky="ew")
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(2, 0))
        SettingCheckbutton(actions, text="显示密码", variable=self.show_password_var, command=self._toggle_password).pack(side="left")
        ttk.Button(actions, text="保存", command=self.save).pack(side="right")
        self.verify_button = ttk.Button(actions, text="验证并保存", command=self.verify)
        self.verify_button.pack(side="right", padx=(4, 4))
        self.manual_login_button = ttk.Button(self, text="打开登录窗口")
        self.status_label = ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", wraplength=250)
        self._status_trace = self.status_var.trace_add("write", self._update_status_visibility)
        try:
            saved = self.store.load()
            self.username_var.set(saved.get("username", ""))
            self.password_var.set(saved.get("password", ""))
            self.status_var.set(self._saved_status(saved) if saved else "填写账号密码后保存")
        except (ValueError, OSError):
            self.status_var.set("读取失败，请检查本机账号配置文件")
        self._traces = [
            (variable, variable.trace_add("write", self._edited))
            for variable in (self.username_var, self.password_var)
        ]
        for entry in (self.username_entry, self.password_entry):
            entry.bind("<Return>", lambda _event: self.save())

    def _update_status_visibility(self, *_args) -> None:
        if self.status_var.get():
            self.status_label.pack(fill="x", pady=(4, 0))
        else:
            self.status_label.pack_forget()

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "•")

    def _edited(self, *_args) -> None:
        self.dirty = True
        self.manual_login_button.pack_forget()
        self.status_var.set("尚未保存；保存或启动任务时提交")

    @staticmethod
    def _saved_status(saved) -> str:
        return "账号已验证（上次验证通过）" if saved.get("verified_at") else "已保存到本机 · 未验证"

    def save_if_dirty(self) -> bool:
        return self.save() if self.dirty else True

    def save(self) -> bool:
        if not self.username_var.get().strip() or not self.password_var.get():
            self.status_var.set("账号和密码需填写完整，尚未保存")
            return False
        try:
            saved = self.store.save(self.username_var.get(), self.password_var.get())
        except ValueError:
            self.status_var.set("账号配置异常，请检查本机配置文件")
            return False
        except OSError:
            self.status_var.set("保存失败，请检查本机文件写入权限")
            return False
        self.status_var.set(self._saved_status(saved))
        self.dirty = False
        return True

    def verify(self) -> None:
        if self._verification_id is not None:
            return
        candidate = (self.username_var.get().strip(), self.password_var.get())
        if not all(candidate):
            self.status_var.set("账号和密码需填写完整，尚未保存")
            return
        try:
            saved = self.store.load()
        except (ValueError, OSError):
            self.status_var.set("读取账号配置失败")
            return
        revision = saved.get("revision", "")
        self.manual_login_button.pack_forget()
        self.verify_button.configure(state="disabled")
        self.status_var.set("正在验证账号…")

        def worker():
            try:
                verify_credentials(*candidate)
            except LoginError as exc:
                result = (str(exc), exc.kind)
            except Exception:
                result = ("验证失败，请检查网络及本机浏览器依赖", "unknown")
            else:
                result = None
            self._verification_queue.put((candidate, revision, result))

        threading.Thread(target=worker, daemon=True, name="qingneng-account-verify").start()
        self._verification_id = self.after(100, self._poll_verification)

    def _poll_verification(self) -> None:
        self._verification_id = None
        try:
            candidate, revision, error = self._verification_queue.get_nowait()
        except queue.Empty:
            self._verification_id = self.after(100, self._poll_verification)
            return
        self.verify_button.configure(state="normal")
        try:
            saved = self.store.load()
            changed = saved.get("revision", "") != revision and (saved.get("username"), saved.get("password")) != candidate
            if (self.username_var.get().strip(), self.password_var.get()) != candidate or changed:
                self.status_var.set("账号已修改，请保存后重新验证")
            elif error:
                self.status_var.set(error[0])
                if error[1] == "verification":
                    self.offer_manual_login()
            else:
                saved = self.store.save(*candidate)
                if self.store.mark_verified(saved["revision"]):
                    self.dirty = False
                    self.status_var.set("账号已验证并保存")
                else:
                    self.status_var.set("账号已修改，请重新验证")
        except (ValueError, OSError):
            self.status_var.set("保存验证结果失败，请重新验证")

    def offer_manual_login(self, callback=None) -> None:
        if callback is None:
            page = getattr(self.winfo_toplevel(), "_visible_page", None)
            callback = getattr(page, "start_login", None)
        if callback is not None:
            self.manual_login_button.configure(command=callback)
            self.manual_login_button.pack(fill="x", pady=(4, 0))

    def destroy(self) -> None:
        if self._verification_id is not None:
            self.after_cancel(self._verification_id)
        self.status_var.trace_remove("write", self._status_trace)
        for variable, trace in self._traces:
            variable.trace_remove("write", trace)
        super().destroy()
