"""Capture native form values on the UI thread; execute business work remotely.

The existing pages, callbacks, dialogs and task widgets remain authoritative for
presentation. This adapter never runs their business closures in desktop mode.
"""
from __future__ import annotations
import json
import queue
import threading
import time
from pathlib import Path
from uuid import uuid4
from .runtime import TaskRegistry
from .tasks import TaskCancelled


class BusinessClient:
    def __init__(self, send):
        self.send = send
        self._lock = threading.Lock()
        self._pending = {}

    def accept(self, response):
        with self._lock:
            target = self._pending.get(response.get("rpc_reply"))
        if target is not None:
            try:
                target.put_nowait(response)
            except queue.Full:
                pass

    def disconnected(self):
        with self._lock:
            for target in self._pending.values():
                try:
                    target.put_nowait(dict(error="应用管理进程已断开；请核对任务结果后重新启动。"))
                except queue.Full:
                    pass

    def call(self, method, params=None):
        request_id = uuid4().hex
        target = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[request_id] = target
        try:
            self.send(dict(rpc=dict(id=request_id, method=method, params=params or {})))
            try:
                response = target.get(timeout=35)
            except queue.Empty:
                raise RuntimeError("后台响应超时；写入或上传不会自动重试，请先核对结果。") from None
            if "error" in response:
                raise RuntimeError(response["error"])
            return response.get("result")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)


