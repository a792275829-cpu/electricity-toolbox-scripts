from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from pathlib import Path
from tkinter import messagebox, ttk

from .runtime import TaskRegistry, ToolPaths


PAGE_NAMES = (
    "导出上网电量",
    "电力交易分析",
    "电量汇总",
    "生成报告",
    "私有数据上传",
    "上传集团每日数据",
    "市场表更新",
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
    ) -> None:
        super().__init__()
        self.title("电力工作工具箱")
        self.geometry("1100x720")
        self.minsize(960, 640)

        self.paths = ToolPaths(workspace or default_workspace())
        self.registry = TaskRegistry()
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_page = ""
        self.status_var = tk.StringVar(master=self, value="就绪")

        self._configure_style()
        self._build_shell(page_factories or self._default_page_factories())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.configure("AppTitle.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("PageTitle.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Muted.TLabel", foreground="#5f6b7a")
        style.configure("Nav.TButton", anchor="w", padding=(14, 11))
        style.configure(
            "NavSelected.TButton",
            anchor="w",
            padding=(14, 11),
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    @staticmethod
    def _default_page_factories() -> Mapping[str, PageFactory]:
        from .pages import page_factories

        return page_factories()

    def _build_shell(self, factories: Mapping[str, PageFactory]) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, padding=(14, 18))
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="电力工作工具箱", style="AppTitle.TLabel").pack(
            anchor="w", pady=(0, 18)
        )

        page_host = ttk.Frame(self)
        page_host.grid(row=0, column=1, sticky="nsew")
        page_host.rowconfigure(0, weight=1)
        page_host.columnconfigure(0, weight=1)

        for name in PAGE_NAMES:
            if name not in factories:
                raise KeyError(f"缺少页面工厂：{name}")
            button = ttk.Button(
                sidebar,
                text=name,
                style="Nav.TButton",
                command=lambda page_name=name: self.show_page(page_name),
                width=22,
            )
            button.pack(fill="x", pady=2)
            self.nav_buttons[name] = button

            page = factories[name](page_host, self.paths, self.registry)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page

        status = ttk.Label(
            self,
            textvariable=self.status_var,
            anchor="w",
            padding=(12, 5),
            relief="sunken",
        )
        status.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.show_page(PAGE_NAMES[0])

    def show_page(self, name: str) -> None:
        if name not in self.pages:
            raise KeyError(f"未知页面：{name}")
        self.pages[name].tkraise()
        self.current_page = name
        self.status_var.set(f"当前功能：{name}")
        for page_name, button in self.nav_buttons.items():
            button.configure(
                style="NavSelected.TButton" if page_name == name else "Nav.TButton"
            )

    def _on_close(self) -> None:
        if self.registry.has_running_tasks():
            confirmed = messagebox.askyesno(
                "仍有任务运行",
                "关闭工具箱将终止由工具箱启动的任务，确定关闭吗？",
                parent=self,
            )
            if not confirmed:
                return
        self.registry.terminate_all()
        self.destroy()


def main() -> int:
    app = ToolboxApp()
    app.mainloop()
    return 0
