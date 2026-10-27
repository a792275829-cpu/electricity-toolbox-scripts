from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "电力工具箱/scripts/benchmark_module_switch.py"


class NavigationBenchmarkTests(unittest.TestCase):
    @staticmethod
    def describe(run: subprocess.CompletedProcess) -> str:
        """Child-process failures are invisible unless the streams come along."""
        return (f"exit={run.returncode}\n"
                f"--- stdout ---\n{run.stdout[-4000:]}\n"
                f"--- stderr ---\n{run.stderr[-4000:]}")

    def test_real_pages_finish_at_requested_module_and_write_paint_timings(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "timings.json"
            run = subprocess.run([sys.executable, str(SCRIPT), "--switches", "2",
                                  "--interval-ms", "0", "--output", str(output)],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(0, run.returncode, self.describe(run))
            if not output.is_file():
                self.fail("timings file missing; child said:\n" + self.describe(run))
            result = json.loads(output.read_text())
        self.assertEqual(2, result["switches"])
        self.assertEqual(["导出上网电量", "风力预测截图"],
                         [row["target"] for row in result["rows"]])
        self.assertEqual(2, result["rows"][-1]["pages"])
        self.assertGreater(result["max_ms"], 0)
        self.assertTrue(all(row["dispatches"] == 1 for row in result["rows"]))

    def test_manual_trace_records_rendered_module_without_form_values(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "manual.jsonl"
            source = r'''
import importlib.util
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
root, script, output = sys.argv[1:]
sys.path[:0] = [root, str(Path(root) / "电力工具箱")]
from toolbox.app import ToolboxApp
original = ToolboxApp.__init__

def build(app, *args, **kwargs):
    original(app, *args, **kwargs)
    started = time.monotonic()
    button = app.nav_buttons["市场表更新"]
    state = {"clicked": False}

    def pump():
        # The row is appended only after the page has painted, so a fixed timer
        # raced a slow CI machine and closed the window before the write. Wait
        # for the file, and click only once the button is actually mapped --
        # event_generate on an unmapped widget is silently dropped.
        if os.path.exists(output) and os.path.getsize(output) > 0:
            app.destroy()
            return
        if time.monotonic() - started > 20:
            sys.stderr.write("trace row missing after 20s: clicked=%s viewable=%s\n"
                             % (state["clicked"], button.winfo_viewable()))
            sys.stderr.flush()
            app.destroy()
            return
        if not state["clicked"]:
            try:
                viewable = button.winfo_viewable()
            except Exception:
                viewable = False
            if viewable:
                button.event_generate("<Enter>", x=20, y=10)
                button.event_generate("<ButtonPress-1>", x=20, y=10)
                button.event_generate("<ButtonRelease-1>", x=20, y=10)
                state["clicked"] = True
        app.after(20, pump)

    app.after(0, pump)

spec = importlib.util.spec_from_file_location("navigation_benchmark", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.argv = [script, "--interactive", "--output", output]
with patch.object(ToolboxApp, "__init__", build):
    module.main()
'''
            run = subprocess.run([sys.executable, "-c", source, str(ROOT), str(SCRIPT), str(output)],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(0, run.returncode, self.describe(run))
            if not output.is_file():
                self.fail("trace file missing; child said:\n" + self.describe(run))
            rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(1, len(rows))
        self.assertEqual("市场表更新", rows[0]["target"])
        self.assertFalse(rows[0]["superseded"])
        self.assertGreaterEqual(rows[0]["elapsed_ms"], rows[0]["dispatch_ms"])
        self.assertGreaterEqual(rows[0]["press_to_paint_ms"], rows[0]["release_to_paint_ms"])
        self.assertGreaterEqual(rows[0]["release_to_paint_ms"], rows[0]["elapsed_ms"])
        self.assertEqual({"run_id", "sequence", "target", "superseded", "dispatch_ms",
                          "elapsed_ms", "dispatches", "pages", "images", "commands",
                          "timers", "peak_rss_mib", "press_to_paint_ms",
                          "release_to_paint_ms", "event_loop_lag_ms"}, set(rows[0]))


if __name__ == "__main__":
    unittest.main()
