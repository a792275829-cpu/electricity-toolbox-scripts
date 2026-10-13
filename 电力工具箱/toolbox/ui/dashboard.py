from __future__ import annotations

import tkinter as tk
from datetime import date
from tkinter import ttk
from typing import Callable

from ..runtime import TaskRegistry
from ..tasks import TaskState


class DashboardPage(ttk.Frame):
    def __init__(self, parent: tk.Misc, registry: TaskRegistry, navigate: Callable[[str], None], paths=None) -> None:
        super().__init__(parent, padding=(28, 22))
        self.registry = registry
        self.navigate = navigate
        self.columnconfigure(0, weight=1)
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="今日概览", style="Small.TLabel").pack(side="left")
        ttk.Label(header, text=date.today().strftime("%Y / %m / %d"), style="Small.TLabel").pack(side="right")

        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="ew", padx=26, pady=(68, 0))
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="开始今天的工作", style="Hero.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="选择一个工具，继续你的日常工作。", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 20))

        self.metrics = ttk.Frame(body)
        self.metrics.grid(row=2, column=0, sticky="w", pady=(0, 32))
        self.metric_vars = {key: tk.StringVar(self, "0") for key in ("运行中", "已完成", "失败")}
        for column, (label, variable) in enumerate(self.metric_vars.items()):
            item = ttk.Frame(self.metrics)
            item.grid(row=0, column=column, padx=(0, 24))
            ttk.Label(item, textvariable=variable, style="Metric.TLabel").pack(side="left")
            ttk.Label(item, text=label, style="Small.TLabel").pack(side="left", padx=(6, 0))

        ttk.Label(body, text="常用工具", style="Small.TLabel").grid(row=3, column=0, sticky="w", pady=(0, 12))
        quick = ttk.Frame(body)
        quick.grid(row=4, column=0, sticky="ew")
        tools = (
            ("早报生成", "匹配每日数据，生成生产经营报告"),
            ("出清结果汇总上传", "汇总电量，预览并上传数据"),
            ("现货价格分析表更新", "更新日前与实时 24 点电价"),
            ("市场表更新", "抓取市场数据，更新年度工作簿"),
            ("出力受限分析", "从出清文件生成分析报告"),
            ("WPS写入工具", "将指定区域写入金山文档"),
        )
        self.quick_buttons = {}
        for index, (name, description) in enumerate(tools):
            column = index % 2
            card = ttk.Frame(quick, style="Card.TFrame", padding=(18, 14))
            card.grid(row=index // 2, column=column, sticky="nsew", padx=(0, 12) if column == 0 else (0, 0), pady=(0, 12))
            card.columnconfigure(0, weight=1)
            quick.columnconfigure(column, weight=1, uniform="quick")
            button = ttk.Button(card, text=name, command=lambda value=name: navigate(value), style="Link.TButton")
            button.grid(row=0, column=0, sticky="ew")
            self.quick_buttons[name] = button
            arrow = ttk.Label(card, text="↗", style="CardMuted.TLabel", cursor="hand2")
            arrow.grid(row=0, column=1, padx=(12, 0))
            arrow.bind("<Button-1>", lambda _event, value=name: navigate(value))
            detail = ttk.Label(card, text=description, style="CardMuted.TLabel", wraplength=250)
            detail.grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 2))
            card.bind("<Configure>", lambda event, label=detail: self._resize_detail(event, label))
        ttk.Label(body, text="⌘ F  搜索模块     ·     Esc  清空搜索", style="Small.TLabel").grid(row=5, column=0, sticky="w", pady=(24, 0))
        self._after_id: str | None = None
        self._refresh()

    @staticmethod
    def _resize_detail(event, label: ttk.Label) -> None:
        wraplength = max(130, event.width - 42)
        if int(label.cget("wraplength")) != wraplength:
            label.configure(wraplength=wraplength)

    def _refresh(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        snapshots = self.registry.snapshots()
        counts = {
            "运行中": sum(item.state in {TaskState.CREATED, TaskState.RUNNING, TaskState.CANCELLING} for item in snapshots),
            "已完成": sum(item.state is TaskState.SUCCEEDED for item in snapshots),
            "失败": sum(item.state is TaskState.FAILED for item in snapshots),
        }
        for label, count in counts.items():
            value = str(count)
            if self.metric_vars[label].get() != value:
                self.metric_vars[label].set(value)
        self._after_id = self.after(500, self._refresh)

    def destroy(self) -> None:
        if self._after_id is not None:
            try: self.after_cancel(self._after_id)
            except tk.TclError: pass
        super().destroy()
