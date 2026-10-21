"""JSON-lines transport for the headless Python business layer (protocol 3)."""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]
from toolbox.service import ToolboxService
from toolbox.tasks import TaskEvent, TaskEventKind
from toolbox_redaction import redact
from github_update import GitHubUpdater


class DesktopService(ToolboxService):
    def __init__(self, workspace, emit):
        super().__init__(workspace)
        self.emit = emit
        self.updater = GitHubUpdater(os.environ.get("TOOLBOX_APP_VERSION", "0.0.0"))
        self.release = None
        self.update_task = None
        self.update_checking = False
        self.update_error = None

    def call(self, method, params):
        if method in {"check_updates", "updates.check"}:
            if self.update_checking:
                return True
            self.update_checking = True
            self.update_error = None
            def check():
                try:
                    self.release = self.updater.check()
                    self.emit(dict(event=dict(kind="update-available", release=asdict(self.release) if self.release else None)))
                except Exception as exc:
                    self.update_error = redact(str(exc))
                    self.emit(dict(event=dict(kind="update-error", message=redact(str(exc)))))
                finally:
                    self.update_checking = False
            threading.Thread(target=check, daemon=True).start()
            return True
        if method == "updates.status":
            return dict(checking=self.update_checking, error=self.update_error,
                        release=asdict(self.release) if self.release else None)
        if method == "updates.download":
            with self._lock:
                if self.closed.is_set():
                    raise ValueError("工具箱正在退出")
                if self.update_task and any(s.task_id == self.update_task for s in self.registry.active_snapshots()):
                    return self.update_task
                release = self.release
                if release is None:
                    raise ValueError("请先检查更新")
                if params.get("version") and params["version"] != release.version:
                    raise ValueError("版本信息已变化，请重新检查更新")
                def download(token, emit):
                    class CancelEvent:
                        def is_set(self):
                            return token.cancelled
                    path = self.updater.download(release, CancelEvent(),
                        lambda fraction: emit(TaskEvent("", TaskEventKind.PROGRESS, progress=fraction)))
                    return dict(message="DMG 已下载并校验。请退出工具箱后运行镜像中的安装命令。", outputs=[str(path)])
                self.update_task = self.registry.start_callable("下载更新 DMG", download)
                return self.update_task
        return super().call(method, params)


def serve(workspace):
    output = sys.stdout
    sys.stdout = sys.stderr
    lock = threading.Lock()
    def send(row):
        with lock:
            output.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            output.flush()
    service = DesktopService(workspace, send)
    send(dict(event=dict(kind="service-ready", protocol=3)))
    # Requests are serialized; long business operations run in the task engine.
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get("method"), str):
                raise ValueError("请求格式无效")
            result = service.call(request["method"], request.get("params", {}))
            send(dict(id=request.get("id"), result=result))
        except Exception as exc:
            send(dict(id=request.get("id") if isinstance(request, dict) else None, error=redact(str(exc))))
        service.collect_events()
        if service.closed.is_set():
            break
    # Parent disconnect never causes a write to be killed halfway through.
    while service.registry.has_running_tasks():
        service.registry.shutdown(timeout=0.2)
        service.collect_events()
    send(dict(event=dict(kind="service-closed")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    serve(parser.parse_args().workspace)
