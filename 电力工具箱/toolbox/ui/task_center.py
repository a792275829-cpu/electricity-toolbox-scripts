from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..runtime import TaskRegistry
from ..tasks import TaskState


class TaskCenter(ttk.Frame):
    def __init__(self, parent: tk.Misc, registry: TaskRegistry) -> None:
        super().__init__(parent, padding=12)
        self.registry = registry
        ttk.Label(self, text="任务中心", style="PageTitle.TLabel").pack(anchor="w", pady=(0, 10))
        self.listbox = tk.Listbox(self, width=30, height=12, borderwidth=0)
        self.listbox.pack(fill="both", expand=True)
        self.cancel_button = ttk.Button(self, text="取消选中任务", command=self.cancel_selected)
        self.cancel_button.pack(fill="x", pady=(10, 0))
        self._task_ids: list[str] = []
        self._after_id: str | None = None
        self._refresh()

    def _refresh(self) -> None:
        snapshots = self.registry.snapshots()[-12:]
        self._task_ids = [item.task_id for item in snapshots]
        selection = self.listbox.curselection()
        self.listbox.delete(0, "end")
        labels = {TaskState.RUNNING: "运行中", TaskState.CANCELLING: "取消中", TaskState.SUCCEEDED: "完成", TaskState.FAILED: "失败", TaskState.CANCELLED: "取消", TaskState.CREATED: "等待"}
        for item in snapshots:
            self.listbox.insert("end", f"{labels[item.state]} · {item.name}")
        if selection and selection[0] < len(self._task_ids): self.listbox.selection_set(selection[0])
        self._after_id = self.after(500, self._refresh)

    def cancel_selected(self) -> None:
        selected = self.listbox.curselection()
        if selected and selected[0] < len(self._task_ids):
            self.registry.cancel(self._task_ids[selected[0]])

    def destroy(self) -> None:
        if self._after_id is not None:
            try: self.after_cancel(self._after_id)
            except tk.TclError: pass
        super().destroy()
