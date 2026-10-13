from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable, Mapping
from pathlib import Path
from tkinter import messagebox, ttk

from .catalog import catalog_factories, default_catalog
from .runtime import TaskRegistry, ToolPaths
from .ui import DashboardPage, DiagnosticCenterPanel, TaskCenter, configure_theme
from .ui.credentials_panel import CredentialsPanel
from .ui.theme import SIDEBAR

PAGE_NAMES = (
    "导出上网电量",
    "风力预测截图",
    "出力受限分析",
    "500kV断面分析",
    "广东运行日报解析上传",
    "南方分公司电力市场日报",
    "出清结果汇总上传",
    "早报生成",
    "上传集团每日数据",
    "市场表更新",
    "现货价格分析表更新",
    "交易系统信息更新",
    "功率调整收益统计表更新",
    "广东电价预测",
    "WPS写入工具",
)
PageFactory = Callable[[tk.Misc, ToolPaths, TaskRegistry], tk.Frame]

DEFAULT_WINDOW_WIDTH = 1440
DEFAULT_WINDOW_HEIGHT = 820
SCREEN_EDGE_MARGIN = 80


def default_workspace() -> Path:
    return Path(__file__).resolve().parents[2]


class ToolboxApp(tk.Tk):
    def __init__(
        self,
        *,
        workspace: Path | None = None,
        page_factories: Mapping[str, PageFactory] | None = None,
        lazy_pages: bool | None = None,
        render_prototype: bool = False,
        check_updates: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.title("电力工作工具箱")
        self.paths = ToolPaths(workspace or default_workspace())
        self._check_updates = check_updates
        # One shared asset serves the original Tk UI and both desktop targets.
        icon = Path(__file__).resolve().parents[2] / "desktop/assets/app-icon-256.png"
        if icon.is_file():
            self._application_icon = tk.PhotoImage(master=self, file=str(icon))
            self.iconphoto(True, self._application_icon)
        self.registry = TaskRegistry()
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_page = ""
        self.status_var = tk.StringVar(master=self, value="就绪")
        self._warmup_after_id: str | None = None
        injected = page_factories is not None
        self._factories = dict(page_factories or catalog_factories())
        self._lazy_pages = (not injected) if lazy_pages is None else lazy_pages
        self._toolbox_collect_surface_specs = render_prototype
        self._configure_style()
        self.surface_cache = None
        if render_prototype:
            from .ui.surface_cache import SurfaceCache
            self.surface_cache = SurfaceCache(self)
            self.surface_cache.prepare_styles({"Nav.TButton": 2, "NavSelected.TButton": 2,
                                               "TLabelframe": 2, "TEntry": 3,
                                               "TButton": 6, "Primary.TButton": 2})
        self._build_shell()
        if self.surface_cache is not None:
            for button in self.nav_buttons.values():
                self.surface_cache.track(button)
        self._fit_initial_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._lazy_pages and not injected:
            # Warm only the expensive embedded module once the shell is visible.
            # Its Python imports run in a worker, and the cached page stays hidden.
            self._warmup_after_id = self.after(300, self._warm_heavy_page)

    def _configure_style(self) -> None:
        configure_theme(ttk.Style(self))

    @staticmethod
    def _default_page_factories() -> Mapping[str, PageFactory]:
        return catalog_factories()

    def _build_shell(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self, padding=(14, 18), style="Sidebar.TFrame")
        self.sidebar = sidebar
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="电力工作台", style="AppTitle.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="工作空间", style="SidebarMuted.TLabel").pack(anchor="w", pady=(5, 20))
        dashboard_button = ttk.Button(sidebar, text="今日概览", style="Nav.TButton", command=lambda: self.show_page("今日概览"), width=22)
        dashboard_button.pack(fill="x", pady=(0, 5))
        self.nav_buttons["今日概览"] = dashboard_button

        search_header = ttk.Frame(sidebar, style="Sidebar.TFrame")
        search_header.pack(fill="x", pady=(10, 4))
        ttk.Label(search_header, text="搜索模块", style="SidebarMuted.TLabel").pack(side="left")
        ttk.Label(search_header, text="⌘ F", style="SidebarMuted.TLabel").pack(side="right")
        self.search_var = tk.StringVar(self)
        self.search_entry = ttk.Entry(sidebar, textvariable=self.search_var, width=22, style="Search.TEntry")
        self.search_entry.pack(fill="x", pady=(0, 6))
        self.search_entry.bind("<Escape>", lambda _event: self.search_var.set(""))
        self.search_entry.bind("<Return>", self._open_search_result)
        self.bind("<Command-f>", self._focus_search)
        self.bind("<Control-f>", self._focus_search)
        nav_host = ttk.Frame(sidebar, style="Sidebar.TFrame")
        nav_host.pack(fill="both", expand=True)
        self.nav_canvas = tk.Canvas(nav_host, background=SIDEBAR, highlightthickness=0, width=1, height=1)
        self.nav_canvas.pack(side="left", fill="both", expand=True)
        self.nav_scrollbar = ttk.Scrollbar(nav_host, orient="vertical", command=self.nav_canvas.yview)
        self.nav_canvas.configure(yscrollcommand=self.nav_scrollbar.set)
        self.navigation = ttk.Frame(self.nav_canvas, style="Sidebar.TFrame")
        self._nav_window = self.nav_canvas.create_window(0, 0, window=self.navigation, anchor="nw")
        self.nav_canvas.bind("<Configure>", self._resize_navigation)
        self.navigation.bind("<Configure>", self._resize_navigation)
        self._nav_groups = []

        last_category = None
        for descriptor in default_catalog():
            name = descriptor.name
            if name not in self._factories:
                raise KeyError(f"缺少页面工厂：{name}")
            category = descriptor.category
            if category != last_category:
                label = ttk.Label(self.navigation, text=category, style="Section.TLabel")
                label.pack(anchor="w", padx=10, pady=(12, 4))
                self._nav_groups.append((label, []))
                last_category = category
            button = ttk.Button(self.navigation, text=name, style="Nav.TButton", command=lambda value=name: self.show_page(value), width=22)
            button.pack(fill="x", pady=1)
            self.nav_buttons[name] = button
            self._nav_groups[-1][1].append((descriptor, button))
            button.bind("<MouseWheel>", self._scroll_navigation)
        self.nav_canvas.bind("<MouseWheel>", self._scroll_navigation)
        self.navigation.bind("<MouseWheel>", self._scroll_navigation)
        for label, _items in self._nav_groups:
            label.bind("<MouseWheel>", self._scroll_navigation)
        self.search_empty = ttk.Label(self.navigation, text="没有匹配的模块", style="SidebarMuted.TLabel")
        self._search_trace = self.search_var.trace_add("write", self._filter_navigation)

        self.page_host = ttk.Frame(self)
        self.page_host.grid(row=0, column=1, sticky="nsew")
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)
        self.dashboard = DashboardPage(self.page_host, self.registry, self.show_page, self.paths)
        self.dashboard.grid(row=0, column=0, sticky="nsew")
        self.dashboard.grid_remove()

        right_rail = ttk.Frame(self)
        self.right_rail = right_rail
        self.right_rail_visible = True
        right_rail.grid(row=0, column=2, sticky="nsew")
        right_rail.columnconfigure(0, weight=1)
        right_rail.rowconfigure(0, weight=1)
        self.task_center = TaskCenter(right_rail, self.registry)
        self.task_center.grid(row=0, column=0, sticky="nsew")
        ttk.Separator(right_rail, orient="horizontal").grid(row=1, column=0, sticky="ew")
        self.credentials_panel = CredentialsPanel(right_rail, self.paths.workspace)
        self.credentials_panel.grid(row=2, column=0, sticky="ew")
        ttk.Separator(right_rail, orient="horizontal").grid(row=3, column=0, sticky="ew")
        self.diagnostic_center = DiagnosticCenterPanel(right_rail, self.paths)
        self.diagnostic_center.grid(row=4, column=0, sticky="ew")
        if not self._lazy_pages:
            for name in PAGE_NAMES:
                self._create_page(name)

        status = ttk.Frame(self, style="Status.TFrame", padding=(14, 3))
        status.grid(row=1, column=0, columnspan=3, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status_bar = ttk.Label(status, textvariable=self.status_var, anchor="w", style="Status.TLabel")
        self.status_bar.grid(row=0, column=0, sticky="ew")
        self.rail_toggle = ttk.Button(status, text="收起辅助面板", command=self.toggle_right_rail, style="Quiet.TButton")
        self.rail_toggle.grid(row=0, column=1, sticky="e")
        if self._check_updates is not None:
            self.update_button = ttk.Button(status, text="检查更新", command=self._check_updates,
                                            style="Quiet.TButton")
            self.update_button.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.show_page("今日概览" if self._lazy_pages else PAGE_NAMES[0])

    def _focus_search(self, _event=None) -> str:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _resize_navigation(self, _event=None) -> None:
        width = self.nav_canvas.winfo_width()
        if int(float(self.nav_canvas.itemcget(self._nav_window, "width"))) != width:
            self.nav_canvas.itemconfigure(self._nav_window, width=width)
        region = self.nav_canvas.bbox("all")
        if region != getattr(self, "_nav_scrollregion", None):
            self.nav_canvas.configure(scrollregion=region)
            self._nav_scrollregion = region
        if self.navigation.winfo_reqheight() > self.nav_canvas.winfo_height():
            if not self.nav_scrollbar.winfo_manager():
                self.nav_scrollbar.pack(side="right", fill="y")
        else:
            if self.nav_scrollbar.winfo_manager():
                self.nav_scrollbar.pack_forget()
            if self.nav_canvas.yview()[0] != 0:
                self.nav_canvas.yview_moveto(0)

    def _scroll_navigation(self, event) -> str:
        if self.navigation.winfo_reqheight() > self.nav_canvas.winfo_height():
            # macOS trackpads emit small deltas; other platforms commonly use 120.
            delta = int(event.delta)
            units = -int(delta / 120) if abs(delta) >= 120 else -delta
            self.nav_canvas.yview_scroll(units, "units")
        return "break"

    def _filter_navigation(self, *_args) -> None:
        query = self.search_var.get().strip().casefold()
        self.search_empty.pack_forget()
        visible_count = 0
        for label, items in self._nav_groups:
            label.pack_forget()
            for _descriptor, button in items:
                button.pack_forget()
        for label, items in self._nav_groups:
            matches = [(descriptor, button) for descriptor, button in items
                       if query in f"{descriptor.name} {descriptor.description} {descriptor.category}".casefold()]
            if matches:
                label.pack(anchor="w", padx=10, pady=(12, 4))
            for _descriptor, button in matches:
                button.pack(fill="x", pady=1)
                visible_count += 1
        if not visible_count:
            self.search_empty.pack(anchor="w", padx=10, pady=12)
        self.nav_canvas.yview_moveto(0)

    def _open_search_result(self, _event=None) -> str:
        for _label, items in self._nav_groups:
            for descriptor, button in items:
                if button.winfo_manager():
                    self.show_page(descriptor.name)
                    return "break"
        return "break"

    def toggle_right_rail(self) -> None:
        self.right_rail_visible = not self.right_rail_visible
        if self.right_rail_visible:
            self.right_rail.grid()
        else:
            self.right_rail.grid_remove()
        self.rail_toggle.configure(text="收起辅助面板" if self.right_rail_visible else "展开辅助面板")

    def _fit_initial_window(self) -> None:
        """Size the first window to its content without exceeding the display."""
        self.update_idletasks()
        available_width = max(720, self.winfo_screenwidth() - SCREEN_EDGE_MARGIN)
        available_height = max(600, self.winfo_screenheight() - SCREEN_EDGE_MARGIN)
        required_height = self.sidebar.winfo_reqheight() + self.status_bar.master.winfo_reqheight()
        width = min(DEFAULT_WINDOW_WIDTH, available_width)
        height = min(max(DEFAULT_WINDOW_HEIGHT, required_height), available_height)
        self.geometry(f"{width}x{height}")
        self.minsize(min(980, width), min(640, height))

    def _create_page(self, name: str) -> tk.Frame:
        page = self.pages.get(name)
        if page is None:
            page = self._factories[name](self.page_host, self.paths, self.registry)
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            self.pages[name] = page
            if self.surface_cache is not None and name == "出清结果汇总上传":
                self.surface_cache.track_tree(page)
        return page

    def _warm_heavy_page(self) -> None:
        if self._warmup_after_id is not None:
            self.after_cancel(self._warmup_after_id)
            self._warmup_after_id = None
        self._create_page("WPS写入工具")

    def show_page(self, name: str) -> None:
        if name and name == self.current_page:
            return
        if name == "今日概览":
            page = self.dashboard
        elif name in self._factories:
            page = self._create_page(name)
        else:
            raise KeyError(f"未知页面：{name}")
        previous = self.current_page
        previous_page = self.dashboard if previous == "今日概览" else self.pages.get(previous)
        if previous_page is not None:
            previous_page.grid_remove()
        # Retain each page and its background task, but only map the visible
        # page. Stacked mapped pages otherwise repaint together on macOS.
        page.grid()
        page.tkraise()
        self.current_page = name
        self.status_var.set(f"当前功能：{name}")
        if previous in self.nav_buttons:
            self._set_nav_style(previous, "Nav.TButton")
        self._set_nav_style(name, "NavSelected.TButton")

    def _set_nav_style(self, name: str, style: str) -> None:
        if self.surface_cache is not None:
            self.surface_cache.set_style(self.nav_buttons[name], style)
        else:
            self.nav_buttons[name].configure(style=style)

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
        if self.credentials_panel.dirty and not self.credentials_panel.save():
            messagebox.showwarning("账号尚未保存", self.credentials_panel.status_var.get(), parent=self)
            return
        self.destroy()

    def destroy(self) -> None:
        if self._warmup_after_id is not None:
            self.after_cancel(self._warmup_after_id)
            self._warmup_after_id = None
        if self.surface_cache is not None:
            self.surface_cache.close()
        super().destroy()


def main() -> int:
    prototype = os.environ.get("TOOLBOX_RENDER_PROTOTYPE") == "1"
    app = ToolboxApp(render_prototype=prototype)
    if prototype:
        app.show_page("出清结果汇总上传")
    app.mainloop()
    return 0
