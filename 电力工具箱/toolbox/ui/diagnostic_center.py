from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..diagnostics import (
    DiagnosticCheck,
    DiagnosticLevel,
    DiagnosticReport,
    diagnose_environment,
    diagnose_path,
)
from ..runtime import ToolPaths


_STATUS_ICON = {
    DiagnosticLevel.OK: "✓",
    DiagnosticLevel.WARNING: "!",
    DiagnosticLevel.ERROR: "×",
}


class DiagnosticCenterPanel(ttk.Frame):
    """常驻右侧的只读运行诊断摘要，不介入任何业务脚本。"""

    def __init__(self, parent: tk.Misc, paths: ToolPaths) -> None:
        super().__init__(parent, padding=(12, 10))
        self.paths = paths
        self.report: DiagnosticReport | None = None
        self._visible_checks: list[DiagnosticCheck] = []

        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text="运行诊断", style="PageTitle.TLabel").pack(side="left")
        ttk.Button(header, text="重新检查", command=self.refresh).pack(side="right")

        self.health_var = tk.StringVar(self, "正在检查…")
        self.summary_var = tk.StringVar(self, "")
        ttk.Label(self, textvariable=self.health_var, style="DiagnosticStatus.TLabel").pack(
            anchor="w", pady=(8, 2)
        )
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(anchor="w")

        self.check_list = tk.Listbox(
            self,
            width=30,
            height=5,
            borderwidth=0,
            activestyle="none",
            exportselection=False,
        )
        self.check_list.pack(fill="x", pady=(8, 4))
        self.check_list.bind("<<ListboxSelect>>", self._show_selected)

        self.detail_var = tk.StringVar(self, "")
        self.detail_label = ttk.Label(
            self,
            textvariable=self.detail_var,
            style="Muted.TLabel",
            wraplength=250,
            justify="left",
        )
        self.detail_label.pack(fill="x", pady=(2, 8))

        actions = ttk.Frame(self)
        actions.pack(fill="x")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="检查文件…", command=self._check_file).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(actions, text="复制报告", command=self._copy_report).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        self.refresh()

    def refresh(self) -> None:
        self._render(diagnose_environment(self.paths))

    def _render(self, report: DiagnosticReport) -> None:
        self.report = report
        normal = len(report.passed)
        warnings = len(report.warnings)
        errors = len(report.errors)
        if errors:
            self.health_var.set(f"× 发现 {errors} 项异常")
        elif warnings:
            self.health_var.set(f"! 环境可用，有 {warnings} 项提醒")
        else:
            self.health_var.set("✓ 运行环境正常")
        self.summary_var.set(f"{normal} 正常 · {warnings} 提醒 · {errors} 异常  ·  {report.generated_at:%H:%M}")

        issues = [*report.errors, *report.warnings]
        self._visible_checks = issues[:5] if issues else self._normal_highlights(report)
        self.check_list.delete(0, "end")
        for check in self._visible_checks:
            self.check_list.insert("end", f"{_STATUS_ICON[check.status]}  {check.name}")

        if issues:
            self.check_list.selection_set(0)
            self._show_check(self._visible_checks[0])
        else:
            self.detail_var.set("关键运行条件均正常，不影响现有脚本使用。")

    @staticmethod
    def _normal_highlights(report: DiagnosticReport) -> list[DiagnosticCheck]:
        preferred = ("Python", "工具箱工作目录", "工作目录写入权限", "Node.js")
        highlights: list[DiagnosticCheck] = []
        for prefix in preferred:
            match = next((item for item in report.passed if item.name.startswith(prefix)), None)
            if match is not None:
                highlights.append(match)
        return highlights[:5]

    def _show_selected(self, _event=None) -> None:
        selected = self.check_list.curselection()
        if selected and selected[0] < len(self._visible_checks):
            self._show_check(self._visible_checks[selected[0]])

    def _show_check(self, check: DiagnosticCheck) -> None:
        if check.status is DiagnosticLevel.OK:
            text = check.detail
        else:
            text = check.recommendation or check.detail
        self.detail_var.set(text)

    def _check_file(self) -> None:
        selected = filedialog.askopenfilename(title="选择要检查的文件", parent=self)
        if not selected:
            return
        report = diagnose_path(selected, require_write=True)
        messagebox.showinfo("文件检查结果", report.summary(), parent=self)

    def _copy_report(self) -> None:
        if self.report is None:
            return
        self.clipboard_clear()
        self.clipboard_append(self.report.summary())
        self.update_idletasks()
        previous = self.summary_var.get()
        self.summary_var.set("诊断报告已复制")
        self.after(1800, lambda: self.summary_var.set(previous) if self.winfo_exists() else None)
