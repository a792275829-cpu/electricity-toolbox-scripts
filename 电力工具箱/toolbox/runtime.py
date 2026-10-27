from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from toolbox_storage import data_path, online_energy_output_dir, wind_forecast_output_dir
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .tasks import ProcessRunner, TaskEngine

_MODULE_CACHE: dict[tuple[str, Path], tuple[int, ModuleType]] = {}
_MODULE_CACHE_LOCK = threading.RLock()
_MISSING_MODULE = object()


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
    def online_energy_output_dir(self) -> Path:
        from toolbox_credentials import read_json

        config = read_json(data_path(self.online_energy_dir / "config.json"))
        return online_energy_output_dir(config.get("outputDir"))

    @property
    def wind_forecast(self) -> Path:
        return self.workspace / "风力预测截图" / "wind_forecast_screenshot.py"

    @property
    def wind_forecast_dir(self) -> Path:
        return self.wind_forecast.parent

    @property
    def wind_forecast_output_dir(self) -> Path:
        from toolbox_credentials import read_json

        config = read_json(data_path(self.wind_forecast_dir / "config.json"))
        return wind_forecast_output_dir(config.get("outputDir"), script_dir=self.wind_forecast_dir)

    @property
    def spot_price_update(self) -> Path:
        return self.workspace / "现货价格更新" / "update_spot_prices.py"

    @property
    def spot_price_update_dir(self) -> Path:
        return self.spot_price_update.parent

    @property
    def transaction_info_update(self) -> Path:
        return self.workspace / "交易系统信息更新" / "update_transaction_info.py"

    @property
    def transaction_info_update_dir(self) -> Path:
        return self.transaction_info_update.parent

    @property
    def power_benefit_update(self) -> Path:
        return self.workspace / "功率调整收益统计表更新" / "update_power_benefit.py"

    @property
    def power_benefit_update_dir(self) -> Path:
        return self.power_benefit_update.parent

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
    def section_analysis_dir(self) -> Path:
        return self.workspace / "500kV断面分析"

    @property
    def section_analysis_runner(self) -> Path:
        return self.section_analysis_dir / "run_500kv_analysis.py"

    @property
    def section_analysis_topology(self) -> Path:
        return self.section_analysis_dir / "500kV节点拓扑.md"

    @property
    def daily_pdf_parser_dir(self) -> Path:
        return self.workspace / "广东运行日报PDF解析"

    @property
    def daily_pdf_parser(self) -> Path:
        return self.daily_pdf_parser_dir / "run_daily_pdf_parser.py"

    @property
    def market_report_dir(self) -> Path:
        return self.workspace / "南方分公司电力市场日报"

    @property
    def market_report_runner(self) -> Path:
        return self.market_report_dir / "run_market_report.py"

    @property
    def market_report_output_dir(self) -> Path:
        return data_path(self.market_report_dir / "输出")

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
        return data_path(self.wps_writer_dir / "logs")

    @property
    def market_table_update(self) -> Path:
        return self.workspace / "市场表更新" / "update_market_table.py"

    @property
    def market_table_update_dir(self) -> Path:
        return self.market_table_update.parent

    @property
    def guangdong_price_dir(self) -> Path:
        return self.workspace / "广东电价预测"

    @property
    def guangdong_data_update(self) -> Path:
        return self.guangdong_price_dir / "update_gdfire_data.py"

    @property
    def guangdong_price_forecast(self) -> Path:
        return self.guangdong_price_dir / "train_d1_forecast.py"

    @property
    def guangdong_price_output_dir(self) -> Path:
        return data_path(self.guangdong_price_dir / "输出")

    @property
    def guangdong_price_paths(self) -> dict[str, Path]:
        module = load_module(
            "toolbox_guangdong_paths",
            Path(__file__).resolve().parents[2] / "广东电价预测" / "gd_price_forecast" / "paths.py",
        )
        return module.resolve_paths(self.guangdong_price_dir)

    @property
    def guangdong_price_master_workbook(self) -> Path:
        return self.guangdong_price_paths["masterWorkbook"]

    @property
    def guangdong_prediction_output_dir(self) -> Path:
        return self.guangdong_price_paths["predictionOutputDir"]

    @property
    def file_dialog_settings(self) -> Path:
        return data_path(self.workspace / "电力工具箱" / "file_dialog_settings.json")


