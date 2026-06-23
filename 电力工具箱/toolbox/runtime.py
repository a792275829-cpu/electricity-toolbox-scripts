from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


PREFERRED_PYTHON = Path(
    r"C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe"
)
PREFERRED_PYTHONW = Path(
    r"C:\Users\lllg\AppData\Local\Programs\Python\Python311\pythonw.exe"
)


@dataclass(frozen=True)
class ToolPaths:
    workspace: Path

    def __init__(self, workspace: Path) -> None:
        object.__setattr__(self, "workspace", Path(workspace).resolve())

    @property
    def online_energy(self) -> Path:
        return self.workspace / "上网电量抓取" / "export_online_energy.py"

    @property
    def online_energy_dir(self) -> Path:
        return self.online_energy.parent

    @property
    def trade_analysis(self) -> Path:
        return (
            self.workspace
            / "电力工具脚本"
            / "电力交易分析工具"
            / "generate_electricity_report.py"
        )

    @property
    def trade_analysis_dir(self) -> Path:
        return self.trade_analysis.parent

    @property
    def summary(self) -> Path:
        return (
            self.workspace
            / "电力工具脚本"
            / "电量汇总工具"
            / "summarize_511_excel.py"
        )

    @property
    def summary_dir(self) -> Path:
        return self.summary.parent

    @property
    def report_gui(self) -> Path:
        return (
            self.workspace
            / "每日生产经营情况汇报自动生成工具"
            / "scripts"
            / "report_gui.py"
        )

    @property
    def report_scripts_dir(self) -> Path:
        return self.report_gui.parent

    @property
    def report_root(self) -> Path:
        return self.report_scripts_dir.parent

    @property
    def private_uploader(self) -> Path:
        return (
            self.workspace
            / "电力工具脚本"
            / "private-data-uploader-tool"
            / "scripts"
            / "upload-private-data.mjs"
        )

    @property
    def private_uploader_dir(self) -> Path:
        return self.private_uploader.parent.parent

    @property
    def group_upload(self) -> Path:
        return self.workspace / "集团每日上传" / "upload_daily_report.py"

    @property
    def group_upload_dir(self) -> Path:
        return self.group_upload.parent

    @property
    def wps_writer(self) -> Path:
        return self.workspace / "wps自动" / "wps_excel_to_kdocs_gui.py"

    @property
    def wps_writer_dir(self) -> Path:
        return self.wps_writer.parent

    @property
    def wps_writer_logs_dir(self) -> Path:
        return self.wps_writer_dir / "logs"


def load_module(name: str, path: Path) -> ModuleType:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到模块文件：{path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def utf8_environment() -> dict[str, str]:
    configure_portable_environment()
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def portable_runtime_root() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime"


def configure_portable_environment() -> None:
    bundled_browsers = portable_runtime_root() / "ms-playwright"
    if bundled_browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled_browsers))


def python_executable() -> str:
    bundled = portable_runtime_root() / "python311" / "python.exe"
    if bundled.is_file():
        return str(bundled)
    if PREFERRED_PYTHON.is_file():
        return str(PREFERRED_PYTHON)
    return sys.executable


def pythonw_executable() -> str:
    bundled = portable_runtime_root() / "python311" / "pythonw.exe"
    if bundled.is_file():
        return str(bundled)
    if PREFERRED_PYTHONW.is_file():
        return str(PREFERRED_PYTHONW)
    if sys.executable.lower().endswith("pythonw.exe"):
        return sys.executable
    candidate = Path(sys.executable).with_name("pythonw.exe")
    if candidate.is_file():
        return str(candidate)
    return sys.executable


def node_executable() -> str:
    bundled = portable_runtime_root() / "node" / "node.exe"
    if bundled.is_file():
        return str(bundled)
    return "node"


configure_portable_environment()


class TaskRegistry:
    def __init__(self) -> None:
        self._processes: set[subprocess.Popen[str]] = set()
        self._threads: set[threading.Thread] = set()
        self._lock = threading.Lock()

    def register_process(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes.add(process)

    def unregister_process(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes.discard(process)

    def register_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._threads.add(thread)

    def unregister_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._threads.discard(thread)

    def has_running_tasks(self) -> bool:
        with self._lock:
            self._processes = {
                process for process in self._processes if process.poll() is None
            }
            self._threads = {thread for thread in self._threads if thread.is_alive()}
            return bool(self._processes or self._threads)

    def terminate_all(self) -> None:
        with self._lock:
            processes = list(self._processes)
            self._processes.clear()
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            except OSError:
                pass
