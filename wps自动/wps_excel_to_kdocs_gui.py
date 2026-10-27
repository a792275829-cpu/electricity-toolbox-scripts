from __future__ import annotations
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import traceback
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Any
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(BASE_DIR.parent))
from toolbox_shared.log_console import LogConsole, explain_error, redact
from toolbox_shared.choices import SegmentedGroup
from toolbox_shared.file_button import FileSelectButton
from toolbox_shared.wps_urls import url_choices
from toolbox_shared.wps_paths import (
    BASE_DIR,
    DATA_DIR,
    CONFIG_PATH,
    DEFAULT_CONFIG_PATH,
    PROFILE_DIR,
    WPS_CHROME_PID_PATH,
    WPS_CHROME_STATE_PATH,
    WPS_CHROME_LAUNCH_VERSION,
    WPS_CHROME_START_LOCK,
    LOG_DIR,
    MAX_RECENT_URLS,
    DOCUMENT_CONFIGS_MIN_HEIGHT,
    CDP_CONNECT_TIMEOUT_MS,
    WPS_LOGIN_STATUS_URL,
    LOGIN_STATUS_POLL_MS,
)
from toolbox_shared.wps_config import (CONFIG_EXPORT_SCHEMA, CONFIG_EXPORT_VERSION, _document_config_from_dict, _region_config_from_dict, _string_list, build_config_export, load_config_export, normalize_runtime_config, runtime_config_from_export_payload, save_config_export)
from toolbox_shared.wps_values import (
    normalize_cell_value,
    normalize_for_compare,
    values_equivalent,
    parse_start_cell,
    normalize_cell_address,
    range_from_cells,
    range_shape,
    split_range,
    target_range_from_start,
    target_rect,
    rect_from_cells,
    rects_overlap,
    coerce_wps_matrix,
    compare_matrix,
)

from toolbox_shared.wps_browser import (
    browser_has_page,
    ensure_background_cdp_target,
    find_or_open_page,
    cdp_candidates,
    resolve_cdp_endpoint,
    find_macos_chrome,
    managed_cdp_host_port,
    build_wps_chrome_command,
    try_resolve_cdp_endpoint,
    cdp_page_targets,
    ensure_wps_cdp_page,
    managed_chrome_state_version,
    managed_chrome_command,
    is_managed_chrome_command,
    discover_managed_chrome_pid,
    remember_managed_chrome,
    clear_managed_chrome_state,
    managed_chrome_pid,
    managed_chrome_launch_version,
    managed_chrome_is_running,
    stop_managed_chrome,
    managed_chrome_is_headless,
    stop_outdated_managed_chrome,
    _ensure_wps_dedicated_chrome,
    ensure_wps_dedicated_chrome,
    open_browser_context,
)
from toolbox_shared.wps_service import (
    MappingRow,
    RegionRow,
    DocumentConfig,
    PreparedMapping,
    WpsLoginStatus,
    read_wps_login_status,
    check_wps_login_status,
    load_config,
    save_config,
    update_recent_urls,
    load_excel_workbook,
    read_excel_range,
    read_excel_ranges,
    read_excel_sheet_names,
    prepare_mappings,
    wait_for_wps_ready,
    evaluate_wps_operation,
    write_to_wps,
    read_online_document_info,
    read_online_sheet_names,
    read_online_range_values,
    read_online_ranges,
    WPS_APPLICATION_GUARD,
)
from toolbox_shared import wps_service as _wps_service

_REMOTE_BUSINESS = None


def read_online_document_info(url, browser_mode, cdp_url, log):
    if _REMOTE_BUSINESS is not None:
        return _REMOTE_BUSINESS.wps_read('read_online_document_info',
            dict(url=url, browser_mode=browser_mode, cdp_url=cdp_url), log)
    return _wps_service.read_online_document_info(url, browser_mode, cdp_url, log)


def read_online_ranges(requests, browser_mode, cdp_url, log):
    if _REMOTE_BUSINESS is not None:
        return _REMOTE_BUSINESS.wps_read('read_online_ranges',
            dict(requests=requests, browser_mode=browser_mode, cdp_url=cdp_url), log)
    return _wps_service.read_online_ranges(requests, browser_mode, cdp_url, log)


def read_online_sheet_names(url, browser_mode, cdp_url, log):
    return read_online_document_info(url, browser_mode, cdp_url, log)['sheets']


def read_online_range_values(url, sheet_name, range_text, rows, cols, browser_mode, cdp_url, log):
    return read_online_ranges([(url, sheet_name, range_text, rows, cols)], browser_mode, cdp_url, log)[0]


