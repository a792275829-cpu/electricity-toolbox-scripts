from __future__ import annotations

import subprocess
import threading
import os
import signal
import codecs
import io
import select
import time
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
        process = subprocess.Popen(
            list(command), cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", env=dict(env) if env is not None else None,
            start_new_session=True,
            close_fds=True, bufsize=1,
        )
        # start_new_session makes this PID the process-group ID. Remember it
        # while the leader exists, so cancellation can also reap orphaned children.
        process._toolbox_process_group = process.pid
        with self._lock:
            self._active_process = process

        stop_reader = threading.Event()

        def read_stdout() -> None:
            assert process.stdout is not None
            descriptor = process.stdout.fileno()
            os.set_blocking(descriptor, False)
            decoder = io.IncrementalNewlineDecoder(
                codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True,
            )
            pending = ""
            try:
                while not stop_reader.is_set():
                    if not select.select([descriptor], [], [], 0.05)[0]:
                        continue
                    try:
                        chunk = os.read(descriptor, 64 * 1024)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    pending += decoder.decode(chunk)
                    parts = pending.split("\n")
                    pending = parts.pop()
                    for line in parts:
                        lines.put(line + "\n")
            finally:
                pending += decoder.decode(b"", final=True)
                if pending:
                    lines.put(pending)
                lines.put(None)

        reader = threading.Thread(target=read_stdout, name=f"toolbox-output-{process.pid}", daemon=True)
        reader.start()
        stream_closed = False
        try:
            while process.poll() is None or not stream_closed:
                if token.cancelled:
                    self.terminate(process)
                    raise TaskCancelled("任务已取消")
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
            stop_reader.set()
            reader.join(timeout=1)
            if process.stdout is not None:
                process.stdout.close()
            with self._lock:
                self._active_process = None

    def terminate(self, process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + self.terminate_timeout
        try:
            self._signal_owned_process(process, signal.SIGTERM)
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            pass
        group = getattr(process, "_toolbox_process_group", None)
        if group is not None:
            while time.monotonic() < deadline:
                try:
                    os.killpg(group, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
        try:
            # The leader may already be gone while a child ignored SIGTERM.
            self._signal_owned_process(process, signal.SIGKILL)
            process.wait(timeout=self.terminate_timeout)
        except OSError:
            pass

    @staticmethod
    def _signal_owned_process(process: subprocess.Popen[str], sig: signal.Signals) -> None:
        """Signal only the session created for this child, never the caller's terminal group."""
        owned_group = getattr(process, "_toolbox_process_group", None)
        if owned_group is not None:
            try:
                os.killpg(owned_group, sig)
            except ProcessLookupError:
                pass
            return
        try:
            process_group = os.getpgid(process.pid)
        except ProcessLookupError:
            return
        if process_group == process.pid:
            os.killpg(process_group, sig)
        else:
            process.send_signal(sig)
