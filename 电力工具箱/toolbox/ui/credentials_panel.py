from __future__ import annotations

import sys
import tkinter as tk

from pathlib import Path
from tkinter import ttk

WORKSPACE = Path(__file__).resolve().parents[3]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
from toolbox_credentials import CredentialStore
from toolbox_choices import SettingCheckbutton


class CredentialsPanel(ttk.Frame):
    """Always-visible account editor; save complete edits after a short debounce."""

    def __init__(self, parent: tk.Misc, workspace: Path) -> None:
        super().__init__(parent, padding=(12, 10))
        self.store = CredentialStore(workspace)
        self._save_id: str | None = None
        self.dirty = False
        self.username_var = tk.StringVar(self)
        self.password_var = tk.StringVar(self)
        self.show_password_var = tk.BooleanVar(self, True)
        self.status_var = tk.StringVar(self)
        ttk.Label(self, text="清能网站账号", style="PanelTitle.TLabel").pack(anchor="w")
        fields = ttk.Frame(self)
        fields.pack(fill="x", pady=(8, 4))
        fields.columnconfigure(1, weight=1)
        ttk.Label(fields, text="账号").grid(row=0, column=0, padx=(0, 8), pady=(0, 5))
        self.username_entry = ttk.Entry(fields, textvariable=self.username_var, width=22)
        self.username_entry.grid(row=0, column=1, sticky="ew", pady=(0, 5))
        ttk.Label(fields, text="密码").grid(row=1, column=0, padx=(0, 8))
        self.password_entry = ttk.Entry(fields, textvariable=self.password_var, width=22)
        self.password_entry.grid(row=1, column=1, sticky="ew")
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(2, 0))
        SettingCheckbutton(actions, text="显示密码", variable=self.show_password_var, command=self._toggle_password).pack(side="left")
        ttk.Button(actions, text="保存", command=self.save).pack(side="right")
        self.status_label = ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", wraplength=250)
        self._status_trace = self.status_var.trace_add("write", self._update_status_visibility)
        try:
            saved = self.store.load()
            self.username_var.set(saved.get("username", ""))
            self.password_var.set(saved.get("password", ""))
            self.status_var.set("已保存到本机" if saved else "填写账号密码后自动保存")
        except (ValueError, OSError):
            self.status_var.set("读取失败，请检查本机账号配置文件")
        self._traces = [
            (variable, variable.trace_add("write", self._edited))
            for variable in (self.username_var, self.password_var)
        ]
        for entry in (self.username_entry, self.password_entry):
            entry.bind("<FocusOut>", lambda _event: self.save())
            entry.bind("<Return>", lambda _event: self.save())

    def _update_status_visibility(self, *_args) -> None:
        # Routine save messages take no space; keep actionable failures visible.
        if any(word in self.status_var.get() for word in ("失败", "异常", "尚未保存")):
            self.status_label.pack(fill="x", pady=(4, 0))
        else:
            self.status_label.pack_forget()

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "•")

    def _edited(self, *_args) -> None:
        self.dirty = True
        if self._save_id is not None:
            self.after_cancel(self._save_id)
        self.status_var.set("等待自动保存…")
        self._save_id = self.after(600, self.save)

    def save(self) -> bool:
        if self._save_id is not None:
            self.after_cancel(self._save_id)
            self._save_id = None
        if not self.username_var.get().strip() or not self.password_var.get():
            self.status_var.set("账号和密码需填写完整，尚未保存")
            return False
        try:
            self.store.save(self.username_var.get(), self.password_var.get())
        except ValueError:
            self.status_var.set("账号配置异常，请检查本机配置文件")
            return False
        except OSError:
            self.status_var.set("保存失败，请检查本机文件写入权限")
            return False
        self.status_var.set("已保存到本机")
        self.dirty = False
        return True

    def destroy(self) -> None:
        if self._save_id is not None:
            self.after_cancel(self._save_id)
        self.status_var.trace_remove("write", self._status_trace)
        for variable, trace in self._traces:
            variable.trace_remove("write", trace)
        super().destroy()
