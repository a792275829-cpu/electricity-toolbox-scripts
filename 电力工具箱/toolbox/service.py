"""Headless application service, shared task engine and bounded event journal."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import deque
from dataclasses import asdict
from pathlib import Path

from toolbox_credentials import CredentialStore
from toolbox_redaction import redact
from toolbox_storage import data_path
from .runtime import ToolPaths, TaskRegistry, python_executable, utf8_environment
from .service_catalog import desktop_catalog
from .tasks import ProcessRunner, ProcessExecutionError, TaskEvent, TaskEventKind
from .workflows import validate

PROTOCOL_VERSION = 3
RESULT_PREFIX = "TOOLBOX_RESULT="


class ToolboxService:
    def __init__(self, workspace: Path):
        self.paths = ToolPaths(workspace)
        self.registry = TaskRegistry(event_capacity=2000)
        self.closed = threading.Event()
        self._lock = threading.RLock()
        self._sequence = 0
        self._journal = deque(maxlen=2000)
        self._dropped = 0
        self._tasks = {}
        self._requests = {}
        self._tools = {t["id"]: t for t in desktop_catalog(self.paths)}

    def collect_events(self):
        with self._lock:
            dropped = self.registry.events.dropped_count
            if dropped > self._dropped:
                self._sequence += dropped - self._dropped
                self._journal.clear()
                self._dropped = dropped
            for event in self.registry.events.drain(limit=500):
                self._sequence += 1
                self._journal.append(dict(sequence=self._sequence, task_id=event.task_id,
                    kind=event.kind.value, message=redact(event.message)[:16000], progress=event.progress))

    def snapshots(self):
        return [dict(id=s.task_id, tool_id=self._tasks.get(s.task_id, ""), name=s.name,
                     state=s.state.value, cancellable=s.cancellable, message=redact(s.message),
                     result=s.result, error=redact(str(s.error)) if s.error else None)
                for s in self.registry.snapshots()]

    def start(self, params):
        with self._lock:
            if self.closed.is_set():
                raise ValueError("工具箱正在退出")
            tool_id, action = params.get("tool_id"), params.get("action", "run")
            request_id = params.get("request_id")
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                raise ValueError("任务缺少有效请求标识")
            # A timed-out invocation can be retried with the same id without a
            # second upload or workbook write.
            fingerprint = json.dumps([tool_id, action, params.get("values", {})], sort_keys=True, ensure_ascii=False)
            if request_id in self._requests:
                previous, task_id = self._requests[request_id]
                if previous != fingerprint:
                    raise ValueError("同一请求标识不能用于不同参数")
                return task_id
            p = validate(self.paths, tool_id, action, params.get("values", {}))
            if any(self._tasks.get(s.task_id) == tool_id for s in self.registry.active_snapshots()):
                raise ValueError("该工具已有任务运行，请等待完成")
            if self.confirmation(tool_id, action, p) and params.get("confirmed") is not True:
                raise ValueError("请先确认上传、覆盖或文件归类操作")
            writable = tool_id in {"market-table", "transaction-info", "power-benefit", "spot-price", "group-upload"}
            cancellable = action not in {"run", "update"} or not (writable or tool_id in {"wps-writer", "guangdong-price"} or
                          tool_id == "summary-upload" and p["mode"] != "summary")
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="toolbox-task-", suffix=".json", delete=False) as stream:
                request_path = Path(stream.name)
                json.dump(dict(tool_id=tool_id, action=action, values=p), stream, ensure_ascii=False)

            def worker(token, emit):
                result = None
                no_data = None
                first_trace = []
                trace_size = 0
                def output(line):
                    nonlocal result, no_data, trace_size
                    if line.startswith(RESULT_PREFIX):
                        result = json.loads(line[len(RESULT_PREFIX):])
                    else:
                        safe_line = redact(line)
                        if (first_trace or "Traceback (most recent call last)" in line) and trace_size < 16000:
                            first_trace.append(safe_line[:16000 - trace_size])
                            trace_size += len(first_trace[-1])
                        if line.startswith("无数据："):
                            no_data = line.strip()
                        emit(TaskEvent("", TaskEventKind.LOG, message=safe_line[:16000]))
                try:
                    ProcessRunner().run([python_executable(), "-u", "-B",
                        str(Path(__file__).resolve().parents[2] / "desktop/python/task_worker.py"), "--workspace", str(self.paths.workspace),
                        "--request", str(request_path)], cwd=self.paths.workspace,
                        env=utf8_environment(), token=token, on_output=output)
                    if result is None:
                        raise RuntimeError("业务进程未返回完成结果，请核对日志")
                    if no_data:
                        result = dict(message=no_data, outputs=[])
                    return result
                except ProcessExecutionError as exc:
                    if first_trace:
                        raise RuntimeError(f"{exc}\n{''.join(first_trace)}") from exc
                    raise
                finally:
                    request_path.unlink(missing_ok=True)
            try:
                task_id = self.registry.start_callable(self._tools[tool_id]["name"], worker, cancellable=cancellable,
                    on_done=lambda _snapshot: request_path.unlink(missing_ok=True))
            except BaseException:
                request_path.unlink(missing_ok=True)
                raise
            self._tasks[task_id] = tool_id
            self._requests[request_id] = (fingerprint, task_id)
            return task_id

    @staticmethod
    def confirmation(tool_id, action, p):
        if tool_id == "group-upload" and action == "run":
            return "确认上传当前选择的数据文件？"
        if tool_id == "summary-upload":
            return "混放的日前、实时文件会先移入六个单元文件夹。确认继续归类" + ("并上传？" if action == "run" and p["mode"] != "summary" else "？")
        if tool_id == "daily-pdf-parser" and p["mode"] != "upload" and Path(p["output_path"]).exists():
            return "输出 Excel 已存在，确认覆盖？"
        return ""

    def call(self, method, params):
        if not isinstance(params, dict):
            raise ValueError("请求参数必须是对象")
        if method == "ping":
            return dict(protocol=PROTOCOL_VERSION, ui="headless", workspace=str(self.paths.workspace), pid=os.getpid())
        if method == "catalog":
            return list(self._tools.values())
        if method == "auto_match":
            from datetime import date, timedelta
            from toolbox_dates import parse_date_input
            from . import file_rules
            tool = params.get("tool_id")
            if tool == "trade-analysis":
                return dict(files=[str(p) for p in file_rules.find_clearing_files_for_dates(date.today())])
            if tool == "report":
                day = parse_date_input(str(params.get("report_date", "")))
                result = {}
                for key, target in zip(("online_workbook", "day_ahead_workbook", "daily_workbook"), (day-timedelta(days=1), day, day-timedelta(days=2))):
                    path = file_rules.find_workbook_for_day(target)
                    if path:
                        result[key] = str(path)
                return result
            if tool == "group-upload":
                path = file_rules.find_latest_energy_file_for_date(date.today(), self.paths.group_upload_dir)
                return dict(files=[str(path)] if path else [])
            raise ValueError("该工具不支持自动匹配")
        if method == "prepare_task":
            tool, action = params.get("tool_id"), params.get("action", "run")
            values = validate(self.paths, tool, action, params.get("values", {}))
            return dict(values=values, confirmation=self.confirmation(tool, action, values))
        if method == "start":
            return self.start(params)
        if method == "tasks":
            return self.snapshots()
        if method == "events":
            self.collect_events()
            cursor = params.get("after", 0)
            if type(cursor) is not int or cursor < 0:
                raise ValueError("事件游标无效")
            with self._lock:
                oldest = self._journal[0]["sequence"] if self._journal else self._sequence + 1
                return dict(cursor=self._sequence, truncated=cursor < oldest - 1,
                            pending=self.registry.events.pending_count,
                            events=[e for e in self._journal if e["sequence"] > cursor])
        if method == "cancel":
            return self.registry.cancel(str(params.get("task_id", "")))
        if method == "credentials.get":
            saved = CredentialStore(self.paths.workspace).load()
            return dict(username=saved.get("username", ""), configured=bool(saved.get("password")))
        if method == "credentials.save":
            store = CredentialStore(self.paths.workspace)
            store.save(params.get("username", ""), params.get("password") or store.load().get("password", ""))
            return True
        if method == "wps.get":
            path = data_path(self.paths.wps_writer_dir) / "wps_excel_to_kdocs_config.json"
            if not path.is_file():
                path = self.paths.wps_writer_dir / "wps_excel_to_kdocs_config.defaults.json"
            from toolbox_wps_config import normalize_runtime_config
            raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            raw.setdefault("browser_mode", "managed_cdp")
            return normalize_runtime_config(raw)
        if method == "wps.save":
            config = params.get("config")
            if not isinstance(config, dict) or not isinstance(config.get("configs"), list):
                raise ValueError("WPS 配置格式无效")
            from toolbox_credentials import write_private_json
            write_private_json(data_path(self.paths.wps_writer_dir) / "wps_excel_to_kdocs_config.json", config)
            return True
        if method in {"wps.import", "wps.export"}:
            from toolbox_wps_config import load_config_export, save_config_export
            path = Path(str(params.get("path", ""))).expanduser()
            if path.suffix.lower() != ".json":
                raise ValueError("请选择 JSON 配置文件")
            if method == "wps.import":
                return load_config_export(path)
            if path.exists() and params.get("overwrite") is not True:
                raise ValueError("输出文件已存在，请选择新的文件名")
            save_config_export(path, params["config"])
            return True
        if method == "diagnostics":
            from .diagnostics import diagnose_environment
            report = diagnose_environment(self.paths)
            return dict(ok=report.ok, summary=report.summary())
        if method in {"activate", "prepare_update", "abort_update"}:
            if method == "prepare_update" and self.registry.has_running_tasks():
                raise ValueError("请等待正在运行的任务完成后更新")
            return True
        if method in {"request_close", "shutdown", "installer_close", "finish_update"}:
            with self._lock:
                active = self.registry.active_snapshots()
                if active and (method != "shutdown" or params.get("cancel") is not True):
                    raise ValueError("仍有任务运行，请先结束任务或确认取消")
                if any(not s.cancellable for s in active):
                    raise ValueError("仍有不可安全中断的写入任务，请等待完成")
                if self.registry.terminate_all():
                    raise ValueError("后台任务尚未退出，请稍后重试")
                self.closed.set()
                return True
        raise ValueError("不支持的服务操作")
