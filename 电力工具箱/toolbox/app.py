from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from pathlib import Path
from tkinter import messagebox, ttk

from .catalog import catalog_factories, default_catalog
from .runtime import TaskRegistry, ToolPaths
from .ui import DashboardPage, TaskCenter, configure_theme

PAGE_NAMES = (
    "导出上网电量",
    "电力交易分析",
    "电量汇总",
    "生成报告",
    "私有数据上传",
    "上传集团每日数据",
    "市场表更新",
    "广东电价预测",
    "WPS写入工具",
)
PageFactory = Callable[[tk.Misc, ToolPaths, TaskRegistry], tk.Frame]


def default_workspace() -> Path:
    return Path(__file__).resolve().parents[2]


class ToolboxApp(tk.Tk):
    def __init__(
        self,
        *,
        workspace: Path | None = None,
        page_factories: Mapping[str, PageFactory] | None = None,
        lazy_pages: bool | None = None,
    ) -> None:
        super().__init__()
        self.title("电力工作工具箱")
        self.geometry("1240x760")
        self.minsize(980, 640)
        self.paths = ToolPaths(workspace or default_workspace())
        self.registry = TaskRegistry()
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_page = ""
        self.status_var = tk.StringVar(master=self, value="就绪")
        injected = page_factories is not None
        self._factories = dict(page_factories or catalog_factories())
        self._lazy_pages = (not injected) if lazy_pages is None else lazy_pages
        self._configure_style()
        self._build_shell()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        configure_theme(ttk.Style(self))

    @staticmethod
    def _default_page_factories() -> Mapping[str, PageFactory]:
        return catalog_factories()

    def _build_shell(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self, padding=(12, 18))
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="电力工作台", style="AppTitle.TLabel").pack(anchor="w", pady=(0, 14))
        dashboard_button = ttk.Button(sidebar, text="今日概览", style="Nav.TButton", command=lambda: self.show_page("今日概览"), width=22)
        dashboard_button.pack(fill="x", pady=(0, 10))
        self.nav_buttons["今日概览"] = dashboard_button

        last_category = None
        for descriptor in default_catalog():
            name = descriptor.name
            if name not in self._factories:
                raise KeyError(f"缺少页面工厂：{name}")
            category = descriptor.category
            if category != last_category:
                ttk.Label(sidebar, text=category, style="Section.TLabel").pack(anchor="w", padx=8, pady=(10, 4))
                last_category = category
            button = ttk.Button(sidebar, text=name, style="Nav.TButton", command=lambda value=name: self.show_page(value), width=22)
            button.pack(fill="x", pady=1)
            self.nav_buttons[name] = button

        self.page_host = ttk.Frame(self)
        self.page_host.grid(row=0, column=1, sticky="nsew")
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)
        self.dashboard = DashboardPage(self.page_host, self.registry, self.show_page, self.paths)
        self.dashboard.grid(row=0, column=0, sticky="nsew")

        self.task_center = TaskCenter(self, self.registry)
        self.task_center.grid(row=0, column=2, sticky="nsew")
        if not self._lazy_pages:
            for name in PAGE_NAMES:
                self._create_page(name)

        ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(12, 5), relief="sunken").grid(row=1, column=0, columnspan=3, sticky="ew")
        self.show_page("今日概览" if self._lazy_pages else PAGE_NAMES[0])

    def _create_page(self, name: str) -> tk.Frame:
        page = self.pages.get(name)
        if page is None:
            page = self._factories[name](self.page_host, self.paths, self.registry)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        return page

    def show_page(self, name: str) -> None:
        if name == "今日概览":
            page = self.dashboard
        elif name in self._factories:
            page = self._create_page(name)
        else:
            raise KeyError(f"未知页面：{name}")
        page.tkraise()
        self.current_page = name
        self.status_var.set(f"当前功能：{name}")
        for page_name, button in self.nav_buttons.items():
            button.configure(style="NavSelected.TButton" if page_name == name else "Nav.TButton")

    def _on_close(self) -> None:
        active = self.registry.active_snapshots()
        if active or self.registry.has_running_tasks():
            names = "\n".join(f"• {task.name}" for task in active) or "• 后台任务"
            if not messagebox.askyesno("仍有任务运行", f"以下任务仍在运行：\n\n{names}\n\n关闭工具箱将取消这些任务，确定关闭吗？", parent=self):
                return
        remaining = self.registry.terminate_all()
        if remaining:
            messagebox.showwarning("无法立即关闭", "仍有不可安全中断的写入任务。请等待任务结束后再关闭。", parent=self)
            return
        self.destroy()


def main() -> int:
    app = ToolboxApp()
    app.mainloop()
    return 0
