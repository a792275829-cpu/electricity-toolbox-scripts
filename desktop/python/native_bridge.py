"""Host the original Tk application, without translating any of its widgets.

Tk owns every page, dialog, binding and account editor on its main thread.
In desktop mode, immutable form requests travel through the Rust broker to the
independent business service; source-mode launch retains the original fallback.
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
    def __init__(self, app: ToolboxApp, emit=None):
        self.app = app
        self.maintenance = False
        self._locked_widgets = {}
        self.updater = None
        self.emit = emit

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
        if method in ("begin_update", "cancel_update"):
            if self.emit is None:
                raise ValueError("源码启动模式不自动更新，请使用 DMG 安装的桌面版")
            self.emit({"event": {"kind": method, "version": params.get("version")}})
            return True
        if method in ("update_progress", "update_failed", "update_cancelled"):
            if method != "update_progress":
                self.call("abort_update", {})
            if self.updater:
                self.updater.update_event(method, params)
            return True
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
        if method in ("installer_close", "finish_update"):
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


def serve(root: Path = ROOT, *, remote_business: bool = False):
    protocol = sys.stdout
    # Existing modules may print; keep the transport exclusively JSON-lines.
    sys.stdout = sys.stderr
    lock = threading.Lock()

    def send(value):
        with lock:
            protocol.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
            protocol.flush()

    client = None
    registry = None
    if remote_business:
        from toolbox.ui_tasks import BusinessClient, RemoteTaskRegistry
        client = BusinessClient(send)
        registry = RemoteTaskRegistry(client)
    app = ToolboxApp(workspace=root, registry=registry, check_updates=lambda: service.updater.show())
    service = NativeService(app, emit=send)
    from update_ui import UpdateController
    updater = None
    if client is not None:
        from remote_updater import RemoteUpdater
        updater = RemoteUpdater(client)
    service.updater = UpdateController(app, service, updater=updater)
    close = lambda: service.call("request_close", {})
    app.protocol("WM_DELETE_WINDOW", close)
    if sys.platform == "darwin":
        app.createcommand("::tk::mac::Quit", close)
    requests = queue.Queue()

    def read():
        for line in sys.stdin:
            if client is not None:
                try:
                    response = json.loads(line)
                    if "rpc_reply" in response:
                        client.accept(response)
                        continue
                except (ValueError, TypeError):
                    pass
            requests.put(line)
        if client is not None:
            client.disconnected()
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
    if client is not None and client.call("ping").get("protocol") != 3:
        app.destroy()
        raise RuntimeError("界面与业务后台版本不匹配，请重新安装完整发行版。")
    app.after(40, poll)
    send({"event": {"kind": "native-ready"}})
    app.mainloop()
    send({"event": {"kind": "native-closed"}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--remote-business", action="store_true")
    args = parser.parse_args()
    serve(args.workspace, remote_business=args.remote_business)
