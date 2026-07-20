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
        self.listbox.bind("<<ListboxSelect>>", self._sync_cancel_state)
        self.cancel_button = ttk.Button(self, text="取消选中任务", command=self.cancel_selected)
        self.cancel_button.pack(fill="x", pady=(10, 0))
        self._task_ids: list[str] = []
        self._after_id: str | None = None
        self._refresh()

    def _refresh(self) -> None:
        selection = self.listbox.curselection()
        selected_id = (
            self._task_ids[selection[0]]
            if selection and selection[0] < len(self._task_ids)
            else None
        )
        snapshots = self.registry.snapshots()[-12:]
        self._task_ids = [item.task_id for item in snapshots]
        self.listbox.delete(0, "end")
        labels = {TaskState.RUNNING: "运行中", TaskState.CANCELLING: "取消中", TaskState.SUCCEEDED: "完成", TaskState.FAILED: "失败", TaskState.CANCELLED: "取消", TaskState.CREATED: "等待"}
        for item in snapshots:
            self.listbox.insert("end", f"{labels[item.state]} · {item.name}")
        if selected_id in self._task_ids:
            self.listbox.selection_set(self._task_ids.index(selected_id))
        self._sync_cancel_state()
        self._after_id = self.after(500, self._refresh)

    def _sync_cancel_state(self, _event=None) -> None:
        selected = self.listbox.curselection()
        enabled = False
        if selected and selected[0] < len(self._task_ids):
            snapshot = self.registry.snapshot(self._task_ids[selected[0]])
            enabled = snapshot.cancellable and snapshot.state in {
                TaskState.CREATED,
                TaskState.RUNNING,
            }
        self.cancel_button.configure(state="normal" if enabled else "disabled")

    def cancel_selected(self) -> None:
        selected = self.listbox.curselection()
        if selected and selected[0] < len(self._task_ids):
            self.registry.cancel(self._task_ids[selected[0]])
            self._sync_cancel_state()

    def destroy(self) -> None:
        if self._after_id is not None:
            try: self.after_cancel(self._after_id)
            except tk.TclError: pass
        super().destroy()
