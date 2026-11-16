"""Offline pilot: real JSON transport, task engine, subprocess and XLSX writer.

Only the worker's online collection boundary is replaced with known sample rows.
This file is selected exclusively by the debug-only WebView pilot host.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]

from toolbox.runtime import load_module
from toolbox.service import ProcessRunner, RESULT_PREFIX
from toolbox_shared.storage import WORKSPACE_ROOT, data_path
from service_bridge import DesktopService, serve


def output_directory():
    if os.environ.get("TOOLBOX_WEBVIEW_PILOT_FIXTURE") != "1" or not os.environ.get("TOOLBOX_DATA_ROOT"):
        raise RuntimeError("WebView 样例服务必须使用隔离数据目录。")
    return data_path(WORKSPACE_ROOT / "上网电量抓取/输出")


class FixtureRunner(ProcessRunner):
    def run(self, command, **kwargs):
        command = list(command)
        original = str(ROOT / "desktop/python/task_worker.py")
        if original not in command:
            raise ValueError("样例服务只允许运行隔离任务。")
        command[command.index(original)] = str(Path(__file__).resolve())
        return super().run(command, **kwargs)


class FixtureService(DesktopService):
    def call(self, method, params):
        if method == "online_energy.settings":
            output = output_directory()
            output.mkdir(parents=True, exist_ok=True)
            return dict(output_dir=str(output), output_exists=True)
        if method in {"start", "prepare_task"}:
            if params.get("tool_id") != "online-energy" or params.get("action", "run") != "run":
                raise ValueError("样例模式仅支持导出上网电量，不连接网站。")
        elif method not in {"ping", "catalog", "tasks", "events", "cancel", "shutdown", "request_close"}:
            raise ValueError("样例模式不支持此操作。")
        return super().call(method, params)


def worker(request_path):
    request = json.loads(request_path.read_text())
    if request["tool_id"] != "online-energy" or request["action"] != "run":
        raise ValueError("样例任务类型错误。")
    module = load_module("webview_pilot_online_energy", ROOT / "上网电量抓取/export_online_energy.py")
    days = module.range_dates(request["values"]["start_date"], request["values"]["end_date"])
    if len(days) > 3:
        raise ValueError("样例模式每次最多导出 3 天。")
    output = output_directory() / uuid4().hex[:12]
    for step in range(12):
        print(f"样例数据准备 {step + 1}/12", flush=True)
        time.sleep(0.2)
    import openpyxl
    outputs = []
    for index, day in enumerate(days):
        value = 12.5 + index
        path = module.save_excel(day, [dict(company="汕头电厂", unit="#1", time="00:00", onlineEle=value)], output)
        book = openpyxl.load_workbook(path)
        try:
            sheet = book["汇总"]
            if [sheet["A1"].value, sheet["A2"].value, sheet["A3"].value, sheet["A3"].number_format] != [
                "汕头 实际上网电量（万千瓦时）", "1号机", value, "General"
            ]:
                raise RuntimeError("样例工作簿回读不一致。")
        finally:
            book.close()
        print(f"已保存并回读：{path}", flush=True)
        outputs.append(str(path))
    print(RESULT_PREFIX + json.dumps(dict(message=f"已生成 {len(outputs)} 个样例工作簿，回读验证通过。", outputs=outputs), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    output_directory()  # Fail before starting if isolation is missing.
    if args.request:
        worker(args.request)
    else:
        import toolbox.service
        import service_bridge
        toolbox.service.ProcessRunner = FixtureRunner
        service_bridge.DesktopService = FixtureService
        if os.environ.get("TOOLBOX_WEBVIEW_PILOT") == "1":
            receipt = ROOT / "logs/webview-pilot-process.json"
            receipt.parent.mkdir(exist_ok=True)
            temporary = receipt.with_suffix(".tmp")
            temporary.write_text(json.dumps(dict(ui_pid=os.getppid(), service_pid=os.getpid()), indent=2))
            temporary.replace(receipt)
        serve(args.workspace)
