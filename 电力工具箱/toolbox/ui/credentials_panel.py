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

    def __init__(self, parent: tk.Misc, workspace: Path, *, auto_verify: bool = True) -> None:
        super().__init__(parent, padding=(12, 10))
        self.store = CredentialStore(workspace)
        self.dirty = False
        self._closed = False
        self._startup_id = None
        self._edit_generation = 0
        self._verification_generation = None
        self._verified_pair = None
        self._verified_revision = None
        self.status_kind = "neutral"
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
        self.retry_button = ttk.Button(self, text="重试验证", command=self.retry_saved)
        self.manual_login_button = ttk.Button(self, text="打开登录窗口")
        self.status_label = ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", wraplength=250)
        self._status_trace = self.status_var.trace_add("write", self._update_status_visibility)
        saved = {}
        try:
            saved = self.store.load()
            self.username_var.set(saved.get("username", ""))
            self.password_var.set(saved.get("password", ""))
            self.status_var.set(self._saved_status(saved) if saved else "填写账号密码后保存")
        except (ValueError, OSError):
            self._set_status("读取失败，请检查本机账号配置文件", "error")
        self._traces = [
            (variable, variable.trace_add("write", self._edited))
            for variable in (self.username_var, self.password_var)
        ]
        for entry in (self.username_entry, self.password_entry):
            entry.bind("<Return>", lambda _event: self.save())
        if auto_verify and saved.get("username") and saved.get("password"):
            self._set_status("等待验证账号…")
            self._startup_id = self.after(300, self.retry_saved)

    STATUS_COLORS = {"neutral": "#666666", "pending": "#666666", "valid": "#247a3c",
                     "error": "#b42318", "manual": "#946200"}

    def _set_status(self, text: str, kind: str = "neutral", *, retry: bool = False) -> None:
        self.status_kind = kind
        self.status_var.set(text)
        if retry:
            self.retry_button.pack(fill="x", pady=(4, 0))
        else:
            self.retry_button.pack_forget()

    def _update_status_visibility(self, *_args) -> None:
        self.status_label.configure(foreground=self.STATUS_COLORS[self.status_kind])
        if self.status_var.get():
            self.status_label.pack(fill="x", pady=(4, 0))
        else:
            self.status_label.pack_forget()

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "•")

    def _edited(self, *_args) -> None:
        self.dirty = True
        self._edit_generation += 1
        self._verified_pair = None
        self._verified_revision = None
        self.manual_login_button.pack_forget()
        self._set_status("尚未保存；保存或启动任务时提交")

    def _is_verified(self, saved) -> bool:
        return self._verified_pair == (saved.get("username"), saved.get("password")) and self._verified_revision == saved.get("revision", "")

    def _saved_status(self, saved) -> str:
        if self._is_verified(saved):
            return "✓ 账号有效"
        return "已保存到本机 · 未验证"

    def _cancel_startup_check(self) -> None:
        if self._startup_id is not None:
            self.after_cancel(self._startup_id)
            self._startup_id = None

    def save_if_dirty(self) -> bool:
        return self.save() if self.dirty else True

    def save(self) -> bool:
        if not self.username_var.get().strip() or not self.password_var.get():
            self._set_status("账号和密码需填写完整，尚未保存", "error")
            return False
        try:
            saved = self.store.save(self.username_var.get(), self.password_var.get())
        except ValueError:
            self._set_status("账号配置异常，请检查本机配置文件", "error")
            return False
        except OSError:
            self._set_status("保存失败，请检查本机文件写入权限", "error")
            return False
        valid = self._is_verified(saved)
        self._set_status(self._saved_status(saved), "valid" if valid else "neutral", retry=not valid)
        self.dirty = False
        return True

    def retry_saved(self) -> None:
        """Startup/retry checks only the saved pair and never writes account metadata."""
        self._cancel_startup_check()
        if self._closed or self._verification_id is not None:
            return
        if self.dirty:
            self._set_status("草稿尚未保存，请保存或验证并保存")
            return
        try:
            saved = self.store.load()
        except (ValueError, OSError):
            self._set_status("读取账号配置失败", "error", retry=True)
            return
        candidate = (saved.get("username", ""), saved.get("password", ""))
        if not all(candidate):
            self._set_status("填写账号密码后保存")
            return
        self._begin_verification(candidate, saved.get("revision", ""), save_on_success=False)

    def verify(self) -> None:
        self._cancel_startup_check()
        if self._closed or self._verification_id is not None:
            return
        candidate = (self.username_var.get().strip(), self.password_var.get())
        if not all(candidate):
            self._set_status("账号和密码需填写完整，尚未保存", "error")
            return
        try:
            saved = self.store.load()
        except (ValueError, OSError):
            self._set_status("读取账号配置失败", "error")
            return
        self._begin_verification(candidate, saved.get("revision", ""), save_on_success=True)

    def _begin_verification(self, candidate, revision, *, save_on_success: bool) -> None:
        self._verification_generation = self._edit_generation
        self._verified_pair = None
        self._verified_revision = None
        self.manual_login_button.pack_forget()
        self.verify_button.configure(state="disabled")
        self._set_status("正在验证账号…", "pending")
        result_queue = self._verification_queue

        def worker():
            try:
                verify_credentials(*candidate)
            except LoginError as exc:
                result = (str(exc), exc.kind)
            except Exception:
                result = ("验证失败", "unknown")
            else:
                result = None
            result_queue.put((candidate, revision, result, save_on_success))

        self._verification_id = self.after(100, self._poll_verification)
        threading.Thread(target=worker, daemon=True, name="qingneng-account-verify").start()

    def _poll_verification(self) -> None:
        self._verification_id = None
        if self._closed:
            return
        try:
            candidate, revision, error, save_on_success = self._verification_queue.get_nowait()
        except queue.Empty:
            self._verification_id = self.after(100, self._poll_verification)
            return
        self.verify_button.configure(state="normal")
        try:
            saved = self.store.load()
            changed = saved.get("revision", "") != revision and (saved.get("username"), saved.get("password")) != candidate
            edited = self._verification_generation is not None and self._edit_generation != self._verification_generation
            if edited or (self.username_var.get().strip(), self.password_var.get()) != candidate or changed:
                self._set_status("账号已修改，请保存后重新验证")
            elif error:
                kind = error[1]
                messages = {"credentials": "✗ 账号或密码错误", "network": "✗ 连接失败，请重试",
                            "server": "✗ 登录服务暂不可用，请重试", "verification": "⚠ 需要人工验证"}
                self._set_status(messages.get(kind, "✗ 暂未确认账号有效，请重试"),
                                 "manual" if kind == "verification" else "error", retry=kind != "verification")
                if kind == "verification":
                    self.offer_manual_login()
            else:
                if save_on_success:
                    saved = self.store.save(*candidate)
                    if not self.store.mark_verified(saved["revision"]):
                        self._set_status("账号已修改，请重新验证")
                        return
                    self.dirty = False
                self._verified_pair = candidate
                self._verified_revision = saved.get("revision", "")
                self._set_status("✓ 账号有效", "valid")
        except (ValueError, OSError):
            self._set_status("读取或保存验证结果失败，请重试", "error", retry=True)
        finally:
            self._verification_generation = None

    def _login_current_tool(self) -> None:
        page = getattr(self.winfo_toplevel(), "_visible_page", None)
        from toolbox_shared.login_tools import STATE_DIRS
        if getattr(page, "tool_id", None) in {*STATE_DIRS, "summary-upload", "group-upload"}:
            callback = getattr(page, "start_login", None)
            if callback is not None:
                callback()
                return
        self._set_status("请先切换到需要登录的清能工具，再打开登录窗口", "manual")

    def offer_manual_login(self, callback=None) -> None:
        self.manual_login_button.configure(command=callback or self._login_current_tool)
        self.manual_login_button.pack(fill="x", pady=(4, 0))

    def destroy(self) -> None:
        self._closed = True
        self._cancel_startup_check()
        if self._verification_id is not None:
            self.after_cancel(self._verification_id)
            self._verification_id = None
        self.status_var.trace_remove("write", self._status_trace)
        for variable, trace in self._traces:
            variable.trace_remove("write", trace)
        super().destroy()
