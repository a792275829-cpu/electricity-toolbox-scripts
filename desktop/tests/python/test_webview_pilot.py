from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / "desktop/tests/python/webview_pilot_entry.py"


class WebViewPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = (Path(self.temp.name) / "data").resolve()
        env = {**os.environ, "TOOLBOX_WEBVIEW_PILOT_FIXTURE": "1", "TOOLBOX_CODE_ROOT": str(ROOT),
               "TOOLBOX_DATA_ROOT": str(self.data)}
        self.proc = subprocess.Popen([sys.executable, "-u", str(ENTRY), "--workspace", str(ROOT)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env=env)
        self.rows = queue.Queue()
        def read():
            for line in self.proc.stdout:
                self.rows.put(json.loads(line))
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.assertEqual(self.rows.get(timeout=10)["event"]["kind"], "service-ready")
        self.sequence = 0

    def tearDown(self):
        if self.proc.poll() is None:
            self.rpc("shutdown", {"cancel": True})
            self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.reader.join(timeout=2)
        error = self.proc.stderr.read()
        self.proc.stdout.close()
        self.proc.stderr.close()
        self.temp.cleanup()
        self.assertEqual(self.proc.returncode, 0, error)

    def rpc(self, method, params=None):
        self.sequence += 1
        self.proc.stdin.write(json.dumps(dict(id=self.sequence, method=method, params=params or {})) + "\n")
        self.proc.stdin.flush()
        while True:
            row = self.rows.get(timeout=10)
            if row.get("id") == self.sequence:
                return row

    def value(self, method, params=None):
        row = self.rpc(method, params)
        self.assertNotIn("error", row, row)
        return row["result"]

    def start(self, **values):
        params = dict(tool_id="online-energy", action="run", request_id="same-request",
                      values={"start_date": "2026-11-10", "end_date": "2026-11-11", **values})
        task_id = self.value("start", params)
        self.assertEqual(self.value("start", params), task_id)
        return task_id

    def finish(self, task_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            task = next(task for task in self.value("tasks") if task["id"] == task_id)
            if task["state"] in {"succeeded", "cancelled", "failed"}:
                return task
            time.sleep(0.05)
        self.fail("Fixture task did not finish")

    def test_real_transport_writer_and_readback_with_request_deduplication(self):
        self.assertEqual(self.value("ping")["ui"], "headless")
        settings = self.value("online_energy.settings")
        self.assertTrue(Path(settings["output_dir"]).is_relative_to(self.data))
        task = self.finish(self.start())
        self.assertEqual(task["state"], "succeeded", task)
        self.assertEqual(len(self.value("tasks")), 1)
        paths = [Path(path) for path in task["result"]["outputs"]]
        self.assertEqual([path.name for path in paths], ["上网电量汇总_2026-11-10.xlsx", "上网电量汇总_2026-11-11.xlsx"])
        import openpyxl
        for index, path in enumerate(paths):
            book = openpyxl.load_workbook(path)
            try:
                sheet = book["汇总"]
                self.assertEqual([sheet["A1"].value, sheet["A2"].value, sheet["A3"].value],
                                 ["汕头 实际上网电量（万千瓦时）", "1号机", 12.5 + index])
                self.assertEqual(sheet["A3"].number_format, "General")
                self.assertIsNone(sheet["A4"].value)
                self.assertEqual(sheet["A28"].value, "清远 实际上网电量（万千瓦时）")
                self.assertEqual(sheet["B28"].value, f"清远 2026-11-{12 + index} 计划电量（万千瓦时）")
                self.assertEqual([sheet["A29"].value, sheet["B29"].value], ["1号机", "1号机"])
                self.assertEqual([sheet["A30"].value, sheet["B30"].value], [125 + 10 * index, 25])
                self.assertEqual([sheet["A30"].number_format, sheet["B30"].number_format], ["General", "General"])
                self.assertEqual([sheet["A31"].value, sheet["B31"].value], [None, None])
            finally:
                book.close()

    def test_cancel_stops_real_worker_without_creating_output(self):
        task_id = self.start()
        self.assertTrue(self.value("cancel", {"task_id": task_id}))
        self.assertEqual(self.finish(task_id)["state"], "cancelled")
        self.assertEqual(list(self.data.rglob("*.xlsx")), [])

    def test_reversed_dates_are_rejected_before_task_creation(self):
        reply = self.rpc("prepare_task", dict(tool_id="online-energy", action="run",
                         values=dict(start_date="2026-11-12", end_date="2026-11-10")))
        self.assertIn("结束日期不能早于开始日期", reply["error"])
        self.assertEqual(self.value("tasks"), [])

    def test_fixture_rejects_login_and_other_tools(self):
        for tool, action in (("online-energy", "login"), ("group-upload", "run")):
            with self.subTest(tool=tool, action=action):
                reply = self.rpc("start", dict(tool_id=tool, action=action))
                self.assertIn("不连接网站", reply["error"])
        self.assertIn("不支持", self.rpc("credentials.get")["error"])

    def test_worker_failure_keeps_first_error_and_exit_code(self):
        task = self.finish(self.start(end_date="2026-11-14"))
        self.assertEqual(task["state"], "failed")
        self.assertIn("样例模式每次最多导出 3 天", task["error"])
        self.assertIn("退出码 1", task["error"])


class SettingsTests(unittest.TestCase):
    def test_settings_only_reads_the_configured_directory(self):
        sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]
        from toolbox.service import ToolboxService
        from unittest.mock import patch, PropertyMock
        from toolbox.runtime import ToolPaths
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "not-created"
            with patch.object(ToolPaths, "online_energy_output_dir", new_callable=PropertyMock, return_value=output):
                self.assertEqual(ToolboxService(ROOT).call("online_energy.settings", {}),
                                 dict(output_dir=str(output), output_exists=False))
            self.assertFalse(output.exists())
