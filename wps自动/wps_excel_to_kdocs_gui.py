from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import traceback
import time as time_module
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter, range_boundaries
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
except ImportError:  # pragma: no cover - surfaced in GUI
    load_workbook = None
    get_column_letter = None
    range_boundaries = None
    coordinate_from_string = None
    column_index_from_string = None

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - surfaced in GUI
    PlaywrightTimeoutError = Exception
    sync_playwright = None


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(BASE_DIR.parent))
from toolbox_storage import data_path
from toolbox_log_console import LogConsole, explain_error, redact
from toolbox_choices import SegmentedGroup

DATA_DIR = data_path(BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "wps_excel_to_kdocs_config.json"
DEFAULT_CONFIG_PATH = BASE_DIR / "wps_excel_to_kdocs_config.defaults.json"
PROFILE_DIR = DATA_DIR / "wps-browser-profile"
WPS_CHROME_PID_PATH = DATA_DIR / "wps-chrome.pid"
WPS_CHROME_STATE_PATH = DATA_DIR / "wps-chrome-state.json"
WPS_CHROME_LAUNCH_VERSION = 6
WPS_CHROME_START_LOCK = threading.Lock()
LOG_DIR = DATA_DIR / "logs"
MAX_RECENT_URLS = 10
DOCUMENT_CONFIGS_MIN_HEIGHT = 220
CDP_CONNECT_TIMEOUT_MS = 15000
WPS_LOGIN_STATUS_URL = "https://account.kdocs.cn/api/v3/islogin"
LOGIN_STATUS_POLL_MS = 15000
CONFIG_EXPORT_SCHEMA = "wps_excel_to_kdocs_config_export"
CONFIG_EXPORT_VERSION = 1


def center_window(window: tk.Toplevel | tk.Tk, width: int | None = None, height: int | None = None) -> None:
    window.update_idletasks()
    actual_width = width or window.winfo_width() or window.winfo_reqwidth()
    actual_height = height or window.winfo_height() or window.winfo_reqheight()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    actual_width = min(actual_width, max(640, screen_width - 80))
    actual_height = min(actual_height, max(480, screen_height - 80))
    x = max(0, (screen_width - actual_width) // 2)
    y = max(0, (screen_height - actual_height) // 2)
    window.geometry(f"{actual_width}x{actual_height}+{x}+{y}")


@dataclass
class MappingRow:
    kdocs_url: str
    local_file: str
    source_sheet: str
    source_start: str
    source_end: str
    target_sheet: str
    target_start: str
    target_end: str
    source_type: str = "excel"
    source_url: str = ""


@dataclass
class RegionRow:
    source_sheet: str
    source_start: str
    source_end: str
    target_sheet: str
    target_start: str
    target_end: str


@dataclass
class DocumentConfig:
    name: str
    kdocs_url: str
    local_file: str
    regions: list[RegionRow]
    source_type: str = "excel"
    source_url: str = ""


@dataclass
class PreparedMapping:
    index: int
    kdocs_url: str
    workbook_path: str
    source_sheet: str
    source_range: str
    target_sheet: str
    target_start: str
    target_range: str
    rows: int
    cols: int
    values: list[list[Any]]


@dataclass(frozen=True)
class WpsLoginStatus:
    state: str
    message: str


def read_wps_login_status(context) -> WpsLoginStatus:
    """Confirm the current browser session with WPS, without exposing account data."""
    response = context.request.get(WPS_LOGIN_STATUS_URL, timeout=10000)
    data = response.json()
    if not isinstance(data, dict):
        return WpsLoginStatus("unknown", "暂时无法确认，请稍后刷新登录状态。")
    if response.status == 200 and data.get("result") == "ok" and data.get("userid"):
        return WpsLoginStatus("logged_in", "已登录")
    if data.get("result") == "userNotLogin" or response.status == 401:
        return WpsLoginStatus("logged_out", "未登录或登录已失效，请点击“登录 WPS”。")
    return WpsLoginStatus("unknown", "暂时无法确认，请检查网络后刷新登录状态。")


def check_wps_login_status(browser_mode: str, cdp_url: str, log) -> WpsLoginStatus:
    # A copied browser profile is not proof of login: cookies may be encrypted
    # for the previous Mac. Existing profiles must be verified online.
    if browser_mode != "cdp" and not PROFILE_DIR.is_dir():
        return WpsLoginStatus("logged_out", "本机尚未登录，请点击“登录 WPS”。")
    if sync_playwright is None:
        raise RuntimeError("缺少浏览器自动化组件，请先运行工具箱初始化程序。")
    with sync_playwright() as playwright:
        _browser, context, close_context = open_browser_context(
            playwright, browser_mode, cdp_url, log, for_login_check=True
        )
        try:
            return read_wps_login_status(context)
        finally:
            if close_context:
                context.close()


def normalize_cell_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    return value


def normalize_for_compare(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text.lower() in {"none", "null"}:
        return ""
    return text


def values_equivalent(expected: Any, actual: Any) -> bool:
    expected_value = normalize_for_compare(expected)
    actual_value = normalize_for_compare(actual)
    if expected_value == actual_value:
        return True
    if isinstance(expected_value, bool) or isinstance(actual_value, bool):
        return False
    try:
        expected_number = float(expected_value)
        actual_number = float(actual_value)
        return abs(expected_number - actual_number) <= 1e-9
    except (TypeError, ValueError):
        return False


def parse_start_cell(cell: str) -> tuple[int, int]:
    if coordinate_from_string is None or column_index_from_string is None:
        raise RuntimeError("缺少表格读取组件，请运行工具箱目录中的 setup_macos.command 完成初始化。")
    col_text, row = coordinate_from_string(cell.strip().upper())
    return row, column_index_from_string(col_text)


def normalize_cell_address(cell: str) -> str:
    row, col = parse_start_cell(cell)
    return f"{get_column_letter(col)}{row}"


def range_from_cells(start_cell: str, end_cell: str) -> str:
    start_row, start_col = parse_start_cell(start_cell)
    end_row, end_col = parse_start_cell(end_cell)
    if end_row < start_row or end_col < start_col:
        raise ValueError(f"结束单元格不能位于起始单元格的上方或左侧：{start_cell} → {end_cell}")
    return f"{get_column_letter(start_col)}{start_row}:{get_column_letter(end_col)}{end_row}"


def range_shape(start_cell: str, end_cell: str) -> tuple[int, int]:
    start_row, start_col = parse_start_cell(start_cell)
    end_row, end_col = parse_start_cell(end_cell)
    if end_row < start_row or end_col < start_col:
        raise ValueError(f"结束单元格不能位于起始单元格的上方或左侧：{start_cell} → {end_cell}")
    return end_row - start_row + 1, end_col - start_col + 1


def split_range(range_text: str) -> tuple[str, str]:
    text = range_text.strip()
    if ":" not in text:
        cell = normalize_cell_address(text)
        return cell, cell
    start, end = text.split(":", 1)
    return normalize_cell_address(start), normalize_cell_address(end)


def target_range_from_start(start_cell: str, rows: int, cols: int) -> str:
    start_row, start_col = parse_start_cell(start_cell)
    end_row = start_row + rows - 1
    end_col = start_col + cols - 1
    return f"{get_column_letter(start_col)}{start_row}:{get_column_letter(end_col)}{end_row}"


def target_rect(start_cell: str, rows: int, cols: int) -> tuple[int, int, int, int]:
    start_row, start_col = parse_start_cell(start_cell)
    return start_row, start_col, start_row + rows - 1, start_col + cols - 1


def rect_from_cells(start_cell: str, end_cell: str) -> tuple[int, int, int, int]:
    start_row, start_col = parse_start_cell(start_cell)
    end_row, end_col = parse_start_cell(end_cell)
    if end_row < start_row or end_col < start_col:
        raise ValueError(f"结束单元格不能位于起始单元格的上方或左侧：{start_cell} → {end_cell}")
    return start_row, start_col, end_row, end_col


def rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    a_r1, a_c1, a_r2, a_c2 = a
    b_r1, b_c1, b_r2, b_c2 = b
    return not (a_r2 < b_r1 or b_r2 < a_r1 or a_c2 < b_c1 or b_c2 < a_c1)


def load_config() -> dict[str, Any]:
    path = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _region_config_from_dict(row: dict[str, Any]) -> dict[str, str]:
    return {
        "source_sheet": str(row.get("source_sheet", "")),
        "source_start": str(row.get("source_start", "")),
        "source_end": str(row.get("source_end", "")),
        "target_sheet": str(row.get("target_sheet", "")),
        "target_start": str(row.get("target_start", "")),
        "target_end": str(row.get("target_end", "")),
    }


def _document_config_from_dict(row: dict[str, Any]) -> dict[str, Any]:
    regions = [
        _region_config_from_dict(region)
        for region in row.get("regions", [])
        if isinstance(region, dict)
    ]
    name = str(row.get("name", "")).strip() or Path(str(row.get("local_file", ""))).stem or "未命名配置"
    source_type = str(row.get("source_type", "excel")).strip() or "excel"
    if source_type not in {"excel", "kdocs"}:
        source_type = "excel"
    return {
        "name": name,
        "source_type": source_type,
        "source_url": str(row.get("source_url", "")),
        "kdocs_url": str(row.get("kdocs_url", "")),
        "local_file": str(row.get("local_file", "")),
        "regions": regions,
    }


def normalize_runtime_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "recent_urls": _string_list(data.get("recent_urls", [])),
        "browser_mode": str(data.get("browser_mode", "persistent") or "persistent"),
        "cdp_url": str(data.get("cdp_url", "http://127.0.0.1:9222") or "http://127.0.0.1:9222"),
        "configs": [
            _document_config_from_dict(row)
            for row in data.get("configs", [])
            if isinstance(row, dict)
        ],
    }


def build_config_export(runtime_config: dict[str, Any], exported_at: str | None = None) -> dict[str, Any]:
    export_time = exported_at or datetime.now().replace(microsecond=0).isoformat()
    return {
        "schema": CONFIG_EXPORT_SCHEMA,
        "version": CONFIG_EXPORT_VERSION,
        "exported_at": export_time,
        **normalize_runtime_config(runtime_config),
    }


def runtime_config_from_export_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != CONFIG_EXPORT_SCHEMA:
        raise ValueError("无法识别此配置文件，请选择由本工具导出的配置。")
    if payload.get("version") != CONFIG_EXPORT_VERSION:
        raise ValueError(f"暂不支持此配置文件版本：{payload.get('version')!r}")
    runtime_config = normalize_runtime_config(payload)
    if not runtime_config["configs"]:
        raise ValueError("配置文件中没有可导入的写入配置。")
    return runtime_config


def load_config_export(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件格式有误，无法读取：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("配置文件结构有误，请重新导出后再导入。")
    return runtime_config_from_export_payload(payload)


def save_config_export(path: Path, runtime_config: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(build_config_export(runtime_config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_recent_urls(urls: list[str], url: str) -> list[str]:
    normalized = url.strip()
    if not normalized:
        return urls[:MAX_RECENT_URLS]
    result = [normalized]
    for item in urls:
        item = item.strip()
        if item and item != normalized and item not in result:
            result.append(item)
        if len(result) >= MAX_RECENT_URLS:
            break
    return result


def load_excel_workbook(workbook_path: Path, **kwargs):
    """Open an OOXML workbook with a useful error for partial/corrupt files."""
    if workbook_path.suffix.lower() in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        try:
            is_zip = zipfile.is_zipfile(workbook_path)
        except OSError as exc:
            raise OSError(f"无法读取表格文件：{workbook_path}") from exc
        if not is_zip:
            try:
                with workbook_path.open("rb") as source:
                    starts_as_zip = source.read(4).startswith(b"PK")
            except OSError:
                starts_as_zip = False
            if starts_as_zip:
                reason = (
                    "表格文件尚未生成完整。"
                    "请重新生成汇总表后再选择。"
                )
            else:
                reason = (
                    "所选文件的内容与表格格式不符。"
                    "请用表格软件打开，另存为 .xlsx 文件后重试。"
                )
            raise ValueError(f"{reason}\n\n文件：{workbook_path}")
    try:
        return load_workbook(workbook_path, **kwargs)
    except zipfile.BadZipFile as exc:
        raise ValueError(
            "表格文件不完整或已损坏，请重新生成后再试。"
            f"\n\n文件：{workbook_path}"
        ) from exc


def read_excel_range(workbook_path: Path, sheet_name: str, source_range: str) -> tuple[list[list[Any]], int, int]:
    return read_excel_ranges(workbook_path, [(sheet_name, source_range)])[0]


def read_excel_ranges(
    workbook_path: Path, requests: list[tuple[str, str]]
) -> list[tuple[list[list[Any]], int, int]]:
    """Read several regions of a workbook with one scan per source sheet."""
    if load_workbook is None or range_boundaries is None:
        raise RuntimeError("缺少表格读取组件，请运行工具箱目录中的 setup_macos.command 完成初始化。")
    if not workbook_path.exists():
        raise FileNotFoundError(f"找不到表格文件：{workbook_path}")
    if not requests:
        return []

    wb = load_excel_workbook(workbook_path, data_only=True, read_only=True)
    try:
        grouped: dict[str, list[tuple[int, tuple[int, int, int, int]]]] = {}
        for index, (sheet_name, source_range) in enumerate(requests):
            if sheet_name not in wb.sheetnames:
                raise ValueError(f"找不到来源工作表：{sheet_name}")
            grouped.setdefault(sheet_name, []).append((index, range_boundaries(source_range)))

        results: list[tuple[list[list[Any]], int, int] | None] = [None] * len(requests)
        for sheet_name, regions in grouped.items():
            first_row = min(bounds[1] for _, bounds in regions)
            last_row = max(bounds[3] for _, bounds in regions)
            first_col = min(bounds[0] for _, bounds in regions)
            last_col = max(bounds[2] for _, bounds in regions)
            collected: dict[int, list[list[Any]]] = {index: [] for index, _ in regions}
            for row_number, row in enumerate(
                wb[sheet_name].iter_rows(
                    min_row=first_row,
                    max_row=last_row,
                    min_col=first_col,
                    max_col=last_col,
                    values_only=True,
                ),
                start=first_row,
            ):
                for index, (min_col, min_row, max_col, max_row) in regions:
                    if min_row <= row_number <= max_row:
                        cells = row[min_col - first_col : max_col - first_col + 1]
                        collected[index].append([normalize_cell_value(cell) for cell in cells])
            for index, (min_col, min_row, max_col, max_row) in regions:
                results[index] = (
                    collected[index],
                    max_row - min_row + 1,
                    max_col - min_col + 1,
                )
        if any(result is None for result in results):
            raise RuntimeError("未能完整读取来源区域，请检查表格文件后重试。")
        return [result for result in results if result is not None]
    finally:
        wb.close()


def read_excel_sheet_names(workbook_path: Path) -> list[str]:
    if load_workbook is None:
        raise RuntimeError("缺少表格读取组件，请运行工具箱目录中的 setup_macos.command 完成初始化。")
    if not workbook_path.exists():
        raise FileNotFoundError(f"找不到表格文件：{workbook_path}")
    wb = load_excel_workbook(workbook_path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def prepare_mappings(
    mappings: list[MappingRow],
    browser_mode: str = "persistent",
    cdp_url: str = "http://127.0.0.1:9222",
    log=None,
) -> list[PreparedMapping]:
    if not mappings:
        raise ValueError("尚未配置写入区域。")

    source_specs: list[tuple[int, MappingRow, str, Path | None, str, str, int, int]] = []
    excel_groups: dict[Path, list[tuple[int, str, str]]] = {}
    target_rects: dict[tuple[str, str], list[tuple[int, tuple[int, int, int, int], str]]] = {}
    log = log or (lambda _message: None)
    for idx, mapping in enumerate(mappings, start=1):
        source_type = mapping.source_type or "excel"
        workbook_path = None
        if source_type == "excel":
            workbook_path_text = mapping.local_file.strip()
            if not workbook_path_text:
                raise ValueError(f"第 {idx} 个写入区域未选择本地文件。")
            workbook_path = Path(workbook_path_text).expanduser()
        elif source_type == "kdocs":
            if not mapping.source_url.strip():
                raise ValueError(f"第 {idx} 个写入区域未填写来源表格链接。")
        else:
            raise ValueError(f"第 {idx} 个写入区域的数据来源类型无法识别：{source_type}")
        if not all(
            [
                mapping.kdocs_url,
                mapping.source_sheet,
                mapping.source_start,
                mapping.source_end,
                mapping.target_sheet,
                mapping.target_start,
                mapping.target_end,
            ]
        ):
            raise ValueError(f"第 {idx} 个写入区域的信息未填完整。")
        source_range = range_from_cells(mapping.source_start, mapping.source_end)
        target_range = range_from_cells(mapping.target_start, mapping.target_end)
        source_rows, source_cols = range_shape(mapping.source_start, mapping.source_end)
        target_rows, target_cols = range_shape(mapping.target_start, mapping.target_end)
        if (source_rows, source_cols) != (target_rows, target_cols):
            raise ValueError(
                f"第 {idx} 个写入区域大小不一致：来源为 {source_rows} 行 {source_cols} 列，"
                f"目标为 {target_rows} 行 {target_cols} 列。"
            )
        rect = rect_from_cells(mapping.target_start, mapping.target_end)
        target_key = (mapping.kdocs_url, mapping.target_sheet)
        for other_idx, other_rect, other_range in target_rects.get(target_key, []):
            if rects_overlap(rect, other_rect):
                raise ValueError(
                    f"目标表格 {mapping.kdocs_url} 的工作表“{mapping.target_sheet}”存在重叠区域："
                    f"第 {other_idx} 个区域 {other_range} 与第 {idx} 个区域 {target_range}。"
                )
        target_rects.setdefault(target_key, []).append((idx, rect, target_range))
        source_specs.append(
            (idx, mapping, source_type, workbook_path, source_range, target_range, source_rows, source_cols)
        )
        if workbook_path is not None:
            excel_groups.setdefault(workbook_path, []).append((idx, mapping.source_sheet, source_range))

    excel_values: dict[int, tuple[list[list[Any]], int, int]] = {}
    for workbook_path, requests in excel_groups.items():
        results = read_excel_ranges(
            workbook_path, [(sheet_name, source_range) for _, sheet_name, source_range in requests]
        )
        for (idx, _, _), result in zip(requests, results):
            excel_values[idx] = result

    online_specs = [spec for spec in source_specs if spec[2] != "excel"]
    online_values = {}
    if online_specs:
        results = read_online_ranges(
            [(mapping.source_url, mapping.source_sheet, source_range, rows, cols)
             for _, mapping, _, _, source_range, _, rows, cols in online_specs],
            browser_mode, cdp_url, log,
        )
        online_values = {spec[0]: values for spec, values in zip(online_specs, results)}

    prepared: list[PreparedMapping] = []
    for idx, mapping, source_type, workbook_path, source_range, target_range, source_rows, source_cols in source_specs:
        if source_type == "excel":
            values, rows, cols = excel_values[idx]
            source_label = str(workbook_path)
        else:
            values = online_values[idx]
            rows, cols = source_rows, source_cols
            source_label = mapping.source_url
        prepared.append(
            PreparedMapping(
                index=idx,
                kdocs_url=mapping.kdocs_url,
                workbook_path=source_label,
                source_sheet=mapping.source_sheet,
                source_range=source_range,
                target_sheet=mapping.target_sheet,
                target_start=mapping.target_start,
                target_range=target_range,
                rows=rows,
                cols=cols,
                values=values,
            )
        )
    return prepared


def coerce_wps_matrix(raw: Any, rows: int, cols: int) -> list[list[Any]]:
    if rows == 1 and cols == 1:
        if isinstance(raw, list):
            if raw and isinstance(raw[0], list):
                return [[raw[0][0] if raw[0] else ""]]
            return [[raw[0] if raw else ""]]
        return [[raw]]
    if isinstance(raw, list):
        if rows == 1 and (not raw or not isinstance(raw[0], list)):
            return [raw]
        matrix = []
        for row in raw:
            matrix.append(row if isinstance(row, list) else [row])
        return matrix
    return [[raw]]


def compare_matrix(expected: list[list[Any]], actual_raw: Any, rows: int, cols: int) -> list[str]:
    actual = coerce_wps_matrix(actual_raw, rows, cols)
    mismatches: list[str] = []
    for r in range(rows):
        for c in range(cols):
            raw_expected = expected[r][c] if r < len(expected) and c < len(expected[r]) else ""
            raw_actual = actual[r][c] if r < len(actual) and c < len(actual[r]) else ""
            if not values_equivalent(raw_expected, raw_actual):
                cell = f"区域内第 {r + 1} 行第 {c + 1} 列"
                expected_value = normalize_for_compare(raw_expected)
                actual_value = normalize_for_compare(raw_actual)
                mismatches.append(f"{cell}：来源值 {expected_value!r}，回读值 {actual_value!r}")
                if len(mismatches) >= 20:
                    return mismatches
    return mismatches


def wait_for_wps_ready(page, log, timeout_seconds: int) -> dict[str, Any]:
    deadline = time_module.monotonic() + timeout_seconds
    last_state: dict[str, Any] = {"ready": False, "url": "", "title": ""}
    last_log_second = 0
    readiness_script = """
    async () => {
      const api = window.WPSOpenApi;
      if (typeof api?.EtApplication !== "function") {
        return {
          ready: false,
          url: location.href,
          title: document.title,
          hasWPSOpenApi: !!api
        };
      }

      const bounded = (value, milliseconds = 750) => Promise.race([
        Promise.resolve(value),
        new Promise(resolve => setTimeout(() => resolve(false), milliseconds))
      ]);
      let app;
      let applicationReady = false;
      let documentReady = false;
      try {
        app = api.EtApplication();
        applicationReady = Boolean(await bounded(app.Ready));
      } catch (_error) {}
      if (!applicationReady && api.documentReadyPromise) {
        try {
          documentReady = Boolean(await bounded(
            Promise.resolve(api.documentReadyPromise).then(() => true)
          ));
        } catch (_error) {}
      }
      return {
        ready: !!app && typeof app.Worksheets === "function"
          && (applicationReady || documentReady) && window.WPSOpenApi === api,
        url: location.href,
        title: document.title,
        hasSave: typeof api.save === "function"
      };
    }
    """
    while time_module.monotonic() < deadline:
        try:
            state = page.evaluate(readiness_script)
            last_state = state
            if state.get("ready"):
                return state
        except Exception as exc:
            last_state = {"ready": False, "url": page.url, "title": "", "error": str(exc)}

        remaining = int(deadline - time_module.monotonic())
        elapsed_bucket = timeout_seconds - remaining
        if elapsed_bucket // 15 > last_log_second // 15:
            last_log_second = elapsed_bucket
            log(f"仍在等待表格加载，当前页面：{page.url}")
        page.wait_for_timeout(1000)
    return last_state


WPS_APPLICATION_GUARD = """
  const api = window.WPSOpenApi;
  if (typeof api?.EtApplication !== "function") {
    throw new Error("WPS_API_NOT_READY: 操作前表格连接已变化");
  }
  const app = api.EtApplication();
  if (!app || typeof app.Worksheets !== "function") {
    throw new Error("WPS_API_NOT_READY: 表格尚未就绪");
  }
"""


def evaluate_wps_operation(page, script: str, payload: Any, log, timeout_seconds: int):
    """Recheck readiness after navigation, including the gap before evaluate."""
    first_error: Exception | None = None
    for attempt in range(1, 4):
        ready = wait_for_wps_ready(
            page, log, timeout_seconds=timeout_seconds if attempt == 1 else 60
        )
        if not ready.get("ready"):
            message = (
                "表格加载超时。"
                "请点击“登录 WPS”，确认已登录且能访问该表格后重试。"
                f"当前页面：{ready.get('title')} {ready.get('url')}"
            )
            if first_error is not None:
                message += f" 首次操作失败原因：{first_error}"
            raise RuntimeError(message) from first_error
        try:
            return page.evaluate(script, payload)
        except Exception as exc:
            # The guard runs before any cell assignment. Do not retry arbitrary
            # TypeErrors or API errors that may have occurred during a write.
            if not any(marker in str(exc) for marker in (
                "WPS_API_NOT_READY:", "Execution context was destroyed"
            )) and "navigation" not in str(exc).lower():
                raise
            if first_error is None:
                first_error = exc
            if attempt == 3:
                raise RuntimeError(
                    f"重试 3 次后仍无法操作表格。首次失败原因：{first_error}"
                ) from exc
            log(f"表格连接已变化，等待恢复后重试（第 {attempt + 1}/3 次）。")
            page.wait_for_timeout(1500)


def browser_has_page(browser, url: str) -> bool:
    target_key = url.rstrip("/").split("/")[-1]
    return any(
        target_key and target_key in page.url
        for context in browser.contexts
        for page in context.pages
    )


def ensure_background_cdp_target(browser, cdp_url: str, url: str) -> bool:
    if browser_has_page(browser, url):
        return False
    session = browser.new_browser_cdp_session()
    session.send("Target.createTarget", {"url": url, "background": True})
    target_key = url.rstrip("/").split("/")[-1]
    deadline = time_module.monotonic() + 10
    while time_module.monotonic() < deadline:
        _base_url, targets = cdp_page_targets(cdp_url)
        if any(
            target.get("type") == "page"
            and target_key
            and target_key in str(target.get("url") or "")
            for target in targets
        ):
            return True
        time_module.sleep(0.05)
    raise RuntimeError("已创建后台表格页面，但加载超时，请重试。")


def find_or_open_page(context, url: str):
    target_key = url.rstrip("/").split("/")[-1]
    for page in context.pages:
        if target_key and target_key in page.url:
            return page
    for page in context.pages:
        if "kdocs.cn" in page.url:
            page.goto(url, wait_until="commit", timeout=20000)
            return page
    page = next(
        (
            candidate
            for candidate in context.pages
            if candidate.url in {"", "about:blank", "chrome://newtab/"}
        ),
        None,
    )
    if page is None:
        page = context.pages[0] if context.pages else context.new_page()
    page.goto(url, wait_until="commit", timeout=20000)
    return page


def cdp_candidates(cdp_url: str) -> list[str]:
    raw = cdp_url.strip() or "http://localhost:9222"
    candidates = [raw]
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.port:
        for host in ("localhost", "[::1]", "127.0.0.1"):
            netloc = f"{host}:{parsed.port}"
            candidate = urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def resolve_cdp_endpoint(cdp_url: str) -> str:
    if cdp_url.startswith("ws://") or cdp_url.startswith("wss://"):
        return cdp_url
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    errors: list[str] = []
    for candidate in cdp_candidates(cdp_url):
        version_url = candidate.rstrip("/") + "/json/version"
        try:
            with opener.open(version_url, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            ws_url = data.get("webSocketDebuggerUrl")
            if ws_url:
                return ws_url
            errors.append(f"{version_url}：浏览器未返回连接地址")
        except Exception as exc:
            errors.append(f"{version_url}: {exc}")
    raise RuntimeError("无法获取浏览器连接地址。" + " | ".join(errors))


def find_macos_chrome() -> Path | None:
    # Keep the automation browser on a distinct application bundle. If the
    # regular Chrome binary is used for a long-lived headless CDP process,
    # LaunchServices may route links from WeChat and other apps into that
    # invisible instance because both share com.google.Chrome.
    candidates = [
        Path("/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        Path.home() / "Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)






def managed_cdp_host_port(cdp_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(cdp_url.strip() or "http://127.0.0.1:9222")
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("专用浏览器的连接地址必须指向本机，例如 http://127.0.0.1:9222。")
    return "127.0.0.1", parsed.port or 9222


def build_wps_chrome_command(
    chrome_path: Path,
    cdp_url: str,
    initial_url: str = "https://www.kdocs.cn/",
    *,
    headless: bool = False,
) -> list[str]:
    host, port = managed_cdp_host_port(cdp_url)
    executable = [str(chrome_path)]
    # A macOS application can retain an "Open using Rosetta" preference even
    # when Chrome and Python are both universal/native. KDocs is CPU-heavy and
    # becomes dramatically slower when its renderer is translated. Force the
    # dedicated instance to use Chrome's native Apple Silicon slice.
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        executable = ["/usr/bin/arch", "-arm64", str(chrome_path)]
    command = executable + [
        f"--remote-debugging-address={host}",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        # Do not let Chrome keep a hidden app/background-mode process after
        # the last dedicated WPS window is closed.
        "--disable-background-mode",
        # The Tk config dialog normally covers Chrome on macOS. Keep the WPS
        # renderer at foreground speed while that dedicated window is occluded.
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    if headless:
        command.extend(["--headless=new", "--window-size=1400,900"])
    command.append(initial_url)
    return command


def try_resolve_cdp_endpoint(cdp_url: str, timeout: float = 0.5) -> str | None:
    if cdp_url.startswith("ws://") or cdp_url.startswith("wss://"):
        return cdp_url
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for candidate in cdp_candidates(cdp_url):
        try:
            with opener.open(candidate.rstrip("/") + "/json/version", timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            websocket_url = data.get("webSocketDebuggerUrl")
            if websocket_url:
                return str(websocket_url)
        except Exception:
            continue
    return None


def cdp_page_targets(
    cdp_url: str, timeout: float = 0.5
) -> tuple[str | None, list[dict[str, Any]]]:
    if cdp_url.startswith("ws://") or cdp_url.startswith("wss://"):
        return None, []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for candidate in cdp_candidates(cdp_url):
        try:
            with opener.open(candidate.rstrip("/") + "/json/list", timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, list):
                return candidate.rstrip("/"), [item for item in data if isinstance(item, dict)]
        except Exception:
            continue
    return None, []


def ensure_wps_cdp_page(
    cdp_url: str,
    initial_url: str = "https://www.kdocs.cn/",
    *,
    require_initial_url: bool = False,
) -> bool:
    base_url, targets = cdp_page_targets(cdp_url)
    page_targets = [target for target in targets if target.get("type") == "page"]
    target_key = initial_url.rstrip("/").split("/")[-1]
    if require_initial_url and target_key:
        if any(target_key in str(target.get("url") or "") for target in page_targets):
            return False
    elif page_targets:
        return False
    if base_url is None:
        raise RuntimeError("无法读取专用浏览器的页面列表。")

    encoded_url = urllib.parse.quote(initial_url, safe="")
    request = urllib.request.Request(
        f"{base_url}/json/new?{encoded_url}", method="PUT"
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        created = json.loads(response.read().decode("utf-8"))
    if not isinstance(created, dict) or created.get("type") != "page":
        raise RuntimeError("专用浏览器未能创建页面，请重试。")
    return True


def managed_chrome_state_version() -> int:
    try:
        state = json.loads(WPS_CHROME_STATE_PATH.read_text(encoding="utf-8"))
        return int(state.get("version") or 0)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def managed_chrome_command(pid: int) -> str:
    if sys.platform != "darwin":
        return ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except subprocess.SubprocessError:
        return ""
    return result.stdout.strip()


def is_managed_chrome_command(command: str) -> bool:
    profile_argument = f"--user-data-dir={PROFILE_DIR}"
    is_browser_main_process = (
        "/Contents/MacOS/Google Chrome" in command
        or "/Contents/MacOS/Chromium" in command
    )
    return (
        profile_argument in command
        and is_browser_main_process
        and "Google Chrome Helper" not in command
        and "--type=" not in command
    )


def discover_managed_chrome_pid() -> int | None:
    """Find a dedicated Chrome whose PID changed without updating our PID file."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.SubprocessError:
        return None

    candidates: list[tuple[bool, int]] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        pid = int(fields[0])
        command = fields[1]
        if is_managed_chrome_command(command):
            candidates.append(("--remote-debugging-port=" in command, pid))
    if not candidates:
        return None
    return max(candidates)[1]


def remember_managed_chrome(pid: int, version: int, *, headless: bool = False) -> None:
    WPS_CHROME_PID_PATH.write_text(str(pid), encoding="utf-8")
    WPS_CHROME_STATE_PATH.write_text(
        json.dumps(
            {"pid": pid, "version": version, "headless": headless},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def clear_managed_chrome_state(pid: int) -> None:
    try:
        recorded_pid = int(WPS_CHROME_PID_PATH.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        recorded_pid = None
    if recorded_pid != pid:
        return
    for path in (WPS_CHROME_PID_PATH, WPS_CHROME_STATE_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def managed_chrome_pid() -> int | None:
    recorded_pid: int | None = None
    try:
        recorded_pid = int(WPS_CHROME_PID_PATH.read_text(encoding="utf-8").strip())
        os.kill(recorded_pid, 0)
        if sys.platform == "darwin":
            if not is_managed_chrome_command(managed_chrome_command(recorded_pid)):
                raise ProcessLookupError
        return recorded_pid
    except (
        FileNotFoundError,
        ValueError,
        ProcessLookupError,
        PermissionError,
        subprocess.SubprocessError,
    ):
        pass

    discovered_pid = discover_managed_chrome_pid()
    if discovered_pid is None:
        if recorded_pid is not None:
            clear_managed_chrome_state(recorded_pid)
        return None
    try:
        remember_managed_chrome(discovered_pid, managed_chrome_state_version())
    except OSError:
        pass
    return discovered_pid


def managed_chrome_launch_version(pid: int) -> int:
    try:
        state = json.loads(WPS_CHROME_STATE_PATH.read_text(encoding="utf-8"))
        if int(state.get("pid") or 0) != pid:
            return 0
        return int(state.get("version") or 0)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def managed_chrome_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    if sys.platform != "darwin":
        return True
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except subprocess.SubprocessError:
        return True
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


def stop_managed_chrome(pid: int, log, reason: str, timeout_seconds: float = 8) -> None:
    log(f"正在重启专用浏览器：{reason}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_managed_chrome_state(pid)
        return

    deadline = time_module.monotonic() + timeout_seconds
    while managed_chrome_is_running(pid) and time_module.monotonic() < deadline:
        time_module.sleep(0.2)
    if managed_chrome_is_running(pid):
        log("专用浏览器未正常关闭，正在结束其进程。")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            clear_managed_chrome_state(pid)
            return
        force_deadline = time_module.monotonic() + 2
        while managed_chrome_is_running(pid) and time_module.monotonic() < force_deadline:
            time_module.sleep(0.1)
    if managed_chrome_is_running(pid):
        raise RuntimeError(
            "专用浏览器无响应，自动重启失败。"
            "请关闭专用浏览器窗口后重试。"
        )
    clear_managed_chrome_state(pid)


def managed_chrome_is_headless(pid: int) -> bool:
    return "--headless" in managed_chrome_command(pid)


def stop_outdated_managed_chrome(
    log, cdp_url: str, *, headless: bool = False
) -> None:
    pid = managed_chrome_pid()
    if pid is None:
        return
    if managed_chrome_launch_version(pid) < WPS_CHROME_LAUNCH_VERSION:
        reason = "正在应用更新后的浏览器设置"
    elif managed_chrome_is_headless(pid) != headless:
        reason = (
            "正在切换为后台运行"
            if headless
            else "正在打开浏览器供您登录"
        )
    else:
        return
    stop_managed_chrome(
        pid,
        log,
        reason,
    )


def _ensure_wps_dedicated_chrome(
    cdp_url: str = "http://127.0.0.1:9222",
    log=lambda _message: None,
    timeout_seconds: float = 15,
    initial_url: str = "https://www.kdocs.cn/",
    preload_url: bool = False,
    headless: bool = False,
) -> dict[str, Any]:
    stop_outdated_managed_chrome(log, cdp_url, headless=headless)
    existing_endpoint = try_resolve_cdp_endpoint(cdp_url)
    if existing_endpoint:
        ensure_wps_cdp_page(
            cdp_url,
            initial_url,
            require_initial_url=preload_url,
        )
        log("已连接正在运行的专用浏览器。")
        return {
            "started": False,
            "endpoint": existing_endpoint,
            "headless": headless,
        }
    existing_pid = managed_chrome_pid()
    if existing_pid is not None:
        # Chrome can briefly stop answering /json/version while a heavy KDocs
        # page is busy. Give the owned process time to recover instead of
        # launching a second process against the same profile.
        recovery_deadline = time_module.monotonic() + 3
        while time_module.monotonic() < recovery_deadline:
            endpoint = try_resolve_cdp_endpoint(cdp_url)
            if endpoint:
                ensure_wps_cdp_page(
                    cdp_url,
                    initial_url,
                    require_initial_url=preload_url,
                )
                log("已连接正在运行的专用浏览器。")
                return {
                    "started": False,
                    "endpoint": endpoint,
                    "pid": existing_pid,
                    "headless": headless,
                }
            time_module.sleep(0.2)
        stop_managed_chrome(
            existing_pid,
            log,
            "浏览器仍在运行，但连接无响应",
        )
    if sys.platform != "darwin":
        raise RuntimeError("当前系统不支持自动启动专用浏览器，请手动打开后连接。")

    chrome_path = find_macos_chrome()
    if chrome_path is None:
        raise RuntimeError("未找到可用浏览器，请先安装谷歌浏览器。")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        PROFILE_DIR.chmod(0o700)
    except OSError:
        pass
    command = build_wps_chrome_command(
        chrome_path,
        cdp_url,
        initial_url,
        headless=headless,
    )
    mode_label = "后台运行" if headless else "供您登录"
    log(f"正在启动专用浏览器（{mode_label}）…")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    remember_managed_chrome(
        process.pid,
        WPS_CHROME_LAUNCH_VERSION,
        headless=headless,
    )
    deadline = time_module.monotonic() + timeout_seconds
    while time_module.monotonic() < deadline:
        endpoint = try_resolve_cdp_endpoint(cdp_url)
        if endpoint:
            ensure_wps_cdp_page(
                cdp_url,
                initial_url,
                require_initial_url=preload_url,
            )
            if headless:
                log("专用浏览器已在后台就绪。")
            else:
                log("专用浏览器已打开；如出现登录页面，请先完成登录。")
            return {
                "started": True,
                "endpoint": endpoint,
                "pid": process.pid,
                "headless": headless,
            }
        if process.poll() is not None:
            break
        time_module.sleep(0.2)
    raise RuntimeError(
        "无法连接专用浏览器。"
        "请关闭使用该登录资料的浏览器窗口后重试。"
    )


def ensure_wps_dedicated_chrome(
    cdp_url: str = "http://127.0.0.1:9222",
    log=lambda _message: None,
    timeout_seconds: float = 15,
    initial_url: str = "https://www.kdocs.cn/",
    preload_url: bool = False,
    headless: bool = False,
) -> dict[str, Any]:
    with WPS_CHROME_START_LOCK:
        return _ensure_wps_dedicated_chrome(
            cdp_url,
            log,
            timeout_seconds,
            initial_url,
            preload_url,
            headless,
        )


def open_browser_context(
    playwright,
    browser_mode: str,
    cdp_url: str,
    log,
    initial_url: str | None = None,
    *,
    for_login_check: bool = False,
):
    if browser_mode in {"managed_cdp", "cdp"}:
        if browser_mode == "managed_cdp":
            # Automatic reads/writes use the saved WPS profile in a headless
            # Chrome process. Only the explicit login button opens a window.
            if for_login_check and try_resolve_cdp_endpoint(cdp_url):
                # Do not close the visible browser while the user is logging in.
                ensure_wps_cdp_page(cdp_url)
            else:
                ensure_wps_dedicated_chrome(cdp_url, log, headless=True)
        else:
            log(f"正在连接已打开的浏览器：{cdp_url}")
        endpoint = resolve_cdp_endpoint(cdp_url)
        log("正在连接浏览器并检查表格页面…")
        try:
            browser = playwright.chromium.connect_over_cdp(
                endpoint, timeout=CDP_CONNECT_TIMEOUT_MS
            )
        except Exception as exc:
            if browser_mode != "managed_cdp":
                raise
            if "context management is not supported" in str(exc):
                log("专用浏览器中没有页面，正在打开金山文档并重新连接。")
                ensure_wps_cdp_page(cdp_url)
            elif isinstance(exc, PlaywrightTimeoutError) and not for_login_check:
                # The HTTP status endpoint can still respond while every renderer
                # is stuck. Restart only the browser owned by this tool, before
                # any spreadsheet operation has begun.
                with WPS_CHROME_START_LOCK:
                    pid = managed_chrome_pid()
                    if pid is None:
                        raise RuntimeError(
                            "浏览器连接超时，请关闭专用浏览器后重新打开。"
                        ) from exc
                    stop_managed_chrome(pid, log, "表格页面无响应，连接超时")
                    _ensure_wps_dedicated_chrome(
                        cdp_url, log, headless=True,
                        initial_url=initial_url or "https://www.kdocs.cn/",
                    )
            else:
                raise
            endpoint = resolve_cdp_endpoint(cdp_url)
            try:
                browser = playwright.chromium.connect_over_cdp(
                    endpoint, timeout=CDP_CONNECT_TIMEOUT_MS
                )
            except Exception as retry_error:
                raise RuntimeError(
                    "重新连接浏览器失败，请打开专用浏览器检查登录状态后重试。"
                    f"首次连接失败原因：{exc}"
                ) from retry_error
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        if (
            browser_mode == "cdp"
            and initial_url
            and ensure_background_cdp_target(browser, cdp_url, initial_url)
        ):
            # Playwright discovers background targets that existed when the CDP
            # connection was established. Reconnect once after creating it.
            browser = playwright.chromium.connect_over_cdp(
                endpoint, timeout=CDP_CONNECT_TIMEOUT_MS
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        return browser, context, False

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    log("正在启动临时浏览器，并载入已保存的登录信息…")
    try:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=for_login_check,
            channel=None if os.environ.get("TOOLBOX_PORTABLE") == "1" else "chrome",
            viewport={"width": 1400, "height": 900},
        )
    except Exception:
        log("未能启动系统浏览器，正在尝试使用内置浏览器。")
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=for_login_check,
            viewport={"width": 1400, "height": 900},
        )
    return None, context, True


def write_to_wps(
    url: str,
    prepared: list[PreparedMapping],
    log,
    browser_mode: str = "persistent",
    cdp_url: str = "http://127.0.0.1:9222",
) -> dict[str, Any]:
    if sync_playwright is None:
        raise RuntimeError("缺少浏览器自动化组件，请运行工具箱目录中的 setup_macos.command 完成初始化。")

    payload = [
        {
            "index": item.index,
            "targetSheet": item.target_sheet,
            "targetRange": item.target_range,
            "rows": item.rows,
            "cols": item.cols,
            "values": item.values,
        }
        for item in prepared
    ]

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        try:
            browser, context, close_context = open_browser_context(
                p, browser_mode, cdp_url, log, initial_url=url
            )
        except Exception as exc:
            if browser_mode == "cdp":
                raise RuntimeError(
                    "无法连接已打开的浏览器，请使用独立的用户资料目录和远程调试端口启动，"
                    "并在该窗口登录金山文档。"
                ) from exc
            raise
        try:
            log(f"正在打开目标表格：{url}")
            page = find_or_open_page(context, url)

            log("正在等待表格加载；如出现登录页面，请先在专用浏览器中完成登录。")
            writer_script = "async (payload) => {" + WPS_APPLICATION_GUARD + """
              if (typeof api.save !== "function") {
                throw new Error("WPS_API_NOT_READY: 表格保存功能尚未就绪");
              }
              const results = [];
              for (const item of payload) {
                const sheet = app.Worksheets(item.targetSheet);
                const range = sheet.Range(item.targetRange);
                const assignment = item.rows === 1 && item.cols === 1 ? item.values[0][0] : item.values;
                const mergeCells = await range.MergeCells;
                range.Value = assignment;
                results.push({
                  index: item.index,
                  targetSheet: item.targetSheet,
                  targetRange: item.targetRange,
                  rows: item.rows,
                  cols: item.cols,
                  mergeCells
                });
              }
              await api.save();
              for (const result of results) {
                const sheet = app.Worksheets(result.targetSheet);
                const range = sheet.Range(result.targetRange);
                result.value = await range.Value;
                result.value2 = await range.Value2;
                result.text = await range.Text;
              }
              return { saved: true, results };
            }
            """
            log(f"正在写入 {len(payload)} 个区域…")
            return evaluate_wps_operation(page, writer_script, payload, log, 180)
        finally:
            if close_context:
                context.close()


def read_online_document_info(url: str, browser_mode: str, cdp_url: str, log) -> dict[str, Any]:
    if sync_playwright is None:
        raise RuntimeError("缺少浏览器自动化组件，请运行工具箱目录中的 setup_macos.command 完成初始化。")

    started_at = time_module.monotonic()
    log("正在读取表格名称和工作表列表…")
    with sync_playwright() as p:
        browser, context, close_context = open_browser_context(
            p, browser_mode, cdp_url, log, initial_url=url
        )
        try:
            page = find_or_open_page(context, url)
            log(
                f"已打开表格，用时 {time_module.monotonic() - started_at:.1f} 秒；"
                "正在等待表格就绪…"
            )
            script = "async () => {" + WPS_APPLICATION_GUARD + """
              const count = await app.Worksheets.Count;
              const sheets = [];
              for (let i = 1; i <= Number(count); i++) {
                const ws = app.Worksheets(i);
                sheets.push(await ws.Name);
              }
              return {title: document.title, sheets};
            }
            """
            info = evaluate_wps_operation(page, script, None, log, 45)
            sheets = info["sheets"]
            title = info["title"] or page.title()
            log(
                f"表格信息读取完成，用时 {time_module.monotonic() - started_at:.1f} 秒，"
                f"共 {len(sheets)} 个工作表。"
            )
            return {"title": title, "sheets": sheets}
        finally:
            if close_context:
                context.close()


def read_online_sheet_names(url: str, browser_mode: str, cdp_url: str, log) -> list[str]:
    return read_online_document_info(url, browser_mode, cdp_url, log)["sheets"]


def read_online_range_values(
    url: str,
    sheet_name: str,
    range_text: str,
    rows: int,
    cols: int,
    browser_mode: str,
    cdp_url: str,
    log,
) -> list[list[Any]]:
    return read_online_ranges(
        [(url, sheet_name, range_text, rows, cols)], browser_mode, cdp_url, log
    )[0]


def read_online_ranges(
    requests: list[tuple[str, str, str, int, int]],
    browser_mode: str,
    cdp_url: str,
    log,
) -> list[list[list[Any]]]:
    """Read source regions in order using one connection and browser context."""
    if not requests:
        return []
    if sync_playwright is None:
        raise RuntimeError("缺少浏览器自动化组件，请运行工具箱目录中的 setup_macos.command 完成初始化。")

    with sync_playwright() as p:
        browser, context, close_context = open_browser_context(
            p, browser_mode, cdp_url, log, initial_url=requests[0][0]
        )
        try:
            script = "async (payload) => {" + WPS_APPLICATION_GUARD + """
              const {sheetName, rangeText} = payload;
              const sheet = await app.Worksheets(sheetName);
              const range = await sheet.Range(rangeText);
              return await range.Value;
            }
            """
            results = []
            for url, sheet_name, range_text, rows, cols in requests:
                page = find_or_open_page(context, url)
                raw = evaluate_wps_operation(
                    page, script, {"sheetName": sheet_name, "rangeText": range_text}, log, 120
                )
                matrix = coerce_wps_matrix(raw, rows, cols)
                results.append([[normalize_cell_value(cell) for cell in row] for row in matrix])
            return results
        finally:
            if close_context:
                context.close()


class MappingDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        title: str,
        initial: MappingRow | None = None,
        recent_urls: list[str] | None = None,
        browser_mode: str = "persistent",
        cdp_url: str = "http://127.0.0.1:9222",
        log=None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: MappingRow | None = None
        default_url = initial.kdocs_url if initial else ((recent_urls or [""])[0])
        self.local_sheet_combo: ttk.Combobox | None = None
        self.online_sheet_combo: ttk.Combobox | None = None
        self.kdocs_url_combo: ttk.Combobox | None = None
        self.last_online_sheet_url = ""
        self.browser_mode = browser_mode
        self.cdp_url = cdp_url
        self.log = log or (lambda _message: None)

        self.vars = {
            "kdocs_url": tk.StringVar(value=default_url),
            "local_file": tk.StringVar(value=initial.local_file if initial else ""),
            "source_sheet": tk.StringVar(value=initial.source_sheet if initial else ""),
            "source_start": tk.StringVar(value=initial.source_start if initial else ""),
            "source_end": tk.StringVar(value=initial.source_end if initial else ""),
            "target_sheet": tk.StringVar(value=initial.target_sheet if initial else ""),
            "target_start": tk.StringVar(value=initial.target_start if initial else ""),
            "target_end": tk.StringVar(value=initial.target_end if initial else ""),
        }

        labels = [
            ("目标表格链接", "kdocs_url"),
            ("本地文件", "local_file"),
            ("来源工作表", "source_sheet"),
            ("来源起始单元格", "source_start"),
            ("来源结束单元格", "source_end"),
            ("目标工作表", "target_sheet"),
            ("目标起始单元格", "target_start"),
            ("目标结束单元格", "target_end"),
        ]
        for row, (label, key) in enumerate(labels):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=10, pady=6, sticky="w")
            if key == "kdocs_url":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=recent_urls or [], width=64)
                self.kdocs_url_combo = entry
                entry.bind("<<ComboboxSelected>>", lambda _event: self.refresh_online_sheets(auto=True))
                entry.bind("<FocusOut>", lambda _event: self.refresh_online_sheets(auto=True))
                entry.bind("<Return>", lambda _event: self.refresh_online_sheets(auto=True))
            elif key == "source_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=[], width=36)
                self.local_sheet_combo = entry
            elif key == "target_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=[], width=36)
                self.online_sheet_combo = entry
            else:
                entry = ttk.Entry(self, textvariable=self.vars[key], width=64 if key == "local_file" else 36)
            entry.grid(row=row, column=1, padx=10, pady=6, sticky="ew")
            if key == "local_file":
                button_frame = ttk.Frame(self)
                button_frame.grid(row=row, column=2, padx=10, pady=6)
                ttk.Button(button_frame, text="选择文件", command=self.browse_local_file).pack(side="left")
                ttk.Button(button_frame, text="刷新工作表", command=self.refresh_local_sheets).pack(side="left", padx=(6, 0))
            elif key == "target_sheet":
                ttk.Button(self, text="刷新目标工作表", command=self.refresh_online_sheets).grid(
                    row=row, column=2, padx=10, pady=6
                )

        button_frame = ttk.Frame(self)
        button_frame.grid(row=len(labels), column=0, columnspan=3, padx=10, pady=10, sticky="e")
        ttk.Button(button_frame, text="确定", command=self.on_ok).pack(side="left", padx=4)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda _event: self.on_ok())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.refresh_local_sheets(show_errors=False)
        self.transient(parent)
        self.grab_set()
        center_window(self)
        self.wait_visibility()
        self.focus()
        self.wait_window(self)

    def on_ok(self) -> None:
        row = MappingRow(
            kdocs_url=self.vars["kdocs_url"].get().strip(),
            local_file=self.vars["local_file"].get().strip(),
            source_sheet=self.vars["source_sheet"].get().strip(),
            source_start=self.vars["source_start"].get().strip(),
            source_end=self.vars["source_end"].get().strip(),
            target_sheet=self.vars["target_sheet"].get().strip(),
            target_start=self.vars["target_start"].get().strip(),
            target_end=self.vars["target_end"].get().strip(),
        )
        if not all(
            [
                row.kdocs_url,
                row.local_file,
                row.source_sheet,
                row.source_start,
                row.source_end,
                row.target_sheet,
                row.target_start,
                row.target_end,
            ]
        ):
            messagebox.showerror("信息未填完整", "请填写来源文件、目标表格链接及两边的工作表和单元格范围。", parent=self)
            return
        self.result = row
        self.destroy()

    def browse_local_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择来源表格文件",
            filetypes=[("表格文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if path:
            self.vars["local_file"].set(path)
            self.refresh_local_sheets(show_errors=True)

    def refresh_local_sheets(self, show_errors: bool = True) -> None:
        if self.local_sheet_combo is None:
            return
        path_text = self.vars["local_file"].get().strip()
        if not path_text:
            self.local_sheet_combo.configure(values=[])
            return
        try:
            names = read_excel_sheet_names(Path(path_text).expanduser())
        except Exception as exc:
            self.local_sheet_combo.configure(values=[])
            if show_errors:
                messagebox.showerror("读取工作表失败", str(exc), parent=self)
            return
        self.local_sheet_combo.configure(values=names)
        current = self.vars["source_sheet"].get().strip()
        if names and not current:
            self.vars["source_sheet"].set(names[0])

    def refresh_online_sheets(self, auto: bool = False) -> None:
        if self.online_sheet_combo is None:
            return
        url = self.vars["kdocs_url"].get().strip()
        if not url:
            if auto:
                return
            messagebox.showerror("请填写表格链接", "请先填写目标表格链接，再刷新工作表。", parent=self)
            return
        if auto and url == self.last_online_sheet_url:
            return
        try:
            names = read_online_sheet_names(url, self.browser_mode, self.cdp_url, lambda _message: None)
        except Exception as exc:
            self.online_sheet_combo.configure(values=[])
            self.last_online_sheet_url = ""
            messagebox.showerror("读取目标工作表失败", str(exc), parent=self)
            return
        self.online_sheet_combo.configure(values=names)
        self.last_online_sheet_url = url
        current = self.vars["target_sheet"].get().strip()
        if names and not current:
            self.vars["target_sheet"].set(names[0])


class RegionDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Toplevel,
        title: str,
        initial: RegionRow | None = None,
        local_sheets: list[str] | None = None,
        online_sheets: list[str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: RegionRow | None = None

        self.vars = {
            "source_sheet": tk.StringVar(value=initial.source_sheet if initial else ""),
            "source_start": tk.StringVar(value=initial.source_start if initial else ""),
            "source_end": tk.StringVar(value=initial.source_end if initial else ""),
            "target_sheet": tk.StringVar(value=initial.target_sheet if initial else ""),
            "target_start": tk.StringVar(value=initial.target_start if initial else ""),
            "target_end": tk.StringVar(value=initial.target_end if initial else ""),
        }

        labels = [
            ("来源工作表", "source_sheet"),
            ("来源起始单元格", "source_start"),
            ("来源结束单元格", "source_end"),
            ("目标工作表", "target_sheet"),
            ("目标起始单元格", "target_start"),
            ("目标结束单元格", "target_end"),
        ]
        for row, (label, key) in enumerate(labels):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=10, pady=6, sticky="w")
            if key == "source_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=local_sheets or [], width=42)
            elif key == "target_sheet":
                entry = ttk.Combobox(self, textvariable=self.vars[key], values=online_sheets or [], width=42)
            else:
                entry = ttk.Entry(self, textvariable=self.vars[key], width=44)
            entry.grid(row=row, column=1, padx=10, pady=6, sticky="ew")

        if not initial:
            if local_sheets:
                self.vars["source_sheet"].set(local_sheets[0])
            if online_sheets:
                self.vars["target_sheet"].set(online_sheets[0])

        button_frame = ttk.Frame(self)
        button_frame.grid(row=len(labels), column=0, columnspan=2, padx=10, pady=10, sticky="e")
        ttk.Button(button_frame, text="确定", command=self.on_ok).pack(side="left", padx=4)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda _event: self.on_ok())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        center_window(self)
        self.wait_visibility()
        self.focus()
        self.wait_window(self)

    def on_ok(self) -> None:
        row = RegionRow(
            source_sheet=self.vars["source_sheet"].get().strip(),
            source_start=self.vars["source_start"].get().strip(),
            source_end=self.vars["source_end"].get().strip(),
            target_sheet=self.vars["target_sheet"].get().strip(),
            target_start=self.vars["target_start"].get().strip(),
            target_end=self.vars["target_end"].get().strip(),
        )
        if not all([row.source_sheet, row.source_start, row.source_end, row.target_sheet, row.target_start, row.target_end]):
            messagebox.showerror("写入区域未填完整", "请填写来源和目标的工作表、起始单元格及结束单元格。", parent=self)
            return
        self.result = row
        self.destroy()


class DocumentConfigDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        title: str,
        initial: DocumentConfig | None = None,
        recent_urls: list[str] | None = None,
        browser_mode: str = "persistent",
        cdp_url: str = "http://127.0.0.1:9222",
        log=None,
        autosave=None,
    ):
        super().__init__(parent)
        self.title(title)
        self.geometry("900x620")
        self.minsize(820, 520)
        self.result: DocumentConfig | None = None
        self.recent_urls = recent_urls or []
        self.browser_mode = browser_mode
        self.cdp_url = cdp_url
        self.log = log or (lambda _message: None)
        self.autosave = autosave
        self.local_sheets: list[str] = list(
            dict.fromkeys(
                region.source_sheet
                for region in (initial.regions if initial else [])
                if region.source_sheet
            )
        )
        self.online_sheets: list[str] = list(
            dict.fromkeys(
                region.target_sheet
                for region in (initial.regions if initial else [])
                if region.target_sheet
            )
        )
        self.last_online_sheet_url = ""
        self.regions: list[RegionRow] = list(initial.regions) if initial else []
        self._browser_read_lock = threading.Lock()
        self._online_results: queue.Queue[
            tuple[str, int, str, bool, dict[str, Any] | None, str | None]
        ] = queue.Queue()
        self._request_ids = {"source": 0, "target": 0}
        self._pending_online_reads: dict[str, tuple[int, str]] = {}
        self._online_poll_after_id: str | None = None
        self._online_workers: set[threading.Thread] = set()
        self._online_workers_lock = threading.Lock()

        default_url = initial.kdocs_url if initial else (self.recent_urls[0] if self.recent_urls else "")
        default_source_url = initial.source_url if initial else ""
        self.config_name = tk.StringVar(value=initial.name if initial else "")
        self.source_type = tk.StringVar(value=initial.source_type if initial else "excel")
        self.source_url = tk.StringVar(value=default_source_url)
        self.kdocs_url = tk.StringVar(value=default_url)
        self.local_file = tk.StringVar(value=initial.local_file if initial else "")
        self.source_doc_title = tk.StringVar(value="")
        self.target_doc_title = tk.StringVar(value="")
        cached_sheet_count = len(self.online_sheets)
        cached_status = (
            f"已载入配置中保存的 {cached_sheet_count} 个工作表名称；"
            "如表格有变动，请刷新工作表。"
            if cached_sheet_count
            else ""
        )
        self.read_status = tk.StringVar(value=cached_status)

        self.create_widgets()
        # Editing an existing configuration should immediately restore the
        # target document title and current sheet list. The read is asynchronous
        # and managed mode uses the saved profile in headless Chrome, so the
        # dialog remains responsive and no browser window is shown.
        if initial is not None and default_url:
            self.refresh_online_sheets(auto=True)
        if self.source_type.get() == "excel":
            self.refresh_local_sheets(show_errors=False)

        self.transient(parent)
        self.grab_set()
        center_window(self, 900, 620)
        self.wait_visibility()
        self.focus()
        self.wait_window(self)

    def create_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        docs = ttk.LabelFrame(root, text="来源与目标")
        docs.grid(row=0, column=0, sticky="ew")
        ttk.Label(docs, text="配置名称").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(docs, textvariable=self.config_name).grid(row=0, column=1, columnspan=2, padx=8, pady=6, sticky="ew")

        ttk.Label(docs, text="数据来源").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        source_frame = SegmentedGroup(docs, variable=self.source_type,
                                      choices=(("excel", "本地表格"), ("kdocs", "金山文档")))
        source_frame.grid(row=1, column=1, columnspan=2, padx=8, pady=6, sticky="w")

        ttk.Label(docs, text="来源表格链接").grid(row=2, column=0, padx=8, pady=6, sticky="w")
        source_url_combo = ttk.Combobox(docs, textvariable=self.source_url, values=self.recent_urls)
        source_url_combo.grid(row=2, column=1, padx=8, pady=6, sticky="ew")
        self.refresh_source_button = ttk.Button(
            docs, text="刷新来源工作表", command=self.refresh_local_sheets
        )
        self.refresh_source_button.grid(row=2, column=2, padx=8, pady=6)

        ttk.Label(docs, text="目标表格链接").grid(row=3, column=0, padx=8, pady=6, sticky="w")
        url_combo = ttk.Combobox(docs, textvariable=self.kdocs_url, values=self.recent_urls)
        url_combo.grid(row=3, column=1, padx=8, pady=6, sticky="ew")
        self.refresh_target_button = ttk.Button(
            docs, text="刷新目标工作表", command=self.refresh_online_sheets
        )
        self.refresh_target_button.grid(row=3, column=2, padx=8, pady=6)

        ttk.Label(docs, text="来源表格名称").grid(row=4, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(docs, textvariable=self.source_doc_title, state="readonly").grid(row=4, column=1, columnspan=2, padx=8, pady=6, sticky="ew")

        ttk.Label(docs, text="目标表格名称").grid(row=5, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(docs, textvariable=self.target_doc_title, state="readonly").grid(row=5, column=1, columnspan=2, padx=8, pady=6, sticky="ew")

        ttk.Label(docs, text="本地文件").grid(row=6, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(docs, textvariable=self.local_file).grid(row=6, column=1, padx=8, pady=6, sticky="ew")
        file_buttons = ttk.Frame(docs)
        file_buttons.grid(row=6, column=2, padx=8, pady=6)
        ttk.Button(file_buttons, text="选择文件", command=self.browse_local_file).pack(side="left")
        ttk.Button(file_buttons, text="刷新工作表", command=self.refresh_local_sheets).pack(side="left", padx=(6, 0))
        ttk.Label(docs, textvariable=self.read_status, style="Muted.TLabel").grid(
            row=7, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="w"
        )
        docs.columnconfigure(1, weight=1)

        regions_frame = ttk.LabelFrame(root, text="写入区域")
        regions_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        toolbar = ttk.Frame(regions_frame)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(toolbar, text="添加区域", command=self.add_region).pack(side="left")
        ttk.Button(toolbar, text="编辑", command=self.edit_region).pack(side="left", padx=8)
        ttk.Button(toolbar, text="复制", command=self.copy_region).pack(side="left")
        ttk.Button(toolbar, text="删除", command=self.remove_region).pack(side="left", padx=8)

        tree_frame = ttk.Frame(regions_frame)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("source_sheet", "source_start", "source_end", "target_sheet", "target_start", "target_end")
        self.region_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        for key, label, width in [
            ("source_sheet", "来源工作表", 180),
            ("source_start", "来源起始单元格", 100),
            ("source_end", "来源结束单元格", 100),
            ("target_sheet", "目标工作表", 180),
            ("target_start", "目标起始单元格", 100),
            ("target_end", "目标结束单元格", 100),
        ]:
            self.region_tree.heading(key, text=label)
            self.region_tree.column(key, width=width)
        self.region_tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.region_tree.yview)
        scrollbar.pack(side="left", fill="y")
        self.region_tree.configure(yscrollcommand=scrollbar.set)
        self.region_tree.bind("<Double-1>", lambda _event: self.edit_region())

        button_frame = ttk.Frame(root)
        button_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(button_frame, text="保存配置", command=self.on_ok).pack(side="right", padx=4)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="right", padx=4)
        self.refresh_regions()

    def browse_local_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择来源表格文件",
            filetypes=[("表格文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if path:
            self.local_file.set(path)
            self.refresh_local_sheets(show_errors=True)

    def refresh_local_sheets(self, show_errors: bool = True) -> None:
        if self.source_type.get() == "kdocs":
            url = self.source_url.get().strip()
            if not url:
                self.local_sheets = []
                self.source_doc_title.set("")
                if show_errors:
                    messagebox.showerror("请填写表格链接", "请填写来源表格链接。", parent=self)
                return
            self._start_online_read("source", url, show_errors)
            return

        self._request_ids["source"] += 1
        self._pending_online_reads.pop("source", None)
        self.refresh_source_button.configure(state="normal")
        try:
            path_text = self.local_file.get().strip()
            if not path_text:
                self.local_sheets = []
                self.source_doc_title.set("")
                return
            self.local_sheets = read_excel_sheet_names(Path(path_text).expanduser())
            self.source_doc_title.set(Path(path_text).name)
        except Exception as exc:
            self.local_sheets = []
            if show_errors:
                messagebox.showerror("读取来源工作表失败", str(exc), parent=self)

    def refresh_online_sheets(self, auto: bool = False) -> None:
        url = self.kdocs_url.get().strip()
        if not url:
            if auto:
                return
            messagebox.showerror("请填写表格链接", "请先填写目标表格链接，再刷新工作表。", parent=self)
            return
        if auto and url == self.last_online_sheet_url:
            return
        self._start_online_read("target", url, not auto)

    def _start_online_read(self, kind: str, url: str, show_errors: bool) -> None:
        pending = self._pending_online_reads.get(kind)
        if pending is not None and pending[1] == url:
            return

        self._request_ids[kind] += 1
        request_id = self._request_ids[kind]
        self._pending_online_reads[kind] = (request_id, url)
        button = self.refresh_source_button if kind == "source" else self.refresh_target_button
        button.configure(state="disabled")
        if kind == "source":
            self.source_doc_title.set("正在读取…")
        else:
            self.target_doc_title.set("正在读取…")
        self.read_status.set("正在后台读取表格，您可以继续编辑配置…")

        # Keep the worker independent from this Toplevel. A browser read can outlive
        # the dialog, and retaining ``self`` from that thread lets Tk variables be
        # finalized off the main thread when the window has already been closed.
        browser_read_lock = self._browser_read_lock
        browser_mode = self.browser_mode
        cdp_url = self.cdp_url
        log = self.log
        online_results = self._online_results
        online_workers = self._online_workers
        online_workers_lock = self._online_workers_lock

        def worker() -> None:
            info: dict[str, Any] | None = None
            error: str | None = None
            try:
                # A persistent Playwright profile cannot be opened twice at once.
                with browser_read_lock:
                    info = read_online_document_info(
                        url, browser_mode, cdp_url, log
                    )
            except Exception as exc:
                error = str(exc)
            finally:
                online_results.put(
                    (kind, request_id, url, show_errors, info, error)
                )
                with online_workers_lock:
                    online_workers.discard(threading.current_thread())

        thread = threading.Thread(target=worker, daemon=True)
        with self._online_workers_lock:
            self._online_workers.add(thread)
        try:
            thread.start()
        except Exception:
            with self._online_workers_lock:
                self._online_workers.discard(thread)
            raise
        if self._online_poll_after_id is None:
            self._online_poll_after_id = self.after(50, self._drain_online_results)

    def destroy(self) -> None:
        if self._online_poll_after_id is not None:
            try:
                self.after_cancel(self._online_poll_after_id)
            except tk.TclError:
                pass
            self._online_poll_after_id = None
        self._pending_online_reads.clear()
        super().destroy()

    def _drain_online_results(self) -> None:
        self._online_poll_after_id = None
        while True:
            try:
                kind, request_id, url, show_errors, info, error = self._online_results.get_nowait()
            except queue.Empty:
                break
            if request_id != self._request_ids[kind]:
                continue

            self._pending_online_reads.pop(kind, None)
            button = self.refresh_source_button if kind == "source" else self.refresh_target_button
            button.configure(state="normal")
            if error is not None:
                if kind == "source":
                    self.source_doc_title.set("读取失败")
                    title = "读取来源工作表失败"
                else:
                    self.target_doc_title.set("读取失败")
                    self.last_online_sheet_url = ""
                    title = "读取目标工作表失败"
                self.read_status.set("读取失败，已保留原有工作表配置。请刷新重试或检查浏览器登录状态。")
                self.log(f"{title}：{error}")
                if show_errors:
                    messagebox.showerror(title, error, parent=self)
                continue

            sheets = list((info or {}).get("sheets") or [])
            document_title = str((info or {}).get("title") or "")
            if kind == "source":
                self.local_sheets = sheets
                self.source_doc_title.set(document_title)
            else:
                self.online_sheets = sheets
                self.target_doc_title.set(document_title)
                self.last_online_sheet_url = url
            self.read_status.set(f"已读取 {len(sheets)} 个工作表。")

        if self._pending_online_reads:
            self._online_poll_after_id = self.after(50, self._drain_online_results)

    def add_region(self) -> None:
        dialog = RegionDialog(self, "添加区域", local_sheets=self.local_sheets, online_sheets=self.online_sheets)
        if dialog.result:
            self.regions.append(dialog.result)
            self.refresh_regions()
            self.autosave_current_config()

    def edit_region(self) -> None:
        selected = self.region_tree.selection()
        if not selected:
            return
        index = self.region_tree.index(selected[0])
        dialog = RegionDialog(
            self,
            "编辑写入区域",
            self.regions[index],
            local_sheets=self.local_sheets,
            online_sheets=self.online_sheets,
        )
        if dialog.result:
            self.regions[index] = dialog.result
            self.refresh_regions()
            self.autosave_current_config()

    def copy_region(self) -> None:
        selected = self.region_tree.selection()
        if not selected:
            messagebox.showinfo("复制写入区域", "请先选择要复制的写入区域。", parent=self)
            return
        index = self.region_tree.index(selected[0])
        source = self.regions[index]
        copied = RegionRow(
            source_sheet=source.source_sheet,
            source_start=source.source_start,
            source_end=source.source_end,
            target_sheet=source.target_sheet,
            target_start=source.target_start,
            target_end=source.target_end,
        )
        self.regions.insert(index + 1, copied)
        self.refresh_regions()
        new_item = self.region_tree.get_children()[index + 1]
        self.region_tree.selection_set(new_item)
        self.region_tree.focus(new_item)
        self.region_tree.see(new_item)
        self.autosave_current_config()

    def remove_region(self) -> None:
        selected = self.region_tree.selection()
        if not selected:
            return
        count = len(selected)
        if not messagebox.askyesno(
            "确认删除",
            f"确定删除选中的 {count} 个写入区域吗？",
            parent=self,
        ):
            return
        for item in reversed(selected):
            del self.regions[self.region_tree.index(item)]
        self.refresh_regions()
        self.autosave_current_config()

    def build_current_config(self) -> DocumentConfig | None:
        kdocs_url = self.kdocs_url.get().strip()
        local_file = self.local_file.get().strip()
        source_type = self.source_type.get()
        source_url = self.source_url.get().strip()
        if not kdocs_url or not self.regions:
            return None
        if source_type == "excel" and not local_file:
            return None
        if source_type == "kdocs" and not source_url:
            return None
        name = self.config_name.get().strip() or Path(local_file).stem or "未命名配置"
        return DocumentConfig(
            name=name,
            kdocs_url=kdocs_url,
            local_file=local_file,
            regions=list(self.regions),
            source_type=source_type,
            source_url=source_url,
        )

    def autosave_current_config(self) -> None:
        if self.autosave is None:
            return
        config = self.build_current_config()
        if config is None:
            return
        self.autosave(config)

    def refresh_regions(self) -> None:
        for item in self.region_tree.get_children():
            self.region_tree.delete(item)
        for region in self.regions:
            self.region_tree.insert(
                "",
                "end",
                values=(
                    region.source_sheet,
                    region.source_start,
                    region.source_end,
                    region.target_sheet,
                    region.target_start,
                    region.target_end,
                ),
            )

    def on_ok(self) -> None:
        kdocs_url = self.kdocs_url.get().strip()
        local_file = self.local_file.get().strip()
        source_type = self.source_type.get()
        source_url = self.source_url.get().strip()
        if not kdocs_url:
            messagebox.showerror("配置未填完整", "请填写目标表格链接。", parent=self)
            return
        if source_type == "excel" and not local_file:
            messagebox.showerror("配置未填完整", "请选择本地表格文件。", parent=self)
            return
        if source_type == "kdocs" and not source_url:
            messagebox.showerror("配置未填完整", "请填写来源表格链接。", parent=self)
            return
        if not self.regions:
            messagebox.showerror("配置未填完整", "请至少添加一个写入区域。", parent=self)
            return
        name = self.config_name.get().strip() or Path(local_file).stem or "未命名配置"
        self.result = DocumentConfig(
            name=name,
            kdocs_url=kdocs_url,
            local_file=local_file,
            regions=list(self.regions),
            source_type=source_type,
            source_url=source_url,
        )
        if self.autosave is not None:
            self.autosave(self.result)
        self.destroy()


class WpsWriterFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, task_registry: Any | None = None) -> None:
        super().__init__(parent)
        self.task_registry = task_registry
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.browser_event_queue: queue.Queue[tuple[bool, str, bool]] = queue.Queue()
        self.login_event_queue: queue.Queue[tuple[str, str, WpsLoginStatus]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.browser_worker: threading.Thread | None = None
        self.configs: list[DocumentConfig] = []
        self._drain_after_id: str | None = None
        self._login_poll_after_id: str | None = None

        self.recent_urls: list[str] = []
        self.browser_mode = tk.StringVar(master=self, value="managed_cdp")
        self.cdp_url = tk.StringVar(master=self, value="http://127.0.0.1:9222")
        self.browser_status = tk.StringVar(master=self, value="尚未检查浏览器连接")
        self.login_status = tk.StringVar(master=self, value="尚未检查")

        self.create_widgets()
        self.load_initial_config()
        self.schedule_drain_logs()
        self.bind("<Map>", self._schedule_login_check, add="+")
        self.browser_mode.trace_add("write", self._invalidate_login_status)
        self.cdp_url.trace_add("write", self._invalidate_login_status)

    def create_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=4, minsize=DOCUMENT_CONFIGS_MIN_HEIGHT)
        root.rowconfigure(2, weight=1)

        file_frame = ttk.LabelFrame(root, text="浏览器连接")
        file_frame.grid(row=0, column=0, sticky="ew")

        ttk.Label(file_frame, text="连接方式").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        mode_frame = SegmentedGroup(file_frame, variable=self.browser_mode, choices=(
            ("managed_cdp", "WPS 专用浏览器（推荐）"),
            ("persistent", "临时浏览器"),
            ("cdp", "连接已打开的浏览器"),
        ))
        mode_frame.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        ttk.Label(file_frame, text="浏览器连接地址").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.cdp_url).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        browser_actions = ttk.Frame(file_frame)
        browser_actions.grid(row=2, column=1, padx=8, pady=(0, 8), sticky="ew")
        self.start_browser_button = ttk.Button(
            browser_actions,
            text="登录 WPS",
            command=self.start_wps_browser,
        )
        self.start_browser_button.pack(side="left")
        self.check_browser_button = ttk.Button(
            browser_actions,
            text="刷新登录状态",
            command=self.check_wps_browser,
        )
        self.check_browser_button.pack(side="left", padx=(8, 0))
        ttk.Label(browser_actions, textvariable=self.browser_status, style="Muted.TLabel").pack(
            side="left", padx=(12, 0)
        )
        ttk.Label(file_frame, text="WPS 登录状态").grid(
            row=3, column=0, padx=8, pady=(0, 8), sticky="w"
        )
        self.login_status_label = ttk.Label(
            file_frame, textvariable=self.login_status, wraplength=600
        )
        self.login_status_label.grid(row=3, column=1, padx=8, pady=(0, 8), sticky="w")
        login_hint = ttk.Label(
            file_frame,
            text="首次使用或更换电脑后，请先登录。完成登录后，状态会自动刷新。",
            style="Muted.TLabel", wraplength=600,
        )
        login_hint.grid(row=4, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="w")
        file_frame.bind("<Configure>", lambda event: (
            self.login_status_label.configure(wraplength=max(160, event.width - 150)),
            login_hint.configure(wraplength=max(240, event.width - 24)),
        ))
        file_frame.columnconfigure(1, weight=1)

        mapping_frame = ttk.LabelFrame(root, text="写入配置")
        mapping_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        mapping_frame.columnconfigure(0, weight=1)
        mapping_frame.rowconfigure(1, weight=1)

        self.mapping_toolbar = ttk.Frame(mapping_frame)
        self.mapping_toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        self.mapping_toolbar.columnconfigure(2, weight=1)

        self.mapping_primary_toolbar = ttk.Frame(self.mapping_toolbar)
        self.mapping_primary_toolbar.grid(row=0, column=0, sticky="w")
        primary_actions = [
            ("添加配置", self.add_config),
            ("编辑", self.edit_config),
            ("复制", self.copy_config),
            ("删除", self.remove_config),
        ]
        for column, (text, command) in enumerate(primary_actions):
            ttk.Button(
                self.mapping_primary_toolbar,
                text=text,
                command=command,
                width=0,
            ).grid(row=0, column=column, padx=(0 if column == 0 else 4, 0))

        self.mapping_sort_toolbar = ttk.Frame(self.mapping_toolbar)
        self.mapping_sort_toolbar.grid(
            row=0, column=3, sticky="e", padx=(12, 0)
        )
        ttk.Button(
            self.mapping_sort_toolbar, text="上移", command=self.move_config_up, width=0
        ).pack(side="left")
        ttk.Button(
            self.mapping_sort_toolbar, text="下移", command=self.move_config_down, width=0
        ).pack(side="left", padx=(4, 0))

        self.mapping_secondary_toolbar = ttk.Frame(self.mapping_toolbar)
        self.mapping_secondary_toolbar.grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Button(
            self.mapping_secondary_toolbar, text="导入", command=self.import_config, width=0
        ).pack(side="left")
        ttk.Button(
            self.mapping_secondary_toolbar, text="导出", command=self.export_config, width=0
        ).pack(side="left", padx=(4, 0))

        tree_frame = ttk.Frame(mapping_frame)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        columns = ("name", "source_type", "source", "kdocs_url", "region_count", "summary")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)
        self.tree.heading("name", text="配置名称")
        self.tree.heading("source_type", text="数据来源")
        self.tree.heading("source", text="来源文件或链接")
        self.tree.heading("kdocs_url", text="目标表格链接")
        self.tree.heading("region_count", text="区域数量")
        self.tree.heading("summary", text="首个写入区域")
        # Match the reference image: compact names/counts, wider source and summary.
        column_widths = (145, 115, 470, 320, 125, 495)
        for column, width in zip(columns, column_widths):
            self.tree.heading(column, anchor="center")
            self.tree.column(column, width=width // 2, anchor="center", stretch=False)

        def resize_config_columns(event) -> None:
            # Keep all six columns visible at normal sizes, with horizontal
            # scrolling available when the window is too narrow.
            available = max(840, event.width - 2)
            remaining = available
            for index, (column, width) in enumerate(zip(columns, column_widths)):
                actual_width = (
                    remaining if index == len(columns) - 1
                    else round(available * width / sum(column_widths))
                )
                self.tree.column(column, width=actual_width)
                remaining -= actual_width

        self.tree.bind("<Configure>", resize_config_columns)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal_scrollbar.set)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda _event: self.edit_config())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_selection_status())

        self.execution_toolbar = ttk.Frame(mapping_frame)
        self.execution_toolbar.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.selection_status = tk.StringVar(value="请先选择配置，再预览或写入。")
        ttk.Label(
            self.execution_toolbar,
            textvariable=self.selection_status,
            style="Muted.TLabel",
        ).pack(side="left")
        ttk.Button(
            self.execution_toolbar, text="开始写入", command=self.write, style="Primary.TButton"
        ).pack(side="right")
        ttk.Button(
            self.execution_toolbar, text="预览", command=self.preview
        ).pack(side="right", padx=(0, 6))

        self.log_console = LogConsole(root, module="WPS写入工具", directory=LOG_DIR / "runs")
        self.module_log_frame = self.log_console
        self.module_log_frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self.log_text = self.log_console.text

    def add_config(self) -> None:
        dialog = DocumentConfigDialog(
            self,
            "添加配置",
            recent_urls=self.recent_urls,
            browser_mode=self.browser_mode.get(),
            cdp_url=self.cdp_url.get().strip(),
            log=self.log,
        )
        if dialog.result:
            self.configs.append(dialog.result)
            self.remember_mapping_urls()
            self.refresh_tree()
            self.save_current_config()

    def edit_config(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        index = self.tree.index(selected[0])

        def autosave(config: DocumentConfig) -> None:
            self.configs[index] = config
            self.remember_mapping_urls()
            self.save_current_config()

        dialog = DocumentConfigDialog(
            self,
            "编辑配置",
            self.configs[index],
            recent_urls=self.recent_urls,
            browser_mode=self.browser_mode.get(),
            cdp_url=self.cdp_url.get().strip(),
            log=self.log,
            autosave=autosave,
        )
        if dialog.result:
            self.configs[index] = dialog.result
            self.remember_mapping_urls()
            self.refresh_tree()
            self.save_current_config()

    def copy_config(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("复制配置", "请先选择要复制的配置。", parent=self)
            return
        index = self.tree.index(selected[0])
        source = self.configs[index]
        copied = DocumentConfig(
            name=f"{source.name}（副本）",
            kdocs_url=source.kdocs_url,
            local_file=source.local_file,
            source_type=source.source_type,
            source_url=source.source_url,
            regions=[
                RegionRow(
                    source_sheet=region.source_sheet,
                    source_start=region.source_start,
                    source_end=region.source_end,
                    target_sheet=region.target_sheet,
                    target_start=region.target_start,
                    target_end=region.target_end,
                )
                for region in source.regions
            ],
        )
        self.configs.insert(index + 1, copied)
        self.refresh_tree()
        new_item = self.tree.get_children()[index + 1]
        self.tree.selection_set(new_item)
        self.tree.focus(new_item)
        self.tree.see(new_item)
        self.save_current_config()

    def remove_config(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        count = len(selected)
        if not messagebox.askyesno(
            "确认删除",
            f"确定删除选中的 {count} 项配置吗？",
            parent=self,
        ):
            return
        for item in reversed(selected):
            del self.configs[self.tree.index(item)]
        self.refresh_tree()
        self.save_current_config()

    def move_config_up(self) -> None:
        indexes = self.selected_config_indexes()
        if not indexes:
            return
        indexes = sorted(indexes)
        for index in indexes:
            if index == 0:
                continue
            self.configs[index - 1], self.configs[index] = self.configs[index], self.configs[index - 1]
        new_indexes = [max(0, index - 1) for index in indexes]
        self.refresh_tree()
        self.select_config_indexes(new_indexes)
        self.save_current_config()

    def move_config_down(self) -> None:
        indexes = self.selected_config_indexes()
        if not indexes:
            return
        indexes = sorted(indexes, reverse=True)
        last = len(self.configs) - 1
        for index in indexes:
            if index >= last:
                continue
            self.configs[index + 1], self.configs[index] = self.configs[index], self.configs[index + 1]
        new_indexes = [min(last, index + 1) for index in indexes]
        self.refresh_tree()
        self.select_config_indexes(new_indexes)
        self.save_current_config()

    def select_config_indexes(self, indexes: list[int]) -> None:
        children = self.tree.get_children()
        items = [children[index] for index in sorted(set(indexes)) if 0 <= index < len(children)]
        if not items:
            self.update_selection_status()
            return
        self.tree.selection_set(items)
        self.tree.focus(items[0])
        self.tree.see(items[0])
        self.update_selection_status()

    def update_selection_status(self) -> None:
        indexes = self.selected_config_indexes()
        if not indexes:
            self.selection_status.set("请先选择配置，再预览或写入。")
        elif len(indexes) == 1:
            self.selection_status.set(f"已选择：{self.configs[indexes[0]].name}")
        else:
            self.selection_status.set(f"已选择 {len(indexes)} 项配置。")

    def refresh_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for config in self.configs:
            first = config.regions[0] if config.regions else None
            summary = ""
            if first:
                try:
                    summary = (
                        f"{first.source_sheet}!{range_from_cells(first.source_start, first.source_end)} -> "
                        f"{first.target_sheet}!{range_from_cells(first.target_start, first.target_end)}"
                    )
                except Exception:
                    summary = (
                        f"{first.source_sheet}!{first.source_start}:{first.source_end} -> "
                        f"{first.target_sheet}!{first.target_start}:{first.target_end}"
                    )
            self.tree.insert(
                "",
                "end",
                values=(
                    config.name,
                    "金山文档" if config.source_type == "kdocs" else "本地表格",
                    config.source_url if config.source_type == "kdocs" else config.local_file,
                    config.kdocs_url,
                    len(config.regions),
                    summary,
                ),
            )
        self.update_selection_status()

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def start_wps_browser(self, initial_url: str | None = None) -> None:
        self.browser_mode.set("managed_cdp")
        self.save_current_config()
        self._run_browser_action(start=True, initial_url=initial_url)

    def check_wps_browser(self, *, quiet: bool = False) -> None:
        self._run_browser_action(start=False, quiet=quiet)

    def _invalidate_login_status(self, *_args) -> None:
        self.login_status.set("尚未检查")
        self.login_status_label.configure(foreground="#666666")

    def _schedule_login_check(self, event) -> None:
        if event.widget is self and self._login_poll_after_id is None:
            # The toolbox also constructs this page during hidden warmup.
            # Check only once it is visible, without delaying page creation.
            self._login_poll_after_id = self.after(750, self._poll_login_status)

    def _poll_login_status(self) -> None:
        self._login_poll_after_id = None
        if not self.winfo_ismapped():
            return
        if not (self.worker and self.worker.is_alive()):
            self.check_wps_browser(quiet=True)
        self._login_poll_after_id = self.after(LOGIN_STATUS_POLL_MS, self._poll_login_status)

    def _run_browser_action(
        self, *, start: bool, initial_url: str | None = None, quiet: bool = False
    ) -> None:
        if self.browser_worker and self.browser_worker.is_alive():
            return
        self.start_browser_button.configure(state="disabled")
        self.check_browser_button.configure(state="disabled")
        if not quiet:
            self.browser_status.set("正在启动…" if start else "正在检查…")
        if not quiet or self.login_status.get() == "尚未检查":
            self.login_status.set("正在检查…")
        cdp_url = self.cdp_url.get().strip()
        browser_mode = self.browser_mode.get()

        def worker() -> None:
            try:
                if start:
                    result = ensure_wps_dedicated_chrome(
                        cdp_url,
                        self.log,
                        initial_url=initial_url or "https://www.kdocs.cn/latest",
                        preload_url=True,
                    )
                    action = "已启动" if result.get("started") else "已连接"
                    message = f"专用浏览器{action}"
                else:
                    message = "登录状态检查完成"
                login = check_wps_login_status(
                    browser_mode, cdp_url, (lambda _message: None) if quiet else self.log
                )
                self.login_event_queue.put((browser_mode, cdp_url, login))
                self.browser_event_queue.put((True, message, quiet))
            except Exception as exc:
                self.login_event_queue.put((browser_mode, cdp_url, WpsLoginStatus(
                    "unknown", "暂时无法确认，请检查网络或浏览器后刷新登录状态。"
                )))
                self.browser_event_queue.put((False, str(exc), quiet))

        self.browser_worker = self._start_tracked_thread(
            worker,
            name="wps-browser-start" if start else "wps-browser-check",
        )

    def _start_tracked_thread(
        self,
        target,
        *,
        args: tuple[Any, ...] = (),
        name: str,
    ) -> threading.Thread:
        thread: threading.Thread

        def tracked_target() -> None:
            try:
                target(*args)
            finally:
                if self.task_registry is not None:
                    self.task_registry.unregister_thread(thread)

        thread = threading.Thread(target=tracked_target, name=name, daemon=True)
        if self.task_registry is not None:
            self.task_registry.register_thread(thread)
        try:
            thread.start()
        except BaseException:
            if self.task_registry is not None:
                self.task_registry.unregister_thread(thread)
            raise
        return thread

    def detail_log(self, message: str) -> None:
        if "Traceback (most recent call last)" in message:
            self.log_queue.put(message)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            log_path = LOG_DIR / f"wps_write_{now.strftime('%Y-%m-%d')}.log"
            line = redact(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as exc:
            self.log_queue.put(f"详细日志未能保存：{exc}；仍可复制本次运行日志。")

    def detail_log_prepared(self, action: str, prepared: list[PreparedMapping]) -> None:
        self.detail_log(f"{action}: {len(prepared)} region(s)")
        for item in prepared:
            sample = item.values[:3]
            self.detail_log(
                "REGION "
                f"index={item.index}; source={item.workbook_path}; "
                f"source_range={item.source_sheet}!{item.source_range}; "
                f"target={item.kdocs_url}; target_range={item.target_sheet}!{item.target_range}; "
                f"shape={item.rows}x{item.cols}; sample_first3={sample!r}"
            )

    def drain_logs(self) -> None:
        while True:
            try:
                browser_mode, cdp_url, login = self.login_event_queue.get_nowait()
            except queue.Empty:
                break
            if (browser_mode, cdp_url) != (self.browser_mode.get(), self.cdp_url.get().strip()):
                continue
            if self.login_status.get() != login.message:
                self.log("WPS 登录状态：" + login.message)
            self.login_status.set(login.message)
            self.login_status_label.configure(foreground={
                "logged_in": "#15803d", "logged_out": "#b45309", "unknown": "#666666",
            }[login.state])
        while True:
            try:
                success, status, quiet = self.browser_event_queue.get_nowait()
            except queue.Empty:
                break
            self.start_browser_button.configure(state="normal")
            self.check_browser_button.configure(state="normal")
            self.browser_status.set(status if success else "检查失败，请重试")
            if not quiet:
                self.log(status if success else "检查失败：" + status)
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_console.append(message)
            if any(marker in message for marker in ("操作失败：", "预览失败：", "暂时无法写入：", "检查失败：", "回读数据与来源不一致")):
                diagnosis = explain_error(message) or (message, "点击“复制错误”提供完整日志；重试前核对是否已有部分写入。")
                self.log_console.finish("执行失败", *diagnosis)
            elif "Traceback (most recent call last)" in message:
                diagnosis = explain_error(message)
                if diagnosis:
                    self.log_console.finish("执行失败", *diagnosis)
            elif "写入完成：" in message or "预览通过：" in message:
                self.log_console.finish("已完成")
        self.schedule_drain_logs()

    def schedule_drain_logs(self) -> None:
        try:
            self._drain_after_id = self.after(100, self.drain_logs)
        except tk.TclError:
            self._drain_after_id = None

    def destroy(self) -> None:
        if self._login_poll_after_id is not None:
            try:
                self.after_cancel(self._login_poll_after_id)
            except tk.TclError:
                pass
            self._login_poll_after_id = None
        if self._drain_after_id is not None:
            try:
                self.after_cancel(self._drain_after_id)
            except tk.TclError:
                pass
            self._drain_after_id = None
        super().destroy()

    def clear_log(self) -> None:
        # Finish persisting pending messages before starting a separate run.
        while True:
            try:
                self.log_console.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        self.log_console.begin()

    def save_log(self) -> None:
        # Compatibility for older callers: logs are now copied, never exported.
        self.log_console.copy_error()

    def load_initial_config(self) -> None:
        config = load_config()
        self.recent_urls = [url for url in config.get("recent_urls", []) if isinstance(url, str) and url.strip()]
        if config.get("kdocs_url"):
            self.recent_urls = update_recent_urls(self.recent_urls, config["kdocs_url"])
        saved_browser_mode = config.get("browser_mode", "managed_cdp")
        if saved_browser_mode == "persistent" and sys.platform == "darwin":
            saved_browser_mode = "managed_cdp"
        self.browser_mode.set(saved_browser_mode)
        self.cdp_url.set(config.get("cdp_url", "http://127.0.0.1:9222"))
        default_url = config.get("kdocs_url", "")
        default_file = config.get("excel_path", "")
        if isinstance(config.get("configs"), list):
            self.configs = [self.document_config_from_config(row) for row in config.get("configs", []) if isinstance(row, dict)]
        else:
            mappings = [
                self.mapping_from_config(row, default_url=default_url, default_file=default_file)
                for row in config.get("mappings", [])
                if isinstance(row, dict)
            ]
            self.configs = self.group_mappings_into_configs(mappings)
        self.remember_mapping_urls()
        self.refresh_tree()

    def document_config_from_config(self, row: dict[str, Any]) -> DocumentConfig:
        regions = []
        for region in row.get("regions", []):
            if not isinstance(region, dict):
                continue
            regions.append(
                RegionRow(
                    source_sheet=region.get("source_sheet", ""),
                    source_start=region.get("source_start", ""),
                    source_end=region.get("source_end", ""),
                    target_sheet=region.get("target_sheet", ""),
                    target_start=region.get("target_start", ""),
                    target_end=region.get("target_end", ""),
                )
            )
        return DocumentConfig(
            name=row.get("name", "") or Path(row.get("local_file", "")).stem or "未命名配置",
            kdocs_url=row.get("kdocs_url", ""),
            local_file=row.get("local_file", ""),
            regions=regions,
            source_type=row.get("source_type", "excel"),
            source_url=row.get("source_url", ""),
        )

    def mapping_from_config(self, row: dict[str, Any], default_url: str = "", default_file: str = "") -> MappingRow:
        if "source_range" in row and ("source_start" not in row or "source_end" not in row):
            source_start, source_end = split_range(row.get("source_range", ""))
        else:
            source_start = row.get("source_start", "")
            source_end = row.get("source_end", "")

        target_start = row.get("target_start", "")
        target_end = row.get("target_end", "")
        if not target_end and target_start and "source_range" in row:
            source_start_for_shape, source_end_for_shape = split_range(row.get("source_range", ""))
            rows, cols = range_shape(source_start_for_shape, source_end_for_shape)
            target_range = target_range_from_start(target_start, rows, cols)
            _target_start, target_end = split_range(target_range)

        return MappingRow(
            kdocs_url=row.get("kdocs_url", "") or default_url,
            local_file=row.get("local_file", "") or default_file,
            source_sheet=row.get("source_sheet", ""),
            source_start=source_start,
            source_end=source_end,
            target_sheet=row.get("target_sheet", ""),
            target_start=target_start,
            target_end=target_end,
            source_type=row.get("source_type", "excel"),
            source_url=row.get("source_url", ""),
        )

    def group_mappings_into_configs(self, mappings: list[MappingRow]) -> list[DocumentConfig]:
        grouped: dict[tuple[str, str], list[RegionRow]] = {}
        for mapping in mappings:
            key = (mapping.kdocs_url, mapping.local_file)
            grouped.setdefault(key, []).append(
                RegionRow(
                    source_sheet=mapping.source_sheet,
                    source_start=mapping.source_start,
                    source_end=mapping.source_end,
                    target_sheet=mapping.target_sheet,
                    target_start=mapping.target_start,
                    target_end=mapping.target_end,
                )
            )
        configs = []
        for index, ((kdocs_url, local_file), regions) in enumerate(grouped.items(), start=1):
            name = Path(local_file).stem or f"配置 {index}"
            configs.append(DocumentConfig(name=name, kdocs_url=kdocs_url, local_file=local_file, regions=regions))
        return configs

    def remember_mapping_urls(self) -> None:
        urls = self.recent_urls
        for config in self.configs:
            urls = update_recent_urls(urls, config.kdocs_url)
            if config.source_type == "kdocs":
                urls = update_recent_urls(urls, config.source_url)
        self.recent_urls = urls

    def save_current_config(self) -> None:
        self.remember_mapping_urls()
        save_config(self.current_runtime_config())

    def current_runtime_config(self) -> dict[str, Any]:
        return normalize_runtime_config(
            {
                "recent_urls": self.recent_urls,
                "browser_mode": self.browser_mode.get(),
                "cdp_url": self.cdp_url.get(),
                "configs": [
                    {
                        "name": config.name,
                        "source_type": config.source_type,
                        "source_url": config.source_url,
                        "kdocs_url": config.kdocs_url,
                        "local_file": config.local_file,
                        "regions": [region.__dict__ for region in config.regions],
                    }
                    for config in self.configs
                ],
            }
        )

    def apply_runtime_config(self, config: dict[str, Any]) -> None:
        runtime_config = normalize_runtime_config(config)
        self.recent_urls = runtime_config["recent_urls"]
        self.browser_mode.set(runtime_config["browser_mode"])
        self.cdp_url.set(runtime_config["cdp_url"])
        self.configs = [self.document_config_from_config(row) for row in runtime_config["configs"]]
        self.remember_mapping_urls()
        self.refresh_tree()
        self.save_current_config()

    def import_config(self) -> None:
        path_text = filedialog.askopenfilename(
            parent=self,
            title="导入写入配置",
            filetypes=[("配置文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path_text:
            return
        try:
            runtime_config = load_config_export(Path(path_text))
            self.apply_runtime_config(runtime_config)
        except Exception as exc:
            messagebox.showerror("导入配置失败", str(exc), parent=self)
            self.log(f"导入配置失败：{exc}")
            return
        self.log(f"已导入配置：{Path(path_text).name}")
        messagebox.showinfo("导入配置", "配置已导入并保存。", parent=self)

    def export_config(self) -> None:
        default_name = f"写入配置_{datetime.now().strftime('%Y%m%d')}.json"
        path_text = filedialog.asksaveasfilename(
            parent=self,
            title="导出写入配置",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("配置文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path_text:
            return
        try:
            save_config_export(Path(path_text), self.current_runtime_config())
        except Exception as exc:
            messagebox.showerror("导出配置失败", str(exc), parent=self)
            self.log(f"导出配置失败：{exc}")
            return
        self.log(f"配置已导出至：{path_text}")
        messagebox.showinfo("导出配置", "配置已导出。", parent=self)

    def selected_config_indexes(self) -> list[int]:
        selected = self.tree.selection()
        return sorted(self.tree.index(item) for item in selected)

    def flatten_configs(self, configs: list[DocumentConfig] | None = None) -> list[MappingRow]:
        mappings: list[MappingRow] = []
        for config in (configs if configs is not None else self.configs):
            for region in config.regions:
                mappings.append(
                    MappingRow(
                        kdocs_url=config.kdocs_url,
                        local_file=config.local_file,
                        source_sheet=region.source_sheet,
                        source_start=region.source_start,
                        source_end=region.source_end,
                        target_sheet=region.target_sheet,
                        target_start=region.target_start,
                        target_end=region.target_end,
                        source_type=config.source_type,
                        source_url=config.source_url,
                    )
                )
        return mappings

    def prepare_selected(self) -> list[PreparedMapping]:
        indexes = self.selected_config_indexes()
        if not indexes:
            raise ValueError("请先选择至少一项配置，再预览或写入。")
        configs = [self.configs[index] for index in indexes]
        return prepare_mappings(
            self.flatten_configs(configs),
            browser_mode=self.browser_mode.get(),
            cdp_url=self.cdp_url.get().strip(),
            log=lambda _message: None,
        )

    def preview(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在写入", "写入任务正在进行，请等待完成。", parent=self)
            return
        self.clear_log()
        self.log_console.status.set("正在预览…")
        try:
            prepared = self.prepare_selected()
        except Exception as exc:
            diagnosis = explain_error(type(exc).__name__ + ": " + str(exc))
            messagebox.showerror("预览失败", "\n\n".join(diagnosis) if diagnosis else str(exc), parent=self)
            self.log(f"预览失败：{exc}")
            self.detail_log(f"PREVIEW ERROR: {exc}")
            self.detail_log(traceback.format_exc())
            return
        urls = len({item.kdocs_url for item in prepared})
        self.detail_log_prepared("PREVIEW OK", prepared)
        self.log(f"预览通过：共 {len(prepared)} 个写入区域，涉及 {urls} 份目标表格。")

    def write(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在写入", "写入任务正在进行，请等待完成。", parent=self)
            return
        self.clear_log()
        self.log_console.status.set("正在检查写入配置…")
        try:
            prepared = self.prepare_selected()
        except Exception as exc:
            diagnosis = explain_error(type(exc).__name__ + ": " + str(exc))
            messagebox.showerror("暂时无法写入", "\n\n".join(diagnosis) if diagnosis else str(exc), parent=self)
            self.log(f"暂时无法写入：{exc}")
            self.detail_log(f"WRITE BLOCKED: {exc}")
            self.detail_log(traceback.format_exc())
            return

        self.log_console.status.set("正在写入…")
        self.save_current_config()
        self.detail_log_prepared("WRITE PREPARED", prepared)
        self.worker = self._start_tracked_thread(
            self.write_worker,
            args=(prepared, self.browser_mode.get(), self.cdp_url.get().strip()),
            name="wps-write",
        )

    def write_worker(self, prepared: list[PreparedMapping], browser_mode: str, cdp_url: str) -> None:
        try:
            self.log(f"开始写入，共 {len(prepared)} 个区域。")
            self.detail_log(f"WRITE STARTED: browser_mode={browser_mode}; cdp_url={cdp_url}; regions={len(prepared)}")
            by_index = {item.index: item for item in prepared}
            all_ok = True

            groups: dict[str, list[PreparedMapping]] = {}
            for item in prepared:
                groups.setdefault(item.kdocs_url, []).append(item)

            for url, group in groups.items():
                self.detail_log(f"WRITE TARGET: url={url}; regions={len(group)}")
                result = write_to_wps(url, group, self.log, browser_mode=browser_mode, cdp_url=cdp_url)
                self.detail_log(f"WRITE TARGET RESULT: url={url}; saved={result.get('saved')}; returned={len(result.get('results', []))}")
                for entry in result.get("results", []):
                    item = by_index[entry["index"]]
                    mismatches = compare_matrix(item.values, entry.get("value"), item.rows, item.cols)
                    if mismatches:
                        all_ok = False
                        self.detail_log(
                            f"VERIFY MISMATCH: index={item.index}; target={item.target_sheet}!{item.target_range}; "
                            f"mismatches={mismatches!r}; read_value={entry.get('value')!r}"
                        )
                        self.log(f"第 {item.index} 个区域回读不一致：{item.target_sheet}!{item.target_range}")
                        for mismatch in mismatches:
                            self.log(f"  {mismatch}")
                    else:
                        self.detail_log(f"VERIFY OK: index={item.index}; target={item.target_sheet}!{item.target_range}")
            if all_ok:
                self.detail_log(f"WRITE OK: regions={len(prepared)}")
                self.log(f"写入完成：{len(prepared)} 个区域已保存，回读核对一致。")
            else:
                self.detail_log("WRITE WARNING: verification mismatches detected")
                self.log("写入已结束，但回读数据与来源不一致，请查看上方差异。")
        except Exception as exc:
            self.log(f"操作失败：{exc}")
            self.log("详细错误信息已保存到日志目录。")
            self.detail_log(f"WRITE ERROR: {exc}")
            self.detail_log(traceback.format_exc())


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("WPS 写入工具")
        self.geometry("1120x760")
        self.minsize(980, 640)
        self.writer_frame = WpsWriterFrame(self)
        self.writer_frame.pack(fill="both", expand=True)
        center_window(self, 1120, 760)


if __name__ == "__main__":
    if "--launch-browser" in sys.argv:
        launch_result = ensure_wps_dedicated_chrome(
            "http://127.0.0.1:9222", print
        )
        print(
            "WPS 专用浏览器已就绪"
            f"（{'已启动' if launch_result.get('started') else '已连接'}）。"
        )
    else:
        app = App()
        app.mainloop()
