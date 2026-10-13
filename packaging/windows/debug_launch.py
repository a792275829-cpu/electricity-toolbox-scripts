"""Run the existing Tk GUI with a heartbeat-based hang stack recorder."""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


def launch() -> int:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "packaging/windows"))
    from portable_bootstrap import configure
    configure(root)
    os.environ["TOOLBOX_CODE_ROOT"] = str(root)
    os.environ["TOOLBOX_DATA_ROOT"] = str(root / "development/data")
    destination = root / "logs/maintenance"
    destination.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    heartbeat = [time.monotonic()]
    log_path = destination / ("tk-hang-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".log")
    with log_path.open("w", encoding="utf-8") as log:
        faulthandler.enable(file=log, all_threads=True)
        def monitor():
            while not stopped.wait(5):
                if time.monotonic() - heartbeat[0] >= 10:
                    log.write(f"\nTk unresponsive at {datetime.now().isoformat()}\n")
                    log.flush()
                    faulthandler.dump_traceback(file=log, all_threads=True)
        watcher = threading.Thread(target=monitor, name="tk-hang-monitor", daemon=True)
        watcher.start()
        try:
            from toolbox.app import ToolboxApp
            app = ToolboxApp(workspace=root)
            def pulse():
                heartbeat[0] = time.monotonic()
                app.after(250, pulse)
            pulse()
            print("调试窗口启动；无响应超过 10 秒时线程栈写入：", log_path, flush=True)
            app.mainloop()
        finally:
            stopped.set()
            watcher.join(6)
            faulthandler.disable()
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
