"""Host the original Tk application, without translating any of its widgets.

Only lifecycle and updater requests cross the Tauri boundary. Tk owns every
business page, dialog, binding, task and account editor, on its main thread.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from pathlib import Path
from tkinter import messagebox

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]

from toolbox.app import ToolboxApp


class NativeService:
    def __init__(self, app: ToolboxApp):
        self.app = app
        self.maintenance = False
        self._locked_widgets = {}
        self.updater = None

    def _lock_inputs(self, locked: bool):
        tag = f"ToolboxUpdateLock{id(self.app)}"
        if locked:
            for event in ("<ButtonPress>", "<ButtonRelease>", "<KeyPress>", "<KeyRelease>", "<MouseWheel>"):
                self.app.tk.call("bind", tag, event, "break")
            pending = [self.app]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                self._locked_widgets[widget] = widget.bindtags()
                widget.bindtags((tag, *widget.bindtags()))
        else:
            for widget in self._locked_widgets:
                if widget.winfo_exists():
                    widget.bindtags(tuple(t for t in widget.bindtags() if t != tag))
            self._locked_widgets.clear()
            for event in ("<ButtonPress>", "<ButtonRelease>", "<KeyPress>", "<KeyRelease>", "<MouseWheel>"):
                self.app.unbind_class(tag, event)

    def call(self, method: str, params: dict):
        app = self.app
        if method == "ping":
            return {
                "protocol": 2,
                "ui": "original-tk",
                "workspace": str(app.paths.workspace),
            }
        if method == "tasks":
            return [
                {
                    "id": s.task_id,
                    "name": s.name,
                    "state": s.state.value,
                    "cancellable": s.cancellable,
                }
                for s in app.registry.snapshots()
            ]
        if method == "check_updates":
            if self.updater:
                self.updater.check(manual=True)
            return True
        if method == "activate":
            if self.updater:
                self.updater.activate()
            if not self.maintenance:
                app.deiconify()
                app.lift()
                app.focus_force()
            return not self.maintenance
        if method == "prepare_update":
            if app.registry.has_running_tasks():
                raise ValueError("请等待正在运行的任务完成后更新")
            if app.credentials_panel.dirty and not app.credentials_panel.save():
                raise ValueError(app.credentials_panel.status_var.get())
            self.maintenance = True
            # Keep the footer visible while blocking business input.
            self._lock_inputs(True)
            return True
        if method == "abort_update":
            if self.maintenance:
                self.maintenance = False
                self._lock_inputs(False)
                app.deiconify()
                app.lift()
            return True
        if method == "installer_close":
            if app.registry.has_running_tasks():
                raise ValueError("请等待正在运行的任务完成后安装")
            if app.credentials_panel.dirty and not app.credentials_panel.save():
                raise ValueError("账号保存失败，已阻止安装")
            app.after_idle(app.destroy)
            return True
        if method in ("request_close", "shutdown"):
            if self.maintenance:
                messagebox.showinfo("正在更新", "正在更新，请稍候。", parent=app)
                return False
            # Preserve the original confirmation, cancellation and save behavior.
            app.after_idle(app._on_close)
            return True
        raise ValueError("原版界面仅接受启动、退出和更新请求")


def serve(root: Path = ROOT):
    protocol = sys.stdout
    # Existing modules may print; keep the transport exclusively JSON-lines.
    sys.stdout = sys.stderr
    lock = threading.Lock()

    def send(value):
        with lock:
            protocol.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
            protocol.flush()

    app = ToolboxApp(workspace=root, check_updates=lambda: service.updater.show())
    service = NativeService(app)
    from update_ui import UpdateController
    service.updater = UpdateController(app, service)
    close = lambda: service.call("request_close", {})
    app.protocol("WM_DELETE_WINDOW", close)
    if sys.platform == "darwin":
        app.createcommand("::tk::mac::Quit", close)
    requests = queue.Queue()

    def read():
        for line in sys.stdin:
            requests.put(line)
        requests.put(None)

    def poll():
        # Calling Tk from the reader thread would change focus/dialog behavior.
        for _ in range(32):
            try:
                line = requests.get_nowait()
            except queue.Empty:
                break
            if line is None:
                app.after_idle(app._on_close)
                break
            request = {}
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or not isinstance(
                    request.get("params", {}), dict
                ):
                    raise ValueError("请求必须为对象")
                result = service.call(request["method"], request.get("params", {}))
                send({"id": request.get("id"), "result": result})
            except Exception as exc:
                send(
                    {
                        "id": request.get("id") if isinstance(request, dict) else None,
                        "error": str(exc),
                    }
                )
        app.after(40, poll)

    threading.Thread(target=read, daemon=True).start()
    app.after(40, poll)
    send({"event": {"kind": "native-ready"}})
    app.mainloop()
    send({"event": {"kind": "native-closed"}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    serve(parser.parse_args().workspace)
