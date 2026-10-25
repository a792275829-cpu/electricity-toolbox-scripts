"""Measure sidebar dispatch plus Tk/macOS drawing with real, idle tool pages.

Use the installed app's Python/Tcl/Tk to compare packaged rendering. Run source
revisions separately. These timings include update(), not a guarantee
of the compositor's final pixel presentation. No business task is started.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--switches", type=int, default=300)
    parser.add_argument("--burst-size", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--interactive", action="store_true", help="手动切换，逐次保存诊断记录")
    parser.add_argument("--data-root", type=Path, help="使用指定本地配置目录；默认隔离临时目录")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.switches < 1 or args.burst_size < 1 or args.interval_ms < 0:
        parser.error("switches/burst-size must be positive; interval-ms must be nonnegative")

    with tempfile.TemporaryDirectory(prefix="toolbox-switch-") as folder, patch.dict(
        os.environ, {"TOOLBOX_DATA_ROOT": str(args.data_root.resolve()) if args.data_root else folder}
    ):
        from toolbox.app import ToolboxApp

        with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}):
            app = ToolboxApp()
        app.geometry("1440x820")
        if not args.interactive:
            app.attributes("-topmost", True)
        app.lift()
        app.focus_force()
        names = list(app.nav_buttons)

        remaps = []
        original = app.show_page

        def show(name):
            remaps.append(name)
            return original(name)

        app.show_page = show
        rows = []
        clicks = 0
        failures = []

        def callback_error(_kind, value, _traceback):
            failures.append(str(value))
            app.quit()

        app.report_callback_exception = callback_error

        if args.interactive:
            app.title("电力工具箱 · 模块切换诊断")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            request = app.show_page
            sequence = 0
            finish_after_id = None
            run_id = time.time_ns()
            pressed, released = {}, {}
            last_tick = time.perf_counter()
            max_loop_lag = 0.0

            def heartbeat():
                nonlocal last_tick, max_loop_lag
                now = time.perf_counter()
                max_loop_lag = max(max_loop_lag, (now - last_tick) * 1000 - 20)
                last_tick = now
                app.after(20, heartbeat)

            for name, button in app.nav_buttons.items():
                button.bind("<ButtonPress-1>",
                            lambda _event, value=name: pressed.__setitem__(value, time.perf_counter()), add="+")
                button.bind("<ButtonRelease-1>",
                            lambda _event, value=name: released.__setitem__(value, time.perf_counter()), add="+")
            app.after(20, heartbeat)

            def trace_request(name):
                nonlocal sequence, finish_after_id
                sequence += 1
                number = sequence
                if finish_after_id is not None:
                    app.after_cancel(finish_after_id)
                    finish_after_id = None
                started = time.perf_counter()
                press_at, release_at = pressed.pop(name, None), released.pop(name, None)
                before = len(remaps)
                request(name)
                dispatch_ms = (time.perf_counter() - started) * 1000

                def finish():
                    nonlocal finish_after_id, max_loop_lag
                    finish_after_id = None
                    # Include pending layout, map events and Aqua drawing, not
                    # only the Python page-dispatch function's return time.
                    try:
                        app.update()
                        if not app.winfo_exists():
                            return
                    except tk.TclError:
                        return
                    finished = time.perf_counter()
                    row = {"run_id": run_id, "sequence": number, "target": name,
                           "superseded": sequence != number,
                           "dispatch_ms": round(dispatch_ms, 3),
                           "elapsed_ms": round((finished - started) * 1000, 3),
                           "press_to_paint_ms": round((finished - press_at) * 1000, 3) if press_at else None,
                           "release_to_paint_ms": round((finished - release_at) * 1000, 3) if release_at else None,
                           "event_loop_lag_ms": round(max_loop_lag, 3),
                           "dispatches": len(remaps) - before, "pages": len(app.pages),
                           "images": len(app.tk.call("image", "names")),
                           "commands": len(app.tk.call("info", "commands")),
                           "timers": len(app.tk.call("after", "info")),
                           "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss /
                                                 (1024 ** 2 if sys.platform == "darwin" else 1024), 2)}
                    max_loop_lag = 0.0
                    with args.output.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")

                finish_after_id = app.after(0, finish)

            app.show_page = trace_request
            try:
                app.mainloop()
                if failures:
                    raise RuntimeError("Diagnostic callback failed: " + failures[0])
            finally:
                try:
                    app.destroy()
                except tk.TclError:
                    # A normal close already destroys the Tk application.
                    pass
            return

        def sample():
            nonlocal clicks
            requested = []
            before = len(remaps)
            started = time.perf_counter()
            for _ in range(min(args.burst_size, args.switches - clicks)):
                name = names[clicks % len(names)]
                app.nav_buttons[names[(clicks - 1) % len(names)]].event_generate("<Leave>")
                button = app.nav_buttons[name]
                button.event_generate("<Enter>", x=20, y=10)
                button.event_generate("<ButtonPress-1>", x=20, y=10)
                button.event_generate("<ButtonRelease-1>", x=20, y=10)
                requested.append(name)
                clicks += 1
            app.update()
            rows.append({
                "clicks": clicks, "target": requested[-1],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "dispatches": len(remaps) - before,
                "pages": len(app.pages), "timers": len(app.tk.call("after", "info")),
                "images": len(app.tk.call("image", "names")),
                "commands": len(app.tk.call("info", "commands")),
                "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss /
                                      (1024 ** 2 if sys.platform == "darwin" else 1024), 2),
            })
            if app.current_page != requested[-1]:
                raise AssertionError("Navigation did not finish at the last requested module")
            if clicks < args.switches:
                app.after(args.interval_ms, sample)
            else:
                app.quit()

        app.after(100, sample)
        try:
            app.mainloop()
            if failures:
                raise RuntimeError("Benchmark callback failed: " + failures[0])
            if clicks != args.switches or app.registry.has_running_tasks():
                raise AssertionError("Benchmark incomplete or unexpected business task")
            values = [row["elapsed_ms"] for row in rows]
            result = {"switches": clicks,
                      "burst_size": args.burst_size, "tk": app.tk.call("info", "patchlevel"),
                      "median_ms": round(statistics.median(values), 3), "max_ms": max(values),
                      "rows": rows}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
