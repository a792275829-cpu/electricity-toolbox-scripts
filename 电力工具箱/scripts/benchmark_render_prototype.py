"""Compare the scoped prototype without reading credentials or running tools.

Run baseline/prototype in separate processes and compare matching screenshots.
Measurements include Tk event processing and drawing submission, not the time
at which the macOS compositor presents the final pixels on the screen.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from collections import deque
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toolbox.app import ToolboxApp


def mac_window_id() -> str:
    # Query only this process's windows; capture by window ID so another app
    # moving in front cannot appear in the screenshots.
    source = '''import AppKit; import CoreGraphics
let pid = Int32(CommandLine.arguments[1])!
NSRunningApplication(processIdentifier: pid)?.activate(options: [])
let windows = CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as! [[String: Any]]
for w in windows where (w[kCGWindowOwnerPID as String] as? Int32) == pid {
    if let b = w[kCGWindowBounds as String] as? [String: Double], (b["Width"] ?? 0) >= 500, (b["Height"] ?? 0) >= 400 {
        print(w[kCGWindowNumber as String]!)
    }
}
'''
    result = subprocess.run(["/usr/bin/swift", "-e", source, str(os.getpid())],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip().splitlines()[0]


def distribution(values):
    ordered = sorted(values)
    return {"median_ms": round(statistics.median(values), 3),
            "p95_ms": round(ordered[int((len(ordered) - 1) * .95)], 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "prototype"), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--hover-interval-ms", type=int, default=35)
    args = parser.parse_args()
    with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}):
        app = ToolboxApp(render_prototype=args.mode == "prototype")
    # Exclude unrelated WPS imports from this controlled rendering experiment.
    app.after_cancel(app._warmup_after_id)
    app._warmup_after_id = None
    app.geometry("1440x820+0+40")
    app.attributes("-topmost", True)
    app.lift()
    app.update()
    window_id = mac_window_id() if sys.platform == "darwin" else None
    app.update()
    start = time.perf_counter()
    app.show_page("出清结果汇总上传")
    app.update()
    first = (time.perf_counter() - start) * 1000
    page = app.pages["出清结果汇总上传"]
    page.input_dir.set("/示例/复盘/2026-10-09")
    page.output_file.set("/示例/复盘/2026-10-09/2026-10-09_汇总.xlsx")
    app.diagnostic_center.summary_var.set("检查于 09:00")
    app.update()
    if app.surface_cache:
        app.surface_cache.flush()
        app.update()

    summary_switch, overview_switch = [], []
    for _ in range(60):
        for name, samples in (("今日概览", overview_switch), ("出清结果汇总上传", summary_switch)):
            start = time.perf_counter()
            app.show_page(name)
            app.update()
            samples.append((time.perf_counter() - start) * 1000)

    hover = []
    buttons = list(app.nav_buttons.values())
    for index in range(120):
        start = time.perf_counter()
        buttons[(index - 1) % len(buttons)].event_generate("<Leave>")
        buttons[index % len(buttons)].event_generate("<Enter>", x=20, y=10)
        app.update()
        hover.append((time.perf_counter() - start) * 1000)

    lag = []
    last = [time.perf_counter()]
    callbacks = []
    moving = [0]

    def heartbeat():
        now = time.perf_counter()
        lag.append(max(0, (now - last[0]) * 1000 - 5))
        last[0] = now
        callbacks.append(app.after(5, heartbeat))

    def move():
        index = moving[0]
        buttons[(index - 1) % len(buttons)].event_generate("<Leave>")
        buttons[index % len(buttons)].event_generate("<Enter>", x=20, y=10)
        moving[0] += 1
        callbacks.append(app.after(args.hover_interval_ms, move))

    callbacks.append(app.after(5, heartbeat))
    callbacks.append(app.after(args.hover_interval_ms, move))
    app.after(2000, app.quit)
    cpu_start = time.process_time()
    app.mainloop()
    cpu = (time.process_time() - cpu_start) * 1000
    for callback in callbacks:
        app.after_cancel(callback)
    result = {"mode": args.mode, "first_summary_ms": round(first, 3),
              "summary_switch": distribution(summary_switch),
              "overview_switch": distribution(overview_switch),
              "hover_draw": distribution(hover), "hover_event_lag": distribution(lag),
              "hover_2s_cpu_ms": round(cpu, 3), "hover_interval_ms": args.hover_interval_ms}
    if app.surface_cache:
        result["cache"] = {"entries": len(app.surface_cache.entries),
                           "pixels": app.surface_cache.pixel_count,
                           "builds": app.surface_cache.build_count}

    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    if args.screenshots:
        if sys.platform != "darwin" or args.output is None:
            parser.error("--screenshots requires macOS and --output")
        pending = deque((size, state) for size in ("1440x820", "1100x680")
                        for state in ("normal", "hover", "focus", "pressed", "disabled"))

        def next_capture():
            if not pending:
                app.quit()
                return
            size, state = pending.popleft()
            app.geometry(size + "+0+40")
            for button in (*buttons, page.run_button):
                button.state(("!active", "!focus", "!pressed", "!disabled"))
            if state != "normal":
                flag = "active" if state == "hover" else state
                app.nav_buttons["风力预测截图"].state((flag,))
                page.run_button.state((flag,))

            def capture():
                process = subprocess.Popen(["/usr/sbin/screencapture", "-x", "-o", "-l", window_id,
                                            str(args.output / f"{args.mode}-{size}-{state}.png")])

                def poll_capture():
                    if process.poll() is None:
                        app.after(15, poll_capture)
                    elif process.returncode:
                        raise RuntimeError(f"Screenshot failed: {process.returncode}")
                    else:
                        app.after(80, next_capture)

                poll_capture()

            app.after(200, capture)

        app.after(200, next_capture)
        app.mainloop()
    if args.output:
        (args.output / f"{args.mode}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    app.destroy()


if __name__ == "__main__":
    main()
