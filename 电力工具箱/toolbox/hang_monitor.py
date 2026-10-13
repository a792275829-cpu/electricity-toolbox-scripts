"""Record Python stacks when Tk stops servicing its heartbeat."""
from __future__ import annotations

import faulthandler
import threading
import time
from datetime import datetime
from pathlib import Path


class TkHangMonitor:
    def __init__(self, directory: Path, *, timeout: float = 10.0, interval: float = 5.0):
        self.directory = directory
        self.timeout = timeout
        self.interval = interval
        self._stopped = threading.Event()
        self._last_pulse = time.monotonic()
        self._last_dump = float("-inf")
        self._after_id = None
        self._thread = threading.Thread(target=self._watch, name="tk-hang-monitor", daemon=True)

    def start(self, root) -> None:
        # Only this callback touches Tk; the watchdog owns no widgets.
        def pulse():
            self._last_pulse = time.monotonic()
            if not self._stopped.is_set():
                self._after_id = root.after(250, pulse)
        pulse()
        self._thread.start()

    def stop(self, root) -> None:
        self._stopped.set()
        if self._after_id is not None:
            root.after_cancel(self._after_id)
            self._after_id = None

    def _watch(self) -> None:
        while not self._stopped.wait(self.interval):
            now = time.monotonic()
            if now - self._last_pulse < self.timeout or now - self._last_dump < 30:
                continue
            self._last_dump = now
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                filename = "tk-hang-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".log"
                with (self.directory / filename).open("w", encoding="utf-8") as log:
                    log.write(f"Tk unresponsive at {datetime.now().isoformat()}\n")
                    log.flush()
                    faulthandler.dump_traceback(file=log, all_threads=True)
            except OSError:
                # Diagnostics must never interfere with business tasks.
                pass
