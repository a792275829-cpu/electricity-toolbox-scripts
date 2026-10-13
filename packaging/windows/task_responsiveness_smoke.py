"""Exercise real Tk pages and subprocesses with temporary data, without websites."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def check(workspace: Path) -> dict:
    sys.path[:0] = [str(workspace), str(workspace / "电力工具箱")]
    with tempfile.TemporaryDirectory(prefix="toolbox-response-") as directory:
        os.environ["TOOLBOX_CODE_ROOT"] = str(workspace)
        os.environ["TOOLBOX_DATA_ROOT"] = str(Path(directory) / "data")
        os.environ["TOOLBOX_PYTHON"] = sys.executable
        from toolbox.app import ToolboxApp
        from toolbox.catalog import default_catalog
        started = time.monotonic()
        app = ToolboxApp(workspace=workspace)
        errors, pulses, gaps = [], [], []
        app.report_callback_exception = lambda *exc: errors.append(str(exc))

        def pulse():
            now = time.monotonic()
            if pulses:
                gaps.append(now - pulses[-1])
            pulses.append(now)
            app.after(50, pulse)

        def pump_until(predicate, timeout=20):
            deadline = time.monotonic() + timeout
            while not predicate():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Tk task did not finish")
                app.update()
                time.sleep(0.005)

        try:
            app.after(50, pulse)
            idle_done = []
            app.after(700, lambda: idle_done.append(True))
            pump_until(lambda: idle_done)
            assert app.pages == {}, "Business pages loaded during startup"
            startup = time.monotonic() - started
            for descriptor in default_catalog():
                app.show_page(descriptor.name)
                app.update()
            wps = app.pages["WPS写入工具"]
            pump_until(lambda: hasattr(wps, "writer_frame"))
            frame = wps.writer_frame

            # Exercise the same task path used by multiple real business pages.
            names = ["导出上网电量", "市场表更新", "交易系统信息更新"]
            code = "import time\nfor i in range(10000):\n print('sample-log-'+str(i))\n if i%1000==0: time.sleep(.01)"
            for name in names:
                page = app.pages[name]
                page.run_process([sys.executable, "-u", "-c", code], cwd=workspace, status="离线响应性测试")
            app.show_page("今日概览")
            pump_until(lambda: all(not app.pages[n].busy and app.pages[n]._log_queue.empty() for n in names))
            for name in names:
                page = app.pages[name]
                assert page.status_var.get() == "已完成"
                assert "sample-log-9999" in page.log_text.get("1.0", "end")

            # Read an actual temporary Excel in preview and write preparation.
            from openpyxl import Workbook
            book = Workbook()
            book.active.title = "Source"
            book.active["A1"] = 123.5
            excel = Path(directory) / "source.xlsx"
            book.save(excel)
            book.close()
            module = sys.modules[frame.__class__.__module__]
            frame.configs = [module.DocumentConfig(
                name="Temporary test", kdocs_url="https://example.invalid/test", local_file=str(excel),
                regions=[module.RegionRow("Source", "A1", "A1", "Target", "B1", "B1")],
            )]
            frame.refresh_tree()
            frame.select_config_indexes([0])
            frame.preview()
            pump_until(lambda: not frame._selection_task_busy)
            received = []

            def offline_write(url, group, log, **kwargs):
                received.extend(group)
                return {"saved": True, "results": [{"index": item.index, "value": item.values} for item in group]}

            with patch.object(module, "write_to_wps", side_effect=offline_write):
                frame.write()
                pump_until(lambda: not frame._selection_task_busy)
            assert len(received) == 1 and received[0].values == [[123.5]], received
            assert not errors, errors
            assert app.registry.events.pending_count <= 10_000
            return {"passed": True, "pages": len(app.pages), "concurrent_tasks": len(names),
                    "process_log_lines": 30000, "heartbeat_count": len(pulses),
                    "max_heartbeat_gap_ms": round(max(gaps, default=0) * 1000),
                    "startup_and_idle_ms": round(startup * 1000),
                    "retained_events": app.registry.events.pending_count,
                    "excel_value_readback": received[0].values,
                    "websites": "mocked; no production upload"}
        finally:
            app.registry.terminate_all()
            app.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = check(args.workspace.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