def _module_is_local(module: object, roots: tuple[Path, ...]) -> bool:
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return False
    try:
        resolved = Path(module_file).resolve()
    except (OSError, TypeError):
        return False
    interpreter_roots = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    if any(
        resolved == interpreter_root or interpreter_root in resolved.parents
        for interpreter_root in interpreter_roots
    ):
        # The project virtual environment lives below the workspace root. Its
        # packages must remain registered or class identities split on re-import.
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def _restore_local_imports(
    before: dict[str, ModuleType],
    *,
    keep_name: str,
    roots: tuple[Path, ...],
) -> None:
    for module_name in set(before).union(sys.modules):
        if module_name == keep_name:
            continue
        previous = before.get(module_name, _MISSING_MODULE)
        current = sys.modules.get(module_name, _MISSING_MODULE)
        if previous is current:
            continue
        shared_file = Path(__file__).resolve().parents[2] / f"{module_name}.py"
        if (previous is _MISSING_MODULE and module_name.startswith("toolbox_")
                and getattr(current, "__file__", None)
                and Path(current.__file__).resolve() == shared_file):
            # Ordinary workspace libraries keep one module/class/lock identity.
            # Standalone tools and their local helper names remain isolated.
            continue
        if not (
            _module_is_local(previous, roots)
            or _module_is_local(current, roots)
        ):
            continue
        if previous is _MISSING_MODULE:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def load_module(name: str, path: Path) -> ModuleType:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到模块文件：{path}")
    modified = path.stat().st_mtime_ns
    with _MODULE_CACHE_LOCK:
        cached = _MODULE_CACHE.get((name, path))
        if cached is not None and cached[0] == modified:
            return cached[1]
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载模块：{path}")
        module = importlib.util.module_from_spec(spec)
        modules_before = dict(sys.modules)
        sys_path_before = list(sys.path)
        local_roots = (path.parent, Path(__file__).resolve().parents[2])
        sys.modules[name] = module
        try:
            sys.path.insert(0, str(path.parent))
            source = path.read_bytes()
            exec(compile(source, str(path), "exec"), module.__dict__)
        except Exception:
            previous = modules_before.get(name, _MISSING_MODULE)
            if previous is _MISSING_MODULE:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
            raise
        finally:
            sys.path[:] = sys_path_before
            _restore_local_imports(
                modules_before,
                keep_name=name,
                roots=local_roots,
            )
        # A compatibility shim may hand the import over to the package module.
        module = sys.modules.get(name, module)
        _MODULE_CACHE[(name, path)] = (modified, module)
        return module


def clear_module_cache() -> None:
    with _MODULE_CACHE_LOCK:
        for name, _path in tuple(_MODULE_CACHE):
            sys.modules.pop(name, None)
        _MODULE_CACHE.clear()


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
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)


def python_executable() -> str:
    configured = os.environ.get("TOOLBOX_PYTHON")
    if configured and Path(configured).is_file():
        return configured
    return sys.executable


def pythonw_executable() -> str:
    # Retain the existing helper API for GUI callers on macOS.
    return python_executable()


def node_executable() -> str:
    configured = os.environ.get("TOOLBOX_NODE")
    if configured and Path(configured).is_file():
        return configured
    bundled = portable_runtime_root() / "node/bin/node"
    if bundled.is_file():
        return str(bundled)

    discovered = shutil.which("node")
    if discovered:
        return discovered

    if sys.platform == "darwin":
        home = Path.home()
        candidates = [
            home / ".local" / "bin" / "node",
            home / ".volta" / "bin" / "node",
            Path("/opt/homebrew/bin/node"),
            Path("/usr/local/bin/node"),
        ]
        candidates.extend(
            sorted(
                (home / ".nvm" / "versions" / "node").glob("*/bin/node"),
                reverse=True,
            )
        )
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return "node"


configure_portable_environment()


class TaskRegistry(TaskEngine):
    def __init__(self, *, event_capacity: int = 0) -> None:
        super().__init__(event_capacity=event_capacity)
        self._legacy_processes: set[subprocess.Popen[str]] = set()
        self._legacy_threads: set[threading.Thread] = set()
        self._legacy_lock = threading.Lock()

    def register_process(self, process: subprocess.Popen[str]) -> None:
        with self._legacy_lock:
            self._legacy_processes.add(process)

    def unregister_process(self, process: subprocess.Popen[str]) -> None:
        with self._legacy_lock:
            self._legacy_processes.discard(process)

    def register_thread(self, thread: threading.Thread) -> None:
        with self._legacy_lock:
            self._legacy_threads.add(thread)

    def unregister_thread(self, thread: threading.Thread) -> None:
        with self._legacy_lock:
            self._legacy_threads.discard(thread)

    def has_running_tasks(self) -> bool:
        with self._legacy_lock:
            self._legacy_processes = {
                process for process in self._legacy_processes if process.poll() is None
            }
            self._legacy_threads = {
                thread for thread in self._legacy_threads if thread.is_alive()
            }
            legacy = bool(self._legacy_processes or self._legacy_threads)
        return legacy or super().has_running_tasks()

    def terminate_all(self):
        remaining = self.shutdown(timeout=5.0)
        with self._legacy_lock:
            processes = list(self._legacy_processes)
            self._legacy_processes.clear()
        for process in processes:
            if process.poll() is not None:
                continue
            ProcessRunner().terminate(process)
        with self._legacy_lock:
            active_threads = tuple(
                thread for thread in self._legacy_threads if thread.is_alive()
            )
            self._legacy_threads = set(active_threads)
        return (*remaining, *active_threads)