class RemoteTaskRegistry(TaskRegistry):
    remote_business = True
    def __init__(self, client):
        super().__init__()
        self.client = client
        self._request_ids = {}
        self._request_lock = threading.Lock()

    def execute_remote(self, tool_id, action, values, token, log):
        # Original page dialogs already confirm operations requiring confirmation.
        fingerprint = json.dumps([tool_id, action, values], sort_keys=True, ensure_ascii=False)
        with self._request_lock:
            request_id = self._request_ids.setdefault(fingerprint, uuid4().hex)
        task_id = self.client.call("start", dict(tool_id=tool_id, action=action, values=values,
                                   request_id=request_id, confirmed=True))
        cursor = 0
        cancelled = False
        def consume_events():
            nonlocal cursor
            batch = self.client.call("events", dict(after=cursor))
            cursor = batch["cursor"]
            for event in batch["events"]:
                if event["task_id"] == task_id and event["kind"] == "log":
                    log(event["message"].rstrip("\n"))
            return batch.get("pending", 0)
        while True:
            if token.cancelled and not cancelled:
                cancelled = bool(self.client.call("cancel", dict(task_id=task_id)))
            consume_events()
            snapshot = next(t for t in self.client.call("tasks") if t["id"] == task_id)
            if snapshot["state"] in {"succeeded", "cancelled", "failed"}:
                # Collect output emitted between the last event read and the
                # terminal snapshot, including any remaining bounded batches.
                while consume_events():
                    pass
            if snapshot["state"] == "succeeded":
                with self._request_lock:
                    self._request_ids.pop(fingerprint, None)
                return snapshot["result"]
            if snapshot["state"] == "cancelled":
                with self._request_lock:
                    self._request_ids.pop(fingerprint, None)
                raise TaskCancelled("任务已取消")
            if snapshot["state"] == "failed":
                with self._request_lock:
                    self._request_ids.pop(fingerprint, None)
                raise RuntimeError(snapshot["error"] or "业务任务失败")
            time.sleep(0.15)

    def prepare_page_worker(self, page, status, *, command=None, process=False):
        name = page.__class__.__name__
        tool_id = {
            "OnlineEnergyPage": "online-energy", "WindForecastPage": "wind-forecast",
            "SectionAnalysisPage": "section-analysis", "TradeAnalysisPage": "trade-analysis",
            "MarketTableUpdatePage": "market-table", "TransactionInfoUpdatePage": "transaction-info",
            "PowerBenefitUpdatePage": "power-benefit", "SpotPriceUpdatePage": "spot-price",
            "DailyPdfParserPage": "daily-pdf-parser", "MarketReportPage": "market-report",
            "ReportPage": "report", "GuangdongPricePage": "guangdong-price",
            "GroupUploadPage": "group-upload", "SummaryUploadPage": "summary-upload",
        }.get(name)
        if tool_id is None:
            raise ValueError(f"该页面尚未注册业务服务：{name}")
        get = lambda key: getattr(page, key).get()
        # Read every Tk variable here, before the worker thread is started.
        fields = {
            "online-energy": ("run_date",), "wind-forecast": ("run_date",),
            "section-analysis": ("output_root", "topology_path", "threshold"),
            "trade-analysis": ("output_dir",), "market-table": ("run_date", "workbook"),
            "transaction-info": ("workbook", "start_date", "end_date", "create_backup"),
            "power-benefit": ("start_date", "end_date", "create_backup"),
            "spot-price": ("start_date", "end_date", "overwrite", "create_backup"),
            "daily-pdf-parser": ("pdf_path", "output_path", "mode"),
            "market-report": ("report_date", "day_report", "previous_report", "d2_report", "dongguan_operation", "realtime_maintenance"),
            "report": ("report_date", "online_workbook", "day_ahead_workbook", "daily_workbook", "template_report", "fetch_data", "generate_word"),
            "guangdong-price": ("run_date", "master_workbook", "prediction_output_dir", "training_months"),
            "group-upload": ("force",), "summary-upload": ("input_dir", "output_file", "mode"),
        }[tool_id]
        values = {key: get(key) for key in fields}
        if tool_id in {"section-analysis", "trade-analysis"}:
            values["files"] = [str(p) for p in page.files]
        if tool_id == "group-upload":
            values["files"] = [str(p) for p in page.selected_paths]
        if tool_id == "spot-price":
            for key, label in (("guihu", "归湖"), ("tuolian", "鮀莲"), ("haifeng", "海风")):
                values[key + "_workbook"] = page.station_workbooks[label].get()
                values[key + "_selected"] = page.selected_stations[label].get()
        if tool_id == "power-benefit":
            for index, key in enumerate(("guihu", "tuolian")):
                values[key + "_workbook"] = page.workbooks[index].get()
                values[key + "_selected"] = page.selected[index].get()
        action = "run"
        if command and "--login" in command:
            action = "login"
        elif tool_id == "guangdong-price":
            action = "predict" if process else "update"
            if action == "predict":
                values["master_workbook"] = page.prediction_input.get()
                values["prediction_output_dir"] = str(page.resolve_business_path(values["prediction_output_dir"]))
            values["master_workbook"] = str(page.resolve_business_path(values["master_workbook"]))
        elif tool_id == "summary-upload" and "预览" in status:
            action = "preview"
        if action == "login":
            values = {}

        def worker(token):
            result = self.execute_remote(tool_id, action, values, token, page.append_log)
            if process:
                return 0
            if tool_id == "trade-analysis":
                return [Path(v) for v in result.get("outputs", [])]
            if tool_id == "guangdong-price":
                return {k: Path(v) for k, v in result.get("values", {}).items()}
            if tool_id == "summary-upload":
                page._remote_missing = tuple(result.get("missing", []))
                return Path(result["outputs"][0]) if result.get("outputs") else None
            if tool_id == "report":
                return int(values["fetch_data"]) + int(values["generate_word"])
            return result
        worker._remote_business = True
        return worker

    def wps_action(self, action, config, indexes, log, *, operation=None):
        from .tasks import CancellationToken
        values = dict(config=json.dumps(config, ensure_ascii=False),
                      selected_indexes=",".join(str(i + 1) for i in indexes) or "1", operation=json.dumps(operation or {}))
        return self.execute_remote("wps-writer", action, values, CancellationToken(), log)

    def wps_read(self, method, arguments, log):
        return self.wps_action("inspect", {}, [], log, operation=dict(method=method, arguments=arguments))
