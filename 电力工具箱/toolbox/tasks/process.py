from __future__ import annotations

import subprocess
import threading
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Mapping, Sequence

from .models import CancellationToken, TaskCancelled


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int


class ProcessExecutionError(RuntimeError):
    def __init__(self, returncode: int) -> None:
        super().__init__(f"命令退出码 {returncode}")
        self.returncode = returncode


class ProcessRunner:
    def __init__(self, *, terminate_timeout: float = 2.0) -> None:
        self.terminate_timeout = terminate_timeout
        self._active_process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    @property
    def active_process(self) -> subprocess.Popen[str] | None:
        with self._lock:
            return self._active_process

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None,
        token: CancellationToken,
        on_output: Callable[[str], None],
    ) -> ProcessResult:
        lines: Queue[str | None] = Queue()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt":
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            list(command), cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", env=dict(env) if env is not None else None,
            creationflags=creationflags, start_new_session=os.name != "nt",
            close_fds=True, bufsize=1,
        )
        with self._lock:
            self._active_process = process

        def read_stdout() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        stream_closed = False
        try:
            while process.poll() is None or not stream_closed:
                if token.cancelled and process.poll() is None:
                    self.terminate(process)
                try:
                    line = lines.get(timeout=0.05)
                except Empty:
                    continue
                if line is None:
                    stream_closed = True
                else:
                    on_output(line)
            returncode = process.wait()
            if token.cancelled:
                raise TaskCancelled("任务已取消")
            if returncode != 0:
                raise ProcessExecutionError(returncode)
            return ProcessResult(returncode)
        finally:
            if process.poll() is None:
                self.terminate(process)
            if process.stdout is not None:
                process.stdout.close()
            reader.join(timeout=1)
            with self._lock:
                self._active_process = None

    def terminate(self, process: subprocess.Popen[str]) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                self._signal_owned_process(process, signal.SIGTERM)
            process.wait(timeout=self.terminate_timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                self._signal_owned_process(process, signal.SIGKILL)
            process.wait(timeout=self.terminate_timeout)
        except OSError:
            pass

    @staticmethod
    def _signal_owned_process(process: subprocess.Popen[str], sig: signal.Signals) -> None:
        """Signal only the session created for this child, never the caller's terminal group."""
        try:
            process_group = os.getpgid(process.pid)
        except ProcessLookupError:
            return
        if process_group == process.pid:
            os.killpg(process_group, sig)
        else:
            process.send_signal(sig)
