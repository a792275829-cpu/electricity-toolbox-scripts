from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..runtime import TaskRegistry
from ..tasks import TaskState


class DashboardPage(ttk.Frame):
    def __init__(self, parent: tk.Misc, registry: TaskRegistry, navigate: Callable[[str], None]) -> None:
        super().__init__(parent, padding=22)
        self.registry = registry
        self.navigate = navigate
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text="今日工作", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self, text="查看任务状态，或从常用操作开始。", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 16))
        self.metrics = ttk.Frame(self)
        self.metrics.grid(row=2, column=0, sticky="ew")
        self.metric_vars = {key: tk.StringVar(self, "0") for key in ("运行中", "已完成", "失败")}
        for column, (label, variable) in enumerate(self.metric_vars.items()):
            card = ttk.Frame(self.metrics, style="Card.TFrame", padding=14)
            card.grid(row=0, column=column, sticky="ew", padx=(0, 10))
            self.metrics.columnconfigure(column, weight=1)
            ttk.Label(card, text=label, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=variable, style="Metric.TLabel").pack(anchor="w")
        quick = ttk.LabelFrame(self, text="快捷操作", padding=14)
        quick.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        for index, name in enumerate(("生成报告", "市场表更新", "上传集团每日数据", "WPS写入工具")):
            ttk.Button(quick, text=name, command=lambda value=name: navigate(value)).grid(row=index // 2, column=index % 2, sticky="ew", padx=5, pady=5)
            quick.columnconfigure(index % 2, weight=1)
        self._after_id: str | None = None
        self._refresh()

    def _refresh(self) -> None:
        snapshots = self.registry.snapshots()
        self.metric_vars["运行中"].set(str(sum(item.state in {TaskState.CREATED, TaskState.RUNNING, TaskState.CANCELLING} for item in snapshots)))
        self.metric_vars["已完成"].set(str(sum(item.state is TaskState.SUCCEEDED for item in snapshots)))
        self.metric_vars["失败"].set(str(sum(item.state is TaskState.FAILED for item in snapshots)))
        self._after_id = self.after(500, self._refresh)

    def destroy(self) -> None:
        if self._after_id is not None:
            try: self.after_cancel(self._after_id)
            except tk.TclError: pass
        super().destroy()