def center_window(window: tk.Toplevel | tk.Tk, width: int | None = None, height: int | None = None) -> None:
    window.update_idletasks()
    actual_width = width or window.winfo_width() or window.winfo_reqwidth()
    actual_height = height or window.winfo_height() or window.winfo_reqheight()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    actual_width = min(actual_width, max(640, screen_width - 80))
    actual_height = min(actual_height, max(480, screen_height - 80))
    x = max(0, (screen_width - actual_width) // 2)
    y = max(0, (screen_height - actual_height) // 2)
    window.geometry(f"{actual_width}x{actual_height}+{x}+{y}")


class MappingDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        title: str,
        initial: MappingRow | None = None,
        recent_urls: list[str] | None = None,
        browser_mode: str = "persistent",
        cdp_url: str = "http://127.0.0.1:9222",
        log=None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: MappingRow | None = None
        default_url = initial.kdocs_url if initial else ((recent_urls or [""])[0])
        self.local_sheet_combo: ttk.Combobox | None = None
        self.online_sheet_combo: ttk.Combobox | None = None
        self.kdocs_url_combo: ttk.Combobox | None = None
        self.last_online_sheet_url = ""
        self.browser_mode = browser_mode
        self.cdp_url = cdp_url
        self.log = log or (lambda _message: None)

        self.vars = {
            "kdocs_url": tk.StringVar(value=default_url),
            "local_file": tk.StringVar(value=initial.local_file if initial else ""),
            "source_sheet": tk.StringVar(value=initial.source_sheet if initial else ""),
            "source_start": tk.StringVar(value=initial.source_start if initial else ""),
            "source_end": tk.StringVar(value=initial.source_end if initial else ""),
            "target_sheet": tk.StringVar(value=initial.target_sheet if initial else ""),
            "target_start": tk.StringVar(value=initial.target_start if initial else ""),
            "target_end": tk.StringVar(value=initial.target_end if initial else ""),
        }

        labels = [
            ("目标表格链接", "kdocs_url"),
            ("本地文件", "local_file"),
            ("来源工作表", "source_sheet"),
            ("来源起始单元格", "source_start"),
            ("来源结束单元格", "source_end"),
            ("目标工作表", "target_sheet"),
            ("目标起始单元格", "target_start"),
            ("目标结束单元格", "target_end"),
        ]
        for row, (label, key) in enumerate(labels):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=10, pady=6, sticky="w")
            if key == "kdocs_url":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=recent_urls or [], width=64)
                self.kdocs_url_combo = entry
                entry.bind("<<ComboboxSelected>>", lambda _event: self.refresh_online_sheets(auto=True))
                entry.bind("<FocusOut>", lambda _event: self.refresh_online_sheets(auto=True))
                entry.bind("<Return>", lambda _event: self.refresh_online_sheets(auto=True))
            elif key == "source_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=[], width=36)
                self.local_sheet_combo = entry
            elif key == "target_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=[], width=36)
                self.online_sheet_combo = entry
            else:
                entry = ttk.Entry(self, textvariable=self.vars[key], width=64 if key == "local_file" else 36)
            entry.grid(row=row, column=1, padx=(10, 0) if key == "local_file" else 10, pady=6, sticky="ew")
            if key == "local_file":
                button_frame = ttk.Frame(self)
                button_frame.grid(row=row, column=2, padx=(8, 10), pady=6, sticky="w")
                FileSelectButton(button_frame, text="选择文件", command=self.browse_local_file).pack(side="left")
                ttk.Button(button_frame, text="刷新工作表", command=self.refresh_local_sheets).pack(side="left", padx=(6, 0))
            elif key == "target_sheet":
                ttk.Button(self, text="刷新目标工作表", command=self.refresh_online_sheets).grid(
                    row=row, column=2, padx=10, pady=6
                )

        button_frame = ttk.Frame(self)
        button_frame.grid(row=len(labels), column=0, columnspan=3, padx=10, pady=10, sticky="e")
        ttk.Button(button_frame, text="确定", command=self.on_ok).pack(side="left", padx=4)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda _event: self.on_ok())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.refresh_local_sheets(show_errors=False)
        self.transient(parent)
        self.grab_set()
        center_window(self)
        self.wait_visibility()
        self.focus()
        self.wait_window(self)

    def on_ok(self) -> None:
        row = MappingRow(
            kdocs_url=self.vars["kdocs_url"].get().strip(),
            local_file=self.vars["local_file"].get().strip(),
            source_sheet=self.vars["source_sheet"].get().strip(),
            source_start=self.vars["source_start"].get().strip(),
            source_end=self.vars["source_end"].get().strip(),
            target_sheet=self.vars["target_sheet"].get().strip(),
            target_start=self.vars["target_start"].get().strip(),
            target_end=self.vars["target_end"].get().strip(),
        )
        if not all(
            [
                row.kdocs_url,
                row.local_file,
                row.source_sheet,
                row.source_start,
                row.source_end,
                row.target_sheet,
                row.target_start,
                row.target_end,
            ]
        ):
            messagebox.showerror("信息未填完整", "请填写来源文件、目标表格链接及两边的工作表和单元格范围。", parent=self)
            return
        self.result = row
        self.destroy()

    def browse_local_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择来源表格文件",
            filetypes=[("表格文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if path:
            self.vars["local_file"].set(path)
            self.refresh_local_sheets(show_errors=True)

    def refresh_local_sheets(self, show_errors: bool = True) -> None:
        if self.local_sheet_combo is None:
            return
        path_text = self.vars["local_file"].get().strip()
        if not path_text:
            self.local_sheet_combo.configure(values=[])
            return
        try:
            names = read_excel_sheet_names(Path(path_text).expanduser())
        except Exception as exc:
            self.local_sheet_combo.configure(values=[])
            if show_errors:
                messagebox.showerror("读取工作表失败", str(exc), parent=self)
            return
        self.local_sheet_combo.configure(values=names)
        current = self.vars["source_sheet"].get().strip()
        if names and not current:
            self.vars["source_sheet"].set(names[0])

    def refresh_online_sheets(self, auto: bool = False) -> None:
        if self.online_sheet_combo is None:
            return
        url = self.vars["kdocs_url"].get().strip()
        if not url:
            if auto:
                return
            messagebox.showerror("请填写表格链接", "请先填写目标表格链接，再刷新工作表。", parent=self)
            return
        if auto and url == self.last_online_sheet_url:
            return
        try:
            names = read_online_sheet_names(url, self.browser_mode, self.cdp_url, lambda _message: None)
        except Exception as exc:
            self.online_sheet_combo.configure(values=[])
            self.last_online_sheet_url = ""
            messagebox.showerror("读取目标工作表失败", str(exc), parent=self)
            return
        self.online_sheet_combo.configure(values=names)
        self.last_online_sheet_url = url
        current = self.vars["target_sheet"].get().strip()
        if names and not current:
            self.vars["target_sheet"].set(names[0])


class RegionDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Toplevel,
        title: str,
        initial: RegionRow | None = None,
        local_sheets: list[str] | None = None,
        online_sheets: list[str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: RegionRow | None = None

        self.vars = {
            "source_sheet": tk.StringVar(value=initial.source_sheet if initial else ""),
            "source_start": tk.StringVar(value=initial.source_start if initial else ""),
            "source_end": tk.StringVar(value=initial.source_end if initial else ""),
            "target_sheet": tk.StringVar(value=initial.target_sheet if initial else ""),
            "target_start": tk.StringVar(value=initial.target_start if initial else ""),
            "target_end": tk.StringVar(value=initial.target_end if initial else ""),
        }

        labels = [
            ("来源工作表", "source_sheet"),
            ("来源起始单元格", "source_start"),
            ("来源结束单元格", "source_end"),
            ("目标工作表", "target_sheet"),
            ("目标起始单元格", "target_start"),
            ("目标结束单元格", "target_end"),
        ]
        for row, (label, key) in enumerate(labels):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=10, pady=6, sticky="w")
            if key == "source_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=local_sheets or [], width=42)
            elif key == "target_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=online_sheets or [], width=42)
            else:
                entry = ttk.Entry(self, textvariable=self.vars[key], width=44)
            entry.grid(row=row, column=1, padx=10, pady=6, sticky="ew")

        if not initial:
            if local_sheets:
                self.vars["source_sheet"].set(local_sheets[0])
            if online_sheets:
                self.vars["target_sheet"].set(online_sheets[0])

        button_frame = ttk.Frame(self)
        button_frame.grid(row=len(labels), column=0, columnspan=2, padx=10, pady=10, sticky="e")
        ttk.Button(button_frame, text="确定", command=self.on_ok).pack(side="left", padx=4)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda _event: self.on_ok())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        center_window(self)
        self.wait_visibility()
        self.focus()
        self.wait_window(self)

    def on_ok(self) -> None:
        row = RegionRow(
            source_sheet=self.vars["source_sheet"].get().strip(),
            source_start=self.vars["source_start"].get().strip(),
            source_end=self.vars["source_end"].get().strip(),
            target_sheet=self.vars["target_sheet"].get().strip(),
            target_start=self.vars["target_start"].get().strip(),
            target_end=self.vars["target_end"].get().strip(),
        )
        if not all([row.source_sheet, row.source_start, row.source_end, row.target_sheet, row.target_start, row.target_end]):
            messagebox.showerror("写入区域未填完整", "请填写来源和目标的工作表、起始单元格及结束单元格。", parent=self)
            return
        self.result = row
        self.destroy()


class DocumentConfigDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        title: str,
        initial: DocumentConfig | None = None,
        recent_urls: list[str] | None = None,
        browser_mode: str = "persistent",
        cdp_url: str = "http://127.0.0.1:9222",
        log=None,
        autosave=None,
        document_titles: dict[str, str] | None = None,
        on_document_title=None,
    ):
        super().__init__(parent)
        self.title(title)
        self.geometry("900x620")
        self.minsize(820, 520)
        self.result: DocumentConfig | None = None
        self.recent_urls = recent_urls or []
        self.browser_mode = browser_mode
        self.cdp_url = cdp_url
        self.log = log or (lambda _message: None)
        self.autosave = autosave
        self.document_titles = document_titles if document_titles is not None else {}
        self.on_document_title = on_document_title
        self._updating_target_url = False
        self.local_sheets: list[str] = list(
            dict.fromkeys(
                region.source_sheet
                for region in (initial.regions if initial else [])
                if region.source_sheet
            )
        )
        self.online_sheets: list[str] = list(
            dict.fromkeys(
                region.target_sheet
                for region in (initial.regions if initial else [])
                if region.target_sheet
            )
        )
        self.last_online_sheet_url = ""
        self.regions: list[RegionRow] = list(initial.regions) if initial else []
        self._browser_read_lock = threading.Lock()
        self._online_results: queue.Queue[
            tuple[str, int, str, bool, dict[str, Any] | None, str | None]
        ] = queue.Queue()
        self._request_ids = {"source": 0, "target": 0}
        self._pending_online_reads: dict[str, tuple[int, str]] = {}
        self._online_poll_after_id: str | None = None
        self._online_workers: set[threading.Thread] = set()
        self._online_workers_lock = threading.Lock()

        default_url = initial.kdocs_url if initial else (self.recent_urls[0] if self.recent_urls else "")
        default_source_url = initial.source_url if initial else ""
        self.config_name = tk.StringVar(value=initial.name if initial else "")
        self.source_type = tk.StringVar(value=initial.source_type if initial else "excel")
        self.source_url = tk.StringVar(value=default_source_url)
        self.kdocs_url = tk.StringVar(value=default_url)
        self.target_url_display = tk.StringVar(value=default_url)
        self.local_file = tk.StringVar(value=initial.local_file if initial else "")
        self.source_doc_title = tk.StringVar(value="")
        self.target_doc_title = tk.StringVar(value=self.document_titles.get(default_url, ""))
        cached_sheet_count = len(self.online_sheets)
        cached_status = (
            f"已载入配置中保存的 {cached_sheet_count} 个工作表名称；"
            "如表格有变动，请刷新工作表。"
            if cached_sheet_count
            else ""
        )
        self.read_status = tk.StringVar(value=cached_status)

        self.create_widgets()
        self._refresh_target_url_choices()
        self.target_url_display.trace_add("write", self._target_url_edited)
        self.kdocs_url.trace_add("write", self._target_raw_url_changed)
        # Editing an existing configuration should immediately restore the
        # target document title and current sheet list. The read is asynchronous
        # and managed mode uses the saved profile in headless Chrome, so the
        # dialog remains responsive and no browser window is shown.
        if initial is not None and default_url:
            self.refresh_online_sheets(auto=True)
        if self.source_type.get() == "excel":
            self.refresh_local_sheets(show_errors=False)

        self.transient(parent)
        self.grab_set()
        center_window(self, 900, 620)
        self.wait_visibility()
        self.focus()
        self.wait_window(self)

    def create_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        docs = ttk.LabelFrame(root, text="来源与目标")
        docs.grid(row=0, column=0, sticky="ew")
        ttk.Label(docs, text="配置名称").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(docs, textvariable=self.config_name).grid(row=0, column=1, columnspan=2, padx=8, pady=6, sticky="ew")

        ttk.Label(docs, text="数据来源").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        source_frame = SegmentedGroup(docs, variable=self.source_type,
                                      choices=(("excel", "本地表格"), ("kdocs", "金山文档")))
        source_frame.grid(row=1, column=1, columnspan=2, padx=8, pady=6, sticky="w")

        ttk.Label(docs, text="来源表格链接").grid(row=2, column=0, padx=8, pady=6, sticky="w")
        source_url_combo = ttk.Combobox(docs, textvariable=self.source_url, values=self.recent_urls)
        source_url_combo.grid(row=2, column=1, padx=8, pady=6, sticky="ew")
        self.refresh_source_button = ttk.Button(
            docs, text="刷新来源工作表", command=self.refresh_local_sheets
        )
        self.refresh_source_button.grid(row=2, column=2, padx=8, pady=6)

        ttk.Label(docs, text="目标表格链接").grid(row=3, column=0, padx=8, pady=6, sticky="w")
        self.target_url_combo = ttk.Combobox(
            docs, textvariable=self.target_url_display, postcommand=self._refresh_target_url_choices
        )
        self.target_url_combo.grid(row=3, column=1, padx=8, pady=6, sticky="ew")
        self.target_url_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_online_sheets(auto=True))
        self.target_url_combo.bind("<FocusOut>", lambda _event: self._refresh_target_url_choices())
        self.refresh_target_button = ttk.Button(
            docs, text="刷新目标工作表", command=self.refresh_online_sheets
        )
        self.refresh_target_button.grid(row=3, column=2, padx=8, pady=6)

        ttk.Label(docs, text="来源表格名称").grid(row=4, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(docs, textvariable=self.source_doc_title, state="readonly").grid(row=4, column=1, columnspan=2, padx=8, pady=6, sticky="ew")

        ttk.Label(docs, text="本地文件").grid(row=5, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(docs, textvariable=self.local_file).grid(row=5, column=1, padx=(8, 0), pady=6, sticky="ew")
        file_buttons = ttk.Frame(docs)
        file_buttons.grid(row=5, column=2, padx=8, pady=6, sticky="w")
        FileSelectButton(file_buttons, text="选择文件", command=self.browse_local_file).pack(side="left")
        ttk.Button(file_buttons, text="刷新工作表", command=self.refresh_local_sheets).pack(side="left", padx=(6, 0))
        ttk.Label(docs, textvariable=self.read_status, style="Muted.TLabel").grid(
            row=6, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="w"
        )
        docs.columnconfigure(1, weight=1)

        regions_frame = ttk.LabelFrame(root, text="写入区域")
        regions_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        toolbar = ttk.Frame(regions_frame)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(toolbar, text="添加区域", command=self.add_region).pack(side="left")
        ttk.Button(toolbar, text="编辑", command=self.edit_region).pack(side="left", padx=8)
        ttk.Button(toolbar, text="复制", command=self.copy_region).pack(side="left")
        ttk.Button(toolbar, text="删除", command=self.remove_region).pack(side="left", padx=8)

        tree_frame = ttk.Frame(regions_frame)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("source_sheet", "source_start", "source_end", "target_sheet", "target_start", "target_end")
        self.region_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        for key, label, width in [
            ("source_sheet", "来源工作表", 180),
            ("source_start", "来源起始单元格", 100),
            ("source_end", "来源结束单元格", 100),
            ("target_sheet", "目标工作表", 180),
            ("target_start", "目标起始单元格", 100),
            ("target_end", "目标结束单元格", 100),
        ]:
            self.region_tree.heading(key, text=label)
            self.region_tree.column(key, width=width)
        self.region_tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.region_tree.yview)
        scrollbar.pack(side="left", fill="y")
        self.region_tree.configure(yscrollcommand=scrollbar.set)
        self.region_tree.bind("<Double-1>", lambda _event: self.edit_region())

        button_frame = ttk.Frame(root)
        button_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(button_frame, text="保存配置", command=self.on_ok).pack(side="right", padx=4)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="right", padx=4)
        self.refresh_regions()

    def browse_local_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择来源表格文件",
            filetypes=[("表格文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if path:
            self.local_file.set(path)
            self.refresh_local_sheets(show_errors=True)

    def refresh_local_sheets(self, show_errors: bool = True) -> None:
        if self.source_type.get() == "kdocs":
            url = self.source_url.get().strip()
            if not url:
                self.local_sheets = []
                self.source_doc_title.set("")
                if show_errors:
                    messagebox.showerror("请填写表格链接", "请填写来源表格链接。", parent=self)
                return
            self._start_online_read("source", url, show_errors)
            return

        self._request_ids["source"] += 1
        self._pending_online_reads.pop("source", None)
        self.refresh_source_button.configure(state="normal")
        try:
            path_text = self.local_file.get().strip()
            if not path_text:
                self.local_sheets = []
                self.source_doc_title.set("")
                return
            self.local_sheets = read_excel_sheet_names(Path(path_text).expanduser())
            self.source_doc_title.set(Path(path_text).name)
        except Exception as exc:
            self.local_sheets = []
            if show_errors:
                messagebox.showerror("读取来源工作表失败", str(exc), parent=self)

    def _refresh_target_url_choices(self) -> None:
        url = self.kdocs_url.get().strip()
        choices = url_choices(self.recent_urls + ([url] if url else []), self.document_titles, url)
        self.target_url_combo.configure(values=[option["label"] for option in choices["options"]])
        self._updating_target_url = True
        try:
            self.target_url_display.set(choices["label"])
        finally:
            self._updating_target_url = False

    def _target_url_edited(self, *_args) -> None:
        if self._updating_target_url:
            return
        choices = url_choices(self.recent_urls, self.document_titles, self.target_url_display.get())
        url = choices["url"]
        if url != self.kdocs_url.get():
            # Keep typed input editable; decorating happens on selection/blur/read.
            self._updating_target_url = True
            try:
                self.kdocs_url.set(url)
                self.target_doc_title.set(self.document_titles.get(url, ""))
            finally:
                self._updating_target_url = False

    def _target_raw_url_changed(self, *_args) -> None:
        if not self._updating_target_url:
            self.target_doc_title.set(self.document_titles.get(self.kdocs_url.get().strip(), ""))
            self._refresh_target_url_choices()

    def refresh_online_sheets(self, auto: bool = False) -> None:
        url = self.kdocs_url.get().strip()
        if not url:
            if auto:
                return
            messagebox.showerror("请填写表格链接", "请先填写目标表格链接，再刷新工作表。", parent=self)
            return
        if auto and url == self.last_online_sheet_url:
            return
        self._start_online_read("target", url, not auto)

    def _start_online_read(self, kind: str, url: str, show_errors: bool) -> None:
        pending = self._pending_online_reads.get(kind)
        if pending is not None and pending[1] == url:
            return

        self._request_ids[kind] += 1
        request_id = self._request_ids[kind]
        self._pending_online_reads[kind] = (request_id, url)
        button = self.refresh_source_button if kind == "source" else self.refresh_target_button
        button.configure(state="disabled")
        if kind == "source":
            self.source_doc_title.set("正在读取…")
        else:
            self.target_doc_title.set("正在读取…")
        self.read_status.set("正在后台读取表格，您可以继续编辑配置…")

        # Keep the worker independent from this Toplevel. A browser read can outlive
        # the dialog, and retaining ``self`` from that thread lets Tk variables be
        # finalized off the main thread when the window has already been closed.
        browser_read_lock = self._browser_read_lock
        browser_mode = self.browser_mode
        cdp_url = self.cdp_url
        log = self.log
        online_results = self._online_results
        online_workers = self._online_workers
        online_workers_lock = self._online_workers_lock

        def worker() -> None:
            info: dict[str, Any] | None = None
            error: str | None = None
            try:
                # A persistent Playwright profile cannot be opened twice at once.
                with browser_read_lock:
                    info = read_online_document_info(
                        url, browser_mode, cdp_url, log
                    )
            except Exception as exc:
                error = str(exc)
            finally:
                online_results.put(
                    (kind, request_id, url, show_errors, info, error)
                )
                with online_workers_lock:
                    online_workers.discard(threading.current_thread())

        thread = threading.Thread(target=worker, daemon=True)
        with self._online_workers_lock:
            self._online_workers.add(thread)
        try:
            thread.start()
        except Exception:
            with self._online_workers_lock:
                self._online_workers.discard(thread)
            raise
        if self._online_poll_after_id is None:
            self._online_poll_after_id = self.after(50, self._drain_online_results)

    def destroy(self) -> None:
        if self._online_poll_after_id is not None:
            try:
                self.after_cancel(self._online_poll_after_id)
            except tk.TclError:
                pass
            self._online_poll_after_id = None
        self._pending_online_reads.clear()
        super().destroy()

    def _drain_online_results(self) -> None:
        self._online_poll_after_id = None
        while True:
            try:
                kind, request_id, url, show_errors, info, error = self._online_results.get_nowait()
            except queue.Empty:
                break
            if request_id != self._request_ids[kind]:
                continue

            self._pending_online_reads.pop(kind, None)
            button = self.refresh_source_button if kind == "source" else self.refresh_target_button
            button.configure(state="normal")
            if kind == "target" and url != self.kdocs_url.get().strip():
                continue
            if error is not None:
                if kind == "source":
                    self.source_doc_title.set("读取失败")
                    title = "读取来源工作表失败"
                else:
                    self.target_doc_title.set("读取失败")
                    self.last_online_sheet_url = ""
                    title = "读取目标工作表失败"
                self.read_status.set("读取失败，已保留原有工作表配置。请刷新重试或检查浏览器登录状态。")
                self.log(f"{title}：{error}")
                if show_errors:
                    messagebox.showerror(title, error, parent=self)
                continue

            sheets = list((info or {}).get("sheets") or [])
            document_title = str((info or {}).get("title") or "")
            if document_title:
                self.document_titles[url] = document_title
                if self.on_document_title is not None:
                    self.on_document_title(url, document_title)
            if kind == "source":
                self.local_sheets = sheets
                self.source_doc_title.set(document_title)
            else:
                self.online_sheets = sheets
                self.target_doc_title.set(document_title)
                self.last_online_sheet_url = url
            self._refresh_target_url_choices()
            self.read_status.set(f"已读取 {len(sheets)} 个工作表。")

        if self._pending_online_reads:
            self._online_poll_after_id = self.after(50, self._drain_online_results)

    def add_region(self) -> None:
        dialog = RegionDialog(self, "添加区域", local_sheets=self.local_sheets, online_sheets=self.online_sheets)
        if dialog.result:
            self.regions.append(dialog.result)
            self.refresh_regions()
            self.autosave_current_config()

    def edit_region(self) -> None:
        selected = self.region_tree.selection()
        if not selected:
            return
        index = self.region_tree.index(selected[0])
        dialog = RegionDialog(
            self,
            "编辑写入区域",
            self.regions[index],
            local_sheets=self.local_sheets,
            online_sheets=self.online_sheets,
        )
        if dialog.result:
            self.regions[index] = dialog.result
            self.refresh_regions()
            self.autosave_current_config()

    def copy_region(self) -> None:
        selected = self.region_tree.selection()
        if not selected:
            messagebox.showinfo("复制写入区域", "请先选择要复制的写入区域。", parent=self)
            return
        index = self.region_tree.index(selected[0])
        source = self.regions[index]
        copied = RegionRow(
            source_sheet=source.source_sheet,
            source_start=source.source_start,
            source_end=source.source_end,
            target_sheet=source.target_sheet,
            target_start=source.target_start,
            target_end=source.target_end,
        )
        self.regions.insert(index + 1, copied)
        self.refresh_regions()
        new_item = self.region_tree.get_children()[index + 1]
        self.region_tree.selection_set(new_item)
        self.region_tree.focus(new_item)
        self.region_tree.see(new_item)
        self.autosave_current_config()

    def remove_region(self) -> None:
        selected = self.region_tree.selection()
        if not selected:
            return
        count = len(selected)
        if not messagebox.askyesno(
            "确认删除",
            f"确定删除选中的 {count} 个写入区域吗？",
            parent=self,
        ):
            return
        for item in reversed(selected):
            del self.regions[self.region_tree.index(item)]
        self.refresh_regions()
        self.autosave_current_config()

    def build_current_config(self) -> DocumentConfig | None:
        kdocs_url = self.kdocs_url.get().strip()
        local_file = self.local_file.get().strip()
        source_type = self.source_type.get()
        source_url = self.source_url.get().strip()
        if not kdocs_url or not self.regions:
            return None
        if source_type == "excel" and not local_file:
            return None
        if source_type == "kdocs" and not source_url:
            return None
        name = self.config_name.get().strip() or Path(local_file).stem or "未命名配置"
        return DocumentConfig(
            name=name,
            kdocs_url=kdocs_url,
            local_file=local_file,
            regions=list(self.regions),
            source_type=source_type,
            source_url=source_url,
        )

    def autosave_current_config(self) -> None:
        if self.autosave is None:
            return
        config = self.build_current_config()
        if config is None:
            return
        self.autosave(config)

    def refresh_regions(self) -> None:
        for item in self.region_tree.get_children():
            self.region_tree.delete(item)
        for region in self.regions:
            self.region_tree.insert(
                "",
                "end",
                values=(
                    region.source_sheet,
                    region.source_start,
                    region.source_end,
                    region.target_sheet,
                    region.target_start,
                    region.target_end,
                ),
            )

    def on_ok(self) -> None:
        kdocs_url = self.kdocs_url.get().strip()
        local_file = self.local_file.get().strip()
        source_type = self.source_type.get()
        source_url = self.source_url.get().strip()
        if not kdocs_url:
            messagebox.showerror("配置未填完整", "请填写目标表格链接。", parent=self)
            return
        if source_type == "excel" and not local_file:
            messagebox.showerror("配置未填完整", "请选择本地表格文件。", parent=self)
            return
        if source_type == "kdocs" and not source_url:
            messagebox.showerror("配置未填完整", "请填写来源表格链接。", parent=self)
            return
        if not self.regions:
            messagebox.showerror("配置未填完整", "请至少添加一个写入区域。", parent=self)
            return
        name = self.config_name.get().strip() or Path(local_file).stem or "未命名配置"
        self.result = DocumentConfig(
            name=name,
            kdocs_url=kdocs_url,
            local_file=local_file,
            regions=list(self.regions),
            source_type=source_type,
            source_url=source_url,
        )
        if self.autosave is not None:
            self.autosave(self.result)
        self.destroy()


class WpsWriterFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, task_registry: Any | None = None) -> None:
        super().__init__(parent)
        self.task_registry = task_registry
        global _REMOTE_BUSINESS
        _REMOTE_BUSINESS = task_registry if getattr(task_registry, "remote_business", False) is True else None
        self._remote_messages = queue.Queue()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.browser_event_queue: queue.Queue[tuple[bool, str, bool]] = queue.Queue()
        self.login_event_queue: queue.Queue[tuple[str, str, WpsLoginStatus]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.browser_worker: threading.Thread | None = None
        self.configs: list[DocumentConfig] = []
        self._drain_after_id: str | None = None
        self._login_poll_after_id: str | None = None

        self.recent_urls: list[str] = []
        self.document_titles: dict[str, str] = {}
        self.browser_mode = tk.StringVar(master=self, value="managed_cdp")
        self.cdp_url = tk.StringVar(master=self, value="http://127.0.0.1:9222")
        self.browser_status = tk.StringVar(master=self, value="尚未检查浏览器连接")
        self.login_status = tk.StringVar(master=self, value="尚未检查")

        self.create_widgets()
        self.load_initial_config()
        self.schedule_drain_logs()
        self.bind("<Map>", self._schedule_login_check, add="+")
        self.bind("<Unmap>", self._pause_hidden_page, add="+")
        self.browser_mode.trace_add("write", self._invalidate_login_status)
        self.cdp_url.trace_add("write", self._invalidate_login_status)

    def create_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=4, minsize=DOCUMENT_CONFIGS_MIN_HEIGHT)
        root.rowconfigure(2, weight=1)

        file_frame = ttk.LabelFrame(root, text="浏览器连接")
        file_frame.grid(row=0, column=0, sticky="ew")

        ttk.Label(file_frame, text="连接方式").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        mode_frame = SegmentedGroup(file_frame, variable=self.browser_mode, choices=(
            ("managed_cdp", "WPS 专用浏览器（推荐）"),
            ("persistent", "临时浏览器"),
            ("cdp", "连接已打开的浏览器"),
        ))
        mode_frame.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        ttk.Label(file_frame, text="浏览器连接地址").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.cdp_url).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        browser_actions = ttk.Frame(file_frame)
        browser_actions.grid(row=2, column=1, padx=8, pady=(0, 8), sticky="ew")
        self.start_browser_button = ttk.Button(
            browser_actions,
            text="登录 WPS",
            command=self.start_wps_browser,
        )
        self.start_browser_button.pack(side="left")
        self.check_browser_button = ttk.Button(
            browser_actions,
            text="刷新登录状态",
            command=self.check_wps_browser,
        )
        self.check_browser_button.pack(side="left", padx=(8, 0))
        ttk.Label(browser_actions, textvariable=self.browser_status, style="Muted.TLabel").pack(
            side="left", padx=(12, 0)
        )
        ttk.Label(file_frame, text="WPS 登录状态").grid(
            row=3, column=0, padx=8, pady=(0, 8), sticky="w"
        )
        self.login_status_label = ttk.Label(
            file_frame, textvariable=self.login_status, wraplength=600
        )
        self.login_status_label.grid(row=3, column=1, padx=8, pady=(0, 8), sticky="w")
        login_hint = ttk.Label(
            file_frame,
            text="首次使用或更换电脑后，请先登录。完成登录后，状态会自动刷新。",
            style="Muted.TLabel", wraplength=600,
        )
        login_hint.grid(row=4, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="w")
        browser_width = None

        def resize_browser_labels(event) -> None:
            nonlocal browser_width
            if event.width == browser_width:
                return
            browser_width = event.width
            self.login_status_label.configure(wraplength=max(160, event.width - 150))
            login_hint.configure(wraplength=max(240, event.width - 24))

        file_frame.bind("<Configure>", resize_browser_labels)
        file_frame.columnconfigure(1, weight=1)

        mapping_frame = ttk.LabelFrame(root, text="写入配置")
        mapping_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        mapping_frame.columnconfigure(0, weight=1)
        mapping_frame.rowconfigure(1, weight=1)

        self.mapping_toolbar = ttk.Frame(mapping_frame)
        self.mapping_toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        self.mapping_toolbar.columnconfigure(2, weight=1)

        self.mapping_primary_toolbar = ttk.Frame(self.mapping_toolbar)
        self.mapping_primary_toolbar.grid(row=0, column=0, sticky="w")
        primary_actions = [
            ("添加配置", self.add_config),
            ("编辑", self.edit_config),
            ("复制", self.copy_config),
            ("删除", self.remove_config),
        ]
        for column, (text, command) in enumerate(primary_actions):
            ttk.Button(
                self.mapping_primary_toolbar,
                text=text,
                command=command,
                width=0,
            ).grid(row=0, column=column, padx=(0 if column == 0 else 4, 0))

        self.mapping_sort_toolbar = ttk.Frame(self.mapping_toolbar)
        self.mapping_sort_toolbar.grid(
            row=0, column=3, sticky="e", padx=(12, 0)
        )
        ttk.Button(
            self.mapping_sort_toolbar, text="上移", command=self.move_config_up, width=0
        ).pack(side="left")
        ttk.Button(
            self.mapping_sort_toolbar, text="下移", command=self.move_config_down, width=0
        ).pack(side="left", padx=(4, 0))

        self.mapping_secondary_toolbar = ttk.Frame(self.mapping_toolbar)
        self.mapping_secondary_toolbar.grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Button(
            self.mapping_secondary_toolbar, text="导入", command=self.import_config, width=0
        ).pack(side="left")
        ttk.Button(
            self.mapping_secondary_toolbar, text="导出", command=self.export_config, width=0
        ).pack(side="left", padx=(4, 0))

        tree_frame = ttk.Frame(mapping_frame)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        columns = ("name", "source_type", "source", "kdocs_url", "region_count", "summary")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)
        self.tree.heading("name", text="配置名称")
        self.tree.heading("source_type", text="数据来源")
        self.tree.heading("source", text="来源文件或链接")
        self.tree.heading("kdocs_url", text="目标表格链接")
        self.tree.heading("region_count", text="区域数量")
        self.tree.heading("summary", text="首个写入区域")
        # Match the reference image: compact names/counts, wider source and summary.
        column_widths = (145, 115, 470, 320, 125, 495)
        for column, width in zip(columns, column_widths):
            self.tree.heading(column, anchor="center")
            self.tree.column(column, width=width // 2, anchor="center", stretch=False)

        configured_width = None

        def resize_config_columns(event) -> None:
            nonlocal configured_width
            # Keep all six columns visible at normal sizes, with horizontal
            # scrolling available when the window is too narrow.
            available = max(840, event.width - 2)
            if available == configured_width:
                return
            configured_width = available
            remaining = available
            for index, (column, width) in enumerate(zip(columns, column_widths)):
                actual_width = (
                    remaining if index == len(columns) - 1
                    else round(available * width / sum(column_widths))
                )
                self.tree.column(column, width=actual_width)
                remaining -= actual_width

        self.tree.bind("<Configure>", resize_config_columns)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal_scrollbar.set)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda _event: self.edit_config())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_selection_status())

        self.execution_toolbar = ttk.Frame(mapping_frame)
        self.execution_toolbar.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.selection_status = tk.StringVar(value="请先选择配置，再预览或写入。")
        ttk.Label(
            self.execution_toolbar,
            textvariable=self.selection_status,
            style="Muted.TLabel",
        ).pack(side="left")
        ttk.Button(
            self.execution_toolbar, text="开始写入", command=self.write, style="Primary.TButton"
        ).pack(side="right")
        ttk.Button(
            self.execution_toolbar, text="预览", command=self.preview
        ).pack(side="right", padx=(0, 6))

        self.log_console = LogConsole(root, module="WPS写入工具", directory=LOG_DIR / "runs")
        self.module_log_frame = self.log_console
        self.module_log_frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self.log_text = self.log_console.text

    def add_config(self) -> None:
        dialog = DocumentConfigDialog(
            self,
            "添加配置",
            recent_urls=self.recent_urls,
            browser_mode=self.browser_mode.get(),
            cdp_url=self.cdp_url.get().strip(),
            log=self.log,
            document_titles=self.document_titles,
            on_document_title=self.remember_document_title,
        )
        if dialog.result:
            self.configs.append(dialog.result)
            self.remember_mapping_urls()
            self.refresh_tree()
            self.save_current_config()

    def edit_config(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        index = self.tree.index(selected[0])

        def autosave(config: DocumentConfig) -> None:
            self.configs[index] = config
            self.remember_mapping_urls()
            self.save_current_config()

        dialog = DocumentConfigDialog(
            self,
            "编辑配置",
            self.configs[index],
            recent_urls=self.recent_urls,
            browser_mode=self.browser_mode.get(),
            cdp_url=self.cdp_url.get().strip(),
            log=self.log,
            autosave=autosave,
            document_titles=self.document_titles,
            on_document_title=self.remember_document_title,
        )
        if dialog.result:
            self.configs[index] = dialog.result
            self.remember_mapping_urls()
            self.refresh_tree()
            self.save_current_config()

    def copy_config(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("复制配置", "请先选择要复制的配置。", parent=self)
            return
        index = self.tree.index(selected[0])
        source = self.configs[index]
        copied = DocumentConfig(
            name=f"{source.name}（副本）",
            kdocs_url=source.kdocs_url,
            local_file=source.local_file,
            source_type=source.source_type,
            source_url=source.source_url,
            regions=[
                RegionRow(
                    source_sheet=region.source_sheet,
                    source_start=region.source_start,
                    source_end=region.source_end,
                    target_sheet=region.target_sheet,
                    target_start=region.target_start,
                    target_end=region.target_end,
                )
                for region in source.regions
            ],
        )
        self.configs.insert(index + 1, copied)
        self.refresh_tree()
        new_item = self.tree.get_children()[index + 1]
        self.tree.selection_set(new_item)
        self.tree.focus(new_item)
        self.tree.see(new_item)
        self.save_current_config()

    def remove_config(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        count = len(selected)
        if not messagebox.askyesno(
            "确认删除",
            f"确定删除选中的 {count} 项配置吗？",
            parent=self,
        ):
            return
        for item in reversed(selected):
            del self.configs[self.tree.index(item)]
        self.refresh_tree()
        self.save_current_config()

    def move_config_up(self) -> None:
        indexes = self.selected_config_indexes()
        if not indexes:
            return
        indexes = sorted(indexes)
        for index in indexes:
            if index == 0:
                continue
            self.configs[index - 1], self.configs[index] = self.configs[index], self.configs[index - 1]
        new_indexes = [max(0, index - 1) for index in indexes]
        self.refresh_tree()
        self.select_config_indexes(new_indexes)
        self.save_current_config()

    def move_config_down(self) -> None:
        indexes = self.selected_config_indexes()
        if not indexes:
            return
        indexes = sorted(indexes, reverse=True)
        last = len(self.configs) - 1
        for index in indexes:
            if index >= last:
                continue
            self.configs[index + 1], self.configs[index] = self.configs[index], self.configs[index + 1]
        new_indexes = [min(last, index + 1) for index in indexes]
        self.refresh_tree()
        self.select_config_indexes(new_indexes)
        self.save_current_config()

    def select_config_indexes(self, indexes: list[int]) -> None:
        children = self.tree.get_children()
        items = [children[index] for index in sorted(set(indexes)) if 0 <= index < len(children)]
        if not items:
            self.update_selection_status()
            return
        self.tree.selection_set(items)
        self.tree.focus(items[0])
        self.tree.see(items[0])
        self.update_selection_status()

    def update_selection_status(self) -> None:
        indexes = self.selected_config_indexes()
        if not indexes:
            self.selection_status.set("请先选择配置，再预览或写入。")
        elif len(indexes) == 1:
            self.selection_status.set(f"已选择：{self.configs[indexes[0]].name}")
        else:
            self.selection_status.set(f"已选择 {len(indexes)} 项配置。")

    def refresh_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for config in self.configs:
            first = config.regions[0] if config.regions else None
            summary = ""
            if first:
                try:
                    summary = (
                        f"{first.source_sheet}!{range_from_cells(first.source_start, first.source_end)} -> "
                        f"{first.target_sheet}!{range_from_cells(first.target_start, first.target_end)}"
                    )
                except Exception:
                    summary = (
                        f"{first.source_sheet}!{first.source_start}:{first.source_end} -> "
                        f"{first.target_sheet}!{first.target_start}:{first.target_end}"
                    )
            self.tree.insert(
                "",
                "end",
                values=(
                    config.name,
                    "金山文档" if config.source_type == "kdocs" else "本地表格",
                    config.source_url if config.source_type == "kdocs" else config.local_file,
                    config.kdocs_url,
                    len(config.regions),
                    summary,
                ),
            )
        self.update_selection_status()

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def start_wps_browser(self, initial_url: str | None = None) -> None:
        self.browser_mode.set("managed_cdp")
        self.save_current_config()
        self._run_browser_action(start=True, initial_url=initial_url)

    def check_wps_browser(self, *, quiet: bool = False) -> None:
        self._run_browser_action(start=False, quiet=quiet)

    def _invalidate_login_status(self, *_args) -> None:
        self.login_status.set("尚未检查")
        self.login_status_label.configure(foreground="#666666")

    def _schedule_login_check(self, event) -> None:
        if event.widget is not self:
            return
        self.schedule_drain_logs()
        if self._login_poll_after_id is None:
            # The toolbox also constructs this page during hidden warmup.
            # Check only once it is visible, without delaying page creation.
            self._login_poll_after_id = self.after(750, self._poll_login_status)

    def _has_pending_work(self) -> bool:
        return any(thread is not None and thread.is_alive()
                   for thread in (self.worker, self.browser_worker)) or any(
            not pending.empty() for pending in (
                self.log_queue, self.login_event_queue,
                self.browser_event_queue, self._remote_messages,
            )
        )

    def _pause_hidden_page(self, event) -> None:
        if event.widget is not self:
            return
        if self._login_poll_after_id is not None:
            self.after_cancel(self._login_poll_after_id)
            self._login_poll_after_id = None
        if not self._has_pending_work() and self._drain_after_id is not None:
            self.after_cancel(self._drain_after_id)
            self._drain_after_id = None

    def _poll_login_status(self) -> None:
        self._login_poll_after_id = None
        if not self.winfo_ismapped():
            return
        if not (self.worker and self.worker.is_alive()):
            self.check_wps_browser(quiet=True)
        self._login_poll_after_id = self.after(LOGIN_STATUS_POLL_MS, self._poll_login_status)

    def _run_browser_action(
        self, *, start: bool, initial_url: str | None = None, quiet: bool = False
    ) -> None:
        if self.browser_worker and self.browser_worker.is_alive():
            return
        self.start_browser_button.configure(state="disabled")
        self.check_browser_button.configure(state="disabled")
        if not quiet:
            self.browser_status.set("正在启动…" if start else "正在检查…")
        if not quiet or self.login_status.get() == "尚未检查":
            self.login_status.set("正在检查…")
        cdp_url = self.cdp_url.get().strip()
        browser_mode = self.browser_mode.get()
        remote_config = self.current_runtime_config() if getattr(getattr(self, "task_registry", None), "remote_business", False) is True else None

        def worker() -> None:
            try:
                if remote_config is not None:
                    result = self.task_registry.wps_action("login" if start else "status", remote_config, [],
                        (lambda _message: None) if quiet else self.log, operation={"initial_url": initial_url})
                    self.login_event_queue.put((browser_mode, cdp_url, WpsLoginStatus(result["state"], result["message"])))
                    self.browser_event_queue.put((True, "专用浏览器已启动" if start else "登录状态检查完成", quiet))
                    return
                if start:
                    result = ensure_wps_dedicated_chrome(
                        cdp_url,
                        self.log,
                        initial_url=initial_url or "https://www.kdocs.cn/latest",
                        preload_url=True,
                    )
                    action = "已启动" if result.get("started") else "已连接"
                    message = f"专用浏览器{action}"
                else:
                    message = "登录状态检查完成"
                login = check_wps_login_status(
                    browser_mode, cdp_url, (lambda _message: None) if quiet else self.log
                )
                self.login_event_queue.put((browser_mode, cdp_url, login))
                self.browser_event_queue.put((True, message, quiet))
            except Exception as exc:
                self.login_event_queue.put((browser_mode, cdp_url, WpsLoginStatus(
                    "unknown", "暂时无法确认，请检查网络或浏览器后刷新登录状态。"
                )))
                self.browser_event_queue.put((False, getattr(exc, "log_details", str(exc)), quiet))

        self.browser_worker = self._start_tracked_thread(
            worker,
            name="wps-browser-start" if start else "wps-browser-check",
        )

    def _start_tracked_thread(
        self,
        target,
        *,
        args: tuple[Any, ...] = (),
        name: str,
    ) -> threading.Thread:
        thread: threading.Thread

        def tracked_target() -> None:
            try:
                target(*args)
            finally:
                if self.task_registry is not None:
                    self.task_registry.unregister_thread(thread)

        thread = threading.Thread(target=tracked_target, name=name, daemon=True)
        if self.task_registry is not None:
            self.task_registry.register_thread(thread)
        try:
            thread.start()
        except BaseException:
            if self.task_registry is not None:
                self.task_registry.unregister_thread(thread)
            raise
        self.schedule_drain_logs()
        return thread

    def detail_log(self, message: str, *, show_traceback: bool = True) -> None:
        if show_traceback and "Traceback (most recent call last)" in message:
            self.log_queue.put(message)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            log_path = LOG_DIR / f"wps_write_{now.strftime('%Y-%m-%d')}.log"
            line = redact(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as exc:
            self.log_queue.put(f"详细日志未能保存：{exc}；仍可复制本次运行日志。")

    def detail_log_prepared(self, action: str, prepared: list[PreparedMapping]) -> None:
        self.detail_log(f"{action}: {len(prepared)} region(s)")
        for item in prepared:
            sample = item.values[:3]
            self.detail_log(
                "REGION "
                f"index={item.index}; source={item.workbook_path}; "
                f"source_range={item.source_sheet}!{item.source_range}; "
                f"target={item.kdocs_url}; target_range={item.target_sheet}!{item.target_range}; "
                f"shape={item.rows}x{item.cols}; sample_first3={sample!r}"
            )

    def drain_logs(self) -> None:
        if self._drain_after_id is not None:
            self.after_cancel(self._drain_after_id)
        self._drain_after_id = None
        while True:
            try:
                title, error = self._remote_messages.get_nowait()
            except queue.Empty:
                break
            messagebox.showerror(title, error, parent=self)
        while True:
            try:
                browser_mode, cdp_url, login = self.login_event_queue.get_nowait()
            except queue.Empty:
                break
            if (browser_mode, cdp_url) != (self.browser_mode.get(), self.cdp_url.get().strip()):
                continue
            if self.login_status.get() != login.message:
                self.log("WPS 登录状态：" + login.message)
            self.login_status.set(login.message)
            self.login_status_label.configure(foreground={
                "logged_in": "#15803d", "logged_out": "#b45309", "unknown": "#666666",
            }[login.state])
        while True:
            try:
                success, status, quiet = self.browser_event_queue.get_nowait()
            except queue.Empty:
                break
            self.start_browser_button.configure(state="normal")
            self.check_browser_button.configure(state="normal")
            self.browser_status.set(status if success else "检查失败，请重试")
            if not quiet:
                self.log(status if success else "检查失败：" + status)
        chunks = []
        size = 0
        for _ in range(200):
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            chunks.append(message.rstrip("\n") + "\n")
            size += len(message)
            terminal = any(marker in message for marker in (
                "操作失败：", "预览失败：", "暂时无法写入：", "检查失败：", "回读数据与来源不一致",
                "Traceback (most recent call last)", "写入完成：", "预览通过：",
            ))
            if terminal:
                self.log_console.append("".join(chunks))
                chunks.clear()
            if any(marker in message for marker in ("操作失败：", "预览失败：", "暂时无法写入：", "检查失败：", "回读数据与来源不一致")):
                diagnosis = explain_error(message) or (message, "点击“复制错误”提供完整日志；重试前核对是否已有部分写入。")
                self.log_console.finish("执行失败", *diagnosis)
            elif "Traceback (most recent call last)" in message:
                diagnosis = explain_error(message)
                if diagnosis:
                    self.log_console.finish("执行失败", *diagnosis)
            elif "写入完成：" in message or "预览通过：" in message:
                self.log_console.finish("已完成")
            if size >= 64_000:
                break
        if chunks:
            self.log_console.append("".join(chunks))
        if self.winfo_ismapped() or self._has_pending_work():
            self.schedule_drain_logs()

    def schedule_drain_logs(self) -> None:
        if self._drain_after_id is not None:
            return
        try:
            self._drain_after_id = self.after(100, self.drain_logs)
        except tk.TclError:
            self._drain_after_id = None

    def destroy(self) -> None:
        if self._login_poll_after_id is not None:
            try:
                self.after_cancel(self._login_poll_after_id)
            except tk.TclError:
                pass
            self._login_poll_after_id = None
        if self._drain_after_id is not None:
            try:
                self.after_cancel(self._drain_after_id)
            except tk.TclError:
                pass
            self._drain_after_id = None
        self._flush_pending_logs()
        super().destroy()

    def _flush_pending_logs(self) -> None:
        while not self.log_queue.empty():
            chunks = []
            for _ in range(200):
                try:
                    chunks.append(self.log_queue.get_nowait().rstrip("\n") + "\n")
                except queue.Empty:
                    break
            if chunks:
                self.log_console.append("".join(chunks), render=False)

    def clear_log(self) -> None:
        # Persist pending messages without drawing a run that is being closed.
        self._flush_pending_logs()
        self.log_console.begin()

    def save_log(self) -> None:
        # Compatibility for older callers: logs are now copied, never exported.
        self.log_console.copy_error()

    def load_initial_config(self) -> None:
        config = load_config()
        self.document_titles = normalize_runtime_config(config).get("document_titles", {})
        self.recent_urls = [url for url in config.get("recent_urls", []) if isinstance(url, str) and url.strip()]
        if config.get("kdocs_url"):
            self.recent_urls = update_recent_urls(self.recent_urls, config["kdocs_url"])
        saved_browser_mode = config.get("browser_mode", "managed_cdp")
        if saved_browser_mode == "persistent" and sys.platform == "darwin":
            saved_browser_mode = "managed_cdp"
        self.browser_mode.set(saved_browser_mode)
        self.cdp_url.set(config.get("cdp_url", "http://127.0.0.1:9222"))
        default_url = config.get("kdocs_url", "")
        default_file = config.get("excel_path", "")
        if isinstance(config.get("configs"), list):
            self.configs = [self.document_config_from_config(row) for row in config.get("configs", []) if isinstance(row, dict)]
        else:
            mappings = [
                self.mapping_from_config(row, default_url=default_url, default_file=default_file)
                for row in config.get("mappings", [])
                if isinstance(row, dict)
            ]
            self.configs = self.group_mappings_into_configs(mappings)
        self.remember_mapping_urls()
        self.refresh_tree()

    def document_config_from_config(self, row: dict[str, Any]) -> DocumentConfig:
        regions = []
        for region in row.get("regions", []):
            if not isinstance(region, dict):
                continue
            regions.append(
                RegionRow(
                    source_sheet=region.get("source_sheet", ""),
                    source_start=region.get("source_start", ""),
                    source_end=region.get("source_end", ""),
                    target_sheet=region.get("target_sheet", ""),
                    target_start=region.get("target_start", ""),
                    target_end=region.get("target_end", ""),
                )
            )
        return DocumentConfig(
            name=row.get("name", "") or Path(row.get("local_file", "")).stem or "未命名配置",
            kdocs_url=row.get("kdocs_url", ""),
            local_file=row.get("local_file", ""),
            regions=regions,
            source_type=row.get("source_type", "excel"),
            source_url=row.get("source_url", ""),
        )

    def mapping_from_config(self, row: dict[str, Any], default_url: str = "", default_file: str = "") -> MappingRow:
        if "source_range" in row and ("source_start" not in row or "source_end" not in row):
            source_start, source_end = split_range(row.get("source_range", ""))
        else:
            source_start = row.get("source_start", "")
            source_end = row.get("source_end", "")

        target_start = row.get("target_start", "")
        target_end = row.get("target_end", "")
        if not target_end and target_start and "source_range" in row:
            source_start_for_shape, source_end_for_shape = split_range(row.get("source_range", ""))
            rows, cols = range_shape(source_start_for_shape, source_end_for_shape)
            target_range = target_range_from_start(target_start, rows, cols)
            _target_start, target_end = split_range(target_range)

        return MappingRow(
            kdocs_url=row.get("kdocs_url", "") or default_url,
            local_file=row.get("local_file", "") or default_file,
            source_sheet=row.get("source_sheet", ""),
            source_start=source_start,
            source_end=source_end,
            target_sheet=row.get("target_sheet", ""),
            target_start=target_start,
            target_end=target_end,
            source_type=row.get("source_type", "excel"),
            source_url=row.get("source_url", ""),
        )

    def group_mappings_into_configs(self, mappings: list[MappingRow]) -> list[DocumentConfig]:
        grouped: dict[tuple[str, str], list[RegionRow]] = {}
        for mapping in mappings:
            key = (mapping.kdocs_url, mapping.local_file)
            grouped.setdefault(key, []).append(
                RegionRow(
                    source_sheet=mapping.source_sheet,
                    source_start=mapping.source_start,
                    source_end=mapping.source_end,
                    target_sheet=mapping.target_sheet,
                    target_start=mapping.target_start,
                    target_end=mapping.target_end,
                )
            )
        configs = []
        for index, ((kdocs_url, local_file), regions) in enumerate(grouped.items(), start=1):
            name = Path(local_file).stem or f"配置 {index}"
            configs.append(DocumentConfig(name=name, kdocs_url=kdocs_url, local_file=local_file, regions=regions))
        return configs

    def remember_mapping_urls(self) -> None:
        urls = self.recent_urls
        for config in self.configs:
            urls = update_recent_urls(urls, config.kdocs_url)
            if config.source_type == "kdocs":
                urls = update_recent_urls(urls, config.source_url)
        self.recent_urls = urls

    def save_current_config(self) -> None:
        self.remember_mapping_urls()
        save_config(self.current_runtime_config())

    def remember_document_title(self, url: str, title: str) -> None:
        self.document_titles[url] = title
        try:
            self.save_current_config()
        except OSError as exc:
            self.log(f"表格名称已读取，但缓存未能保存：{exc}")

    def current_runtime_config(self) -> dict[str, Any]:
        return normalize_runtime_config(
            {
                "recent_urls": self.recent_urls,
                "document_titles": self.document_titles,
                "browser_mode": self.browser_mode.get(),
                "cdp_url": self.cdp_url.get(),
                "configs": [
                    {
                        "name": config.name,
                        "source_type": config.source_type,
                        "source_url": config.source_url,
                        "kdocs_url": config.kdocs_url,
                        "local_file": config.local_file,
                        "regions": [region.__dict__ for region in config.regions],
                    }
                    for config in self.configs
                ],
            }
        )

    def apply_runtime_config(self, config: dict[str, Any]) -> None:
        runtime_config = normalize_runtime_config(config)
        self.recent_urls = runtime_config["recent_urls"]
        self.document_titles = runtime_config.get("document_titles", {})
        self.browser_mode.set(runtime_config["browser_mode"])
        self.cdp_url.set(runtime_config["cdp_url"])
        self.configs = [self.document_config_from_config(row) for row in runtime_config["configs"]]
        self.remember_mapping_urls()
        self.refresh_tree()
        self.save_current_config()

    def import_config(self) -> None:
        path_text = filedialog.askopenfilename(
            parent=self,
            title="导入写入配置",
            filetypes=[("配置文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path_text:
            return
        try:
            runtime_config = load_config_export(Path(path_text))
            self.apply_runtime_config(runtime_config)
        except Exception as exc:
            messagebox.showerror("导入配置失败", str(exc), parent=self)
            self.log(f"导入配置失败：{exc}")
            return
        self.log(f"已导入配置：{Path(path_text).name}")
        messagebox.showinfo("导入配置", "配置已导入并保存。", parent=self)

    def export_config(self) -> None:
        default_name = f"写入配置_{datetime.now().strftime('%Y%m%d')}.json"
        path_text = filedialog.asksaveasfilename(
            parent=self,
            title="导出写入配置",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("配置文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path_text:
            return
        try:
            save_config_export(Path(path_text), self.current_runtime_config())
        except Exception as exc:
            messagebox.showerror("导出配置失败", str(exc), parent=self)
            self.log(f"导出配置失败：{exc}")
            return
        self.log(f"配置已导出至：{path_text}")
        messagebox.showinfo("导出配置", "配置已导出。", parent=self)

    def selected_config_indexes(self) -> list[int]:
        selected = self.tree.selection()
        return sorted(self.tree.index(item) for item in selected)

    def flatten_configs(self, configs: list[DocumentConfig] | None = None) -> list[MappingRow]:
        mappings: list[MappingRow] = []
        for config in (configs if configs is not None else self.configs):
            for region in config.regions:
                mappings.append(
                    MappingRow(
                        kdocs_url=config.kdocs_url,
                        local_file=config.local_file,
                        source_sheet=region.source_sheet,
                        source_start=region.source_start,
                        source_end=region.source_end,
                        target_sheet=region.target_sheet,
                        target_start=region.target_start,
                        target_end=region.target_end,
                        source_type=config.source_type,
                        source_url=config.source_url,
                    )
                )
        return mappings

    def prepare_selected(self) -> list[PreparedMapping]:
        indexes = self.selected_config_indexes()
        if not indexes:
            raise ValueError("请先选择至少一项配置，再预览或写入。")
        configs = [self.configs[index] for index in indexes]
        return prepare_mappings(
            self.flatten_configs(configs),
            browser_mode=self.browser_mode.get(),
            cdp_url=self.cdp_url.get().strip(),
            log=lambda _message: None,
        )

    def preview(self) -> None:
        if getattr(getattr(self, "task_registry", None), "remote_business", False) is True:
            self._remote_write(preview=True)
            return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在写入", "写入任务正在进行，请等待完成。", parent=self)
            return
        self.clear_log()
        self.log_console.status.set("正在预览…")
        try:
            prepared = self.prepare_selected()
        except Exception as exc:
            diagnosis = explain_error(type(exc).__name__ + ": " + str(exc))
            messagebox.showerror("预览失败", "\n\n".join(diagnosis) if diagnosis else str(exc), parent=self)
            self.log(f"预览失败：{exc}")
            self.detail_log(f"PREVIEW ERROR: {exc}")
            self.detail_log(traceback.format_exc())
            return
        urls = len({item.kdocs_url for item in prepared})
        self.detail_log_prepared("PREVIEW OK", prepared)
        self.log(f"预览通过：共 {len(prepared)} 个写入区域，涉及 {urls} 份目标表格。")

    def write(self) -> None:
        if getattr(getattr(self, "task_registry", None), "remote_business", False) is True:
            self._remote_write(preview=False)
            return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在写入", "写入任务正在进行，请等待完成。", parent=self)
            return
        self.clear_log()
        self.log_console.status.set("正在检查写入配置…")
        try:
            prepared = self.prepare_selected()
        except Exception as exc:
            diagnosis = explain_error(type(exc).__name__ + ": " + str(exc))
            messagebox.showerror("暂时无法写入", "\n\n".join(diagnosis) if diagnosis else str(exc), parent=self)
            self.log(f"暂时无法写入：{exc}")
            self.detail_log(f"WRITE BLOCKED: {exc}")
            self.detail_log(traceback.format_exc())
            return

        self.log_console.status.set("正在写入…")
        self.save_current_config()
        self.detail_log_prepared("WRITE PREPARED", prepared)
        self.worker = self._start_tracked_thread(
            self.write_worker,
            args=(prepared, self.browser_mode.get(), self.cdp_url.get().strip()),
            name="wps-write",
        )

    def _remote_write(self, *, preview: bool) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在写入", "写入任务正在进行，请等待完成。", parent=self)
            return
        indexes = self.selected_config_indexes()
        if not indexes:
            messagebox.showerror("预览失败" if preview else "暂时无法写入", "请先选择至少一项配置，再预览或写入。", parent=self)
            return
        config = self.current_runtime_config()
        self.clear_log()
        self.log_console.status.set("正在预览…" if preview else "正在检查写入配置…")
        if not preview:
            self.save_current_config()
        def worker():
            try:
                result = self.task_registry.wps_action("preview" if preview else "run", config, indexes, self.log)
                self.log(("预览通过：" if preview else "写入完成：") + result.get("message", "已完成"))
            except Exception as exc:
                failure = getattr(exc, "log_details", str(exc))
                diagnosis = explain_error(str(exc))
                if hasattr(exc, "log_details") and diagnosis:
                    failure = diagnosis[0] + "\n" + failure
                self.log(("预览失败：" if preview else "操作失败：") + failure)
                if hasattr(exc, "log_details"):
                    # The business traceback already arrived through self.log.
                    # Keep it in the daily diagnostic file without replaying it.
                    self.detail_log(str(exc), show_traceback=False)
                else:
                    self.detail_log(traceback.format_exc())
                if preview:
                    self._remote_messages.put(("预览失败", str(exc)))
        self.worker = self._start_tracked_thread(worker, name="wps-preview" if preview else "wps-write")

    def write_worker(self, prepared: list[PreparedMapping], browser_mode: str, cdp_url: str) -> None:
        try:
            self.log(f"开始写入，共 {len(prepared)} 个区域。")
            self.detail_log(f"WRITE STARTED: browser_mode={browser_mode}; cdp_url={cdp_url}; regions={len(prepared)}")
            by_index = {item.index: item for item in prepared}
            all_ok = True

            groups: dict[str, list[PreparedMapping]] = {}
            for item in prepared:
                groups.setdefault(item.kdocs_url, []).append(item)

            for url, group in groups.items():
                self.detail_log(f"WRITE TARGET: url={url}; regions={len(group)}")
                result = write_to_wps(url, group, self.log, browser_mode=browser_mode, cdp_url=cdp_url)
                self.detail_log(f"WRITE TARGET RESULT: url={url}; saved={result.get('saved')}; returned={len(result.get('results', []))}")
                for entry in result.get("results", []):
                    item = by_index[entry["index"]]
                    mismatches = compare_matrix(item.values, entry.get("value"), item.rows, item.cols)
                    if mismatches:
                        all_ok = False
                        self.detail_log(
                            f"VERIFY MISMATCH: index={item.index}; target={item.target_sheet}!{item.target_range}; "
                            f"mismatches={mismatches!r}; read_value={entry.get('value')!r}"
                        )
                        self.log(f"第 {item.index} 个区域回读不一致：{item.target_sheet}!{item.target_range}")
                        for mismatch in mismatches:
                            self.log(f"  {mismatch}")
                    else:
                        self.detail_log(f"VERIFY OK: index={item.index}; target={item.target_sheet}!{item.target_range}")
            if all_ok:
                self.detail_log(f"WRITE OK: regions={len(prepared)}")
                self.log(f"写入完成：{len(prepared)} 个区域已保存，回读核对一致。")
            else:
                self.detail_log("WRITE WARNING: verification mismatches detected")
                self.log("写入已结束，但回读数据与来源不一致，请查看上方差异。")
        except Exception as exc:
            self.log(f"操作失败：{exc}")
            self.log("详细错误信息已保存到日志目录。")
            self.detail_log(f"WRITE ERROR: {exc}")
            self.detail_log(traceback.format_exc())


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("WPS 写入工具")
        self.geometry("1120x760")
        self.minsize(980, 640)
        self.writer_frame = WpsWriterFrame(self)
        self.writer_frame.pack(fill="both", expand=True)
        center_window(self, 1120, 760)


if __name__ == "__main__":
    if "--launch-browser" in sys.argv:
        launch_result = ensure_wps_dedicated_chrome(
            "http://127.0.0.1:9222", print
        )
        print(
            "WPS 专用浏览器已就绪"
            f"（{'已启动' if launch_result.get('started') else '已连接'}）。"
        )
    else:
        app = App()
        app.mainloop()
