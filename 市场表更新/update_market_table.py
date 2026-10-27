#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles.numbers import is_date_format
from openpyxl.utils import get_column_letter
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
from toolbox_shared.auth_loader import load_login_module
from toolbox_shared.excel_read import read_cell_snapshots
from toolbox_shared.workbook_commit import assert_workbook_unchanged, workbook_commit_lock, workbook_fingerprint

ONLINE_SCRIPTS_DIR = WORKSPACE / "上网电量抓取"
REPORT_SCRIPTS_DIR = WORKSPACE / "每日生产经营情况汇报自动生成工具" / "scripts"
if str(ONLINE_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(ONLINE_SCRIPTS_DIR))
if str(REPORT_SCRIPTS_DIR) not in sys.path:
    sys.path.append(str(REPORT_SCRIPTS_DIR))

online_export_module = load_login_module(
    WORKSPACE,
    module_name="export_online_energy",
    state_dir=SCRIPT_DIR,
)
AuthStateLock = online_export_module.AuthStateLock
ensure_login = online_export_module.ensure_login
interactive_login = online_export_module.interactive_login
launch_context = online_export_module.launch_context
load_config = online_export_module.load_config
validate_date = online_export_module.validate_date

from fetch_load_compare import select_load_type  # noqa: E402


from toolbox_shared.market_contracts import (
    MARKET_SHEET_NAME,
    COST_SHEET_NAME,
    DAY_AHEAD_LOAD_START_COLUMN,
    DAY_AHEAD_LOAD_END_COLUMN,
    DAY_AHEAD_MARKET_START_COLUMN,
    DAY_AHEAD_MARKET_END_COLUMN,
    ACTUAL_LOAD_START_COLUMN,
    ACTUAL_LOAD_END_COLUMN,
    REALTIME_MARKET_START_COLUMN,
    REALTIME_MARKET_END_COLUMN,
    GENERATION_SETTLEMENT_START_COLUMN,
    GENERATION_SETTLEMENT_END_COLUMN,
    USER_SETTLEMENT_START_COLUMN,
    USER_SETTLEMENT_END_COLUMN,
    UNIT_COST_START_COLUMN,
    UNIT_COST_END_COLUMN,
    OPERATION_MODE_START_COLUMN,
    OPERATION_MODE_END_COLUMN,
    MARKET_BOARD_ONLINE_UNIT_COLUMNS,
    SPOT_CLEARING_UNIT_COUNT_START_COLUMN,
    SPOT_CLEARING_UNIT_COUNT_END_COLUMN,
    DAY_AHEAD_CLEARING_UNIT_COUNT_GROUPS,
    ACTUAL_LOAD_TYPES,
    DEFAULT_POINT,
    DEFAULT_PROVINCE_AREA_ID,
    UNIT_COST_ORG_IDS,
    UNIT_COST_PRICE_UNIT_IDS,
    DAY_AHEAD_MARKET_FIELDS,
    REALTIME_MARKET_FIELDS,
    GENERATION_SETTLEMENT_FIELDS,
    GENERATION_SETTLEMENT_PERCENT_FIELDS,
    USER_SETTLEMENT_FIELDS,
    USER_SETTLEMENT_PERCENT_FIELDS,
    MARKET_SERIES,
)


def log(message: str) -> None:
    print(message, flush=True)


def parse_date(value: str) -> date:
    return datetime.strptime(validate_date(value), "%Y-%m-%d").date()


def parse_cell_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def date_matches(value: Any, target: date) -> bool:
    parsed = parse_cell_date(value)
    return parsed == target


def find_date_row(sheet, target: date) -> int:
    resolved_dates: dict[int, date] = {}
    formula_pattern = re.compile(r"^=\$?A\$?(\d+)\s*([+-])\s*(\d+)$", re.IGNORECASE)

    for row in range(1, sheet.max_row + 1):
        value = sheet.cell(row=row, column=1).value
        parsed = parse_cell_date(value)
        if parsed is None and isinstance(value, str):
            match = formula_pattern.match(value.strip())
            if match:
                ref_row = int(match.group(1))
                delta = int(match.group(3))
                if match.group(2) == "-":
                    delta = -delta
                ref_date = resolved_dates.get(ref_row)
                if ref_date is not None:
                    parsed = ref_date + timedelta(days=delta)
        if parsed is not None:
            resolved_dates[row] = parsed
        if parsed == target:
            return row
    raise RuntimeError(f"{sheet.title} 未找到日期行：{target.isoformat()}")


def workbook_permission_error(path: Path) -> RuntimeError:
    return RuntimeError(
        "无法打开 Excel 文件，macOS 拒绝了当前程序访问该文件。"
        f"请关闭正在占用的 Excel/WPS，并在“系统设置 > 隐私与安全性 > 文件和文件夹”或"
        f"“完全磁盘访问权限”中允许启动工具箱的程序访问桌面/文稿，或把文件移到当前程序可访问的目录后重试：{path}"
    )


def load_workbook_checked(workbook_path: Path | str, **kwargs):
    path = Path(workbook_path).expanduser()
    try:
        return openpyxl.load_workbook(path, **kwargs)
    except PermissionError as exc:
        raise workbook_permission_error(path) from exc


class WorkbookWriteSession:
    """Keep one workbook open for a complete market-table update."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.workbook = None
        self.source_fingerprint = None
        self.date_rows: dict[tuple[str, date], int] = {}
        self.expected_cells: dict[tuple[str, str], tuple[Any, str]] = {}

    def get_workbook(self):
        if self.workbook is None:
            self.source_fingerprint = workbook_fingerprint(self.path)
            self.workbook = load_workbook_checked(self.path)
            assert_workbook_unchanged(self.path, self.source_fingerprint)
        return self.workbook

    def record(self, cell) -> None:
        self.expected_cells[(cell.parent.title, cell.coordinate)] = (
            cell.value,
            cell.number_format,
        )

    def commit(self) -> None:
        if self.workbook is None or not self.expected_cells:
            return
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{self.path.stem}.", suffix=".xlsx", dir=self.path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            self.workbook.save(temporary_path)
            os.chmod(temporary_path, stat.S_IMODE(self.path.stat().st_mode))
            saved = load_workbook_checked(temporary_path, read_only=True, data_only=False)
            try:
                actual_cells = read_cell_snapshots(saved, self.expected_cells)
                for (sheet_name, coordinate), expected in self.expected_cells.items():
                    actual = actual_cells[(sheet_name, coordinate)]
                    if actual[0] != expected[0] or (
                        expected[0] is not None and actual[1] != expected[1]
                    ):
                        raise RuntimeError(
                            f"市场表保存后回读不一致：{sheet_name}!{coordinate}"
                        )
            finally:
                saved.close()
            with workbook_commit_lock((self.path,)):
                assert_workbook_unchanged(self.path, self.source_fingerprint)
                os.replace(temporary_path, self.path)
        except PermissionError as exc:
            raise workbook_permission_error(self.path) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


_active_write_session: ContextVar[WorkbookWriteSession | None] = ContextVar(
    "market_table_write_session", default=None
)


@contextmanager
def workbook_write_session(path: Path | str):
    session = WorkbookWriteSession(path)
    token = _active_write_session.set(session)
    try:
        yield session
        session.commit()
    finally:
        _active_write_session.reset(token)
        if session.workbook is not None:
            session.workbook.close()


def active_session_for(path: Path | str) -> WorkbookWriteSession | None:
    session = _active_write_session.get()
    if session is not None and session.path == Path(path).expanduser().resolve():
        return session
    return None


def workbook_for_write(path: Path):
    session = active_session_for(path)
    return session.get_workbook() if session is not None else load_workbook_checked(path)


def record_written_cell(path: Path, cell) -> None:
    session = active_session_for(path)
    if session is not None:
        session.record(cell)


def save_written_workbook(path: Path, workbook) -> None:
    if active_session_for(path) is None:
        workbook.save(path)


def ensure_workbook_accessible(workbook_path: Path | str) -> None:
    workbook = load_workbook_checked(workbook_path, read_only=True, data_only=False)
    workbook.close()


def find_date_row_in_workbook(
    workbook_path: Path,
    target: date,
    sheet_name: str = MARKET_SHEET_NAME,
) -> int:
    session = active_session_for(workbook_path)
    if session is not None:
        workbook = session.get_workbook()
        if sheet_name not in workbook.sheetnames:
            raise RuntimeError(f"Excel 中未找到工作表：{sheet_name}")
        key = (sheet_name, target)
        if key not in session.date_rows:
            session.date_rows[key] = find_date_row(workbook[sheet_name], target)
        return session.date_rows[key]
    workbook = load_workbook_checked(workbook_path, read_only=True, data_only=False)
    try:
        if sheet_name not in workbook.sheetnames:
            raise RuntimeError(f"Excel 中未找到工作表：{sheet_name}")
        return find_date_row(workbook[sheet_name], target)
    finally:
        workbook.close()


def actual_load_range(row: int) -> str:
    return f"{ACTUAL_LOAD_START_COLUMN}{row}:{ACTUAL_LOAD_END_COLUMN}{row}"


def day_ahead_load_range(row: int) -> str:
    return f"{DAY_AHEAD_LOAD_START_COLUMN}{row}:{DAY_AHEAD_LOAD_END_COLUMN}{row}"


def day_ahead_market_range(row: int) -> str:
    return f"{DAY_AHEAD_MARKET_START_COLUMN}{row}:{DAY_AHEAD_MARKET_END_COLUMN}{row}"


def realtime_market_range(row: int) -> str:
    return f"{REALTIME_MARKET_START_COLUMN}{row}:{REALTIME_MARKET_END_COLUMN}{row}"


def generation_settlement_range(row: int) -> str:
    return f"{GENERATION_SETTLEMENT_START_COLUMN}{row}:{GENERATION_SETTLEMENT_END_COLUMN}{row}"


def user_settlement_range(row: int) -> str:
    return f"{USER_SETTLEMENT_START_COLUMN}{row}:{USER_SETTLEMENT_END_COLUMN}{row}"


def unit_cost_price_range(row: int) -> str:
    return f"{UNIT_COST_START_COLUMN}{row}:{UNIT_COST_END_COLUMN}{row}"


def operation_mode_range(row: int) -> str:
    return f"{OPERATION_MODE_START_COLUMN}{row}:{OPERATION_MODE_END_COLUMN}{row}"


def actual_load_source_date(base_date: date) -> date:
    return MARKET_SERIES["actual_load"].source_date(base_date)


def day_ahead_market_source_date(base_date: date) -> date:
    return MARKET_SERIES["day_ahead_market"].source_date(base_date)


def realtime_market_source_date(base_date: date) -> date:
    return MARKET_SERIES["realtime_market"].source_date(base_date)


def generation_settlement_source_date(base_date: date) -> date:
    return MARKET_SERIES["generation_settlement"].source_date(base_date)


def user_settlement_source_date(base_date: date) -> date:
    return MARKET_SERIES["user_settlement"].source_date(base_date)


def normalize_number(value: float) -> int | float:
    rounded = round(float(value), 4)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def normalize_report_number(value: Any, *, convert_percent: bool = False) -> int | float:
    if convert_percent:
        if isinstance(value, str):
            text = value.strip()
            if text.endswith("%"):
                value = text[:-1].strip()
        return normalize_number(float(value) / 100)
    if isinstance(value, str):
        text = value.strip()
    return normalize_number(value)


def find_style_source_row(sheet, run_date: date, target_row: int) -> int | None:
    if run_date.day != 1:
        return target_row - 1 if target_row > 1 else None
    # 月初前一行可能是月份汇总行，必须按日期查找上月最后一天。
    previous_date = run_date - timedelta(days=1)
    try:
        return find_date_row(sheet, previous_date)
    except RuntimeError:
        log(f"{sheet.title} 未找到 {previous_date.isoformat()} 的格式来源，保留目标单元格原格式。")
        return None


def copy_cell_style(source, target) -> None:
    if source.has_style:
        target._style = copy(source._style)
    if source.number_format:
        target.number_format = source.number_format
    if source.font:
        target.font = copy(source.font)
    if source.fill:
        target.fill = copy(source.fill)
    if source.border:
        target.border = copy(source.border)
    if source.alignment:
        target.alignment = copy(source.alignment)
    if source.protection:
        target.protection = copy(source.protection)


def copy_numeric_cell_style(source, target) -> None:
    original_number_format = target.number_format
    copy_cell_style(source, target)
    if is_date_format(target.number_format):
        target.number_format = (
            original_number_format
            if not is_date_format(original_number_format)
            else "General"
        )


def cell_is_non_empty(cell) -> bool:
    return cell.value not in (None, "")


def write_count_text(summary: dict[str, Any]) -> str:
    count = summary.get("count")
    if count is None:
        return "未知个数"
    skipped = summary.get("skipped", 0)
    if skipped:
        return f"{count} 个数，跳过 {skipped} 个非空单元格"
    return f"{count} 个数"


def summarize_load_type(
    dataset: dict[str, Any],
    load_type: str,
    run_date: date,
    period_list_key: str,
    label: str,
) -> list[int | float]:
    series = select_load_type(dataset, load_type)
    periods = series.get(period_list_key) or []
    if not periods:
        raise RuntimeError(
            f"{run_date.isoformat()} loadType={load_type} 没有返回{label}数据"
        )
    summary = periods[0]
    fields = ("max", "min", "avg", "sum")
    if any(summary.get(field) in (None, "") for field in fields):
        raise RuntimeError(
            f"{run_date.isoformat()} loadType={load_type} 没有返回{label}数据"
        )
    return [
        normalize_number(summary["max"]),
        normalize_number(summary["min"]),
        normalize_number(summary["avg"]),
        normalize_number(summary["sum"]),
    ]


def summarize_actual_load_type(
    dataset: dict[str, Any],
    load_type: str,
    run_date: date,
) -> list[int | float]:
    return summarize_load_type(
        dataset,
        load_type,
        run_date,
        "actualPeriodList",
        "实际运行",
    )


def summarize_day_ahead_load_type(
    dataset: dict[str, Any],
    load_type: str,
    run_date: date,
) -> list[int | float]:
    return summarize_load_type(
        dataset,
        load_type,
        run_date,
        "forecastPeriodList",
        "日前",
    )


def extract_actual_load_values(
    dataset: dict[str, Any],
    run_date: date,
) -> list[int | float]:
    values: list[int | float] = []
    for load_type in ACTUAL_LOAD_TYPES:
        values.extend(summarize_actual_load_type(dataset, load_type, run_date))
    if len(values) != 20:
        raise RuntimeError(f"市场负荷信息应为 20 个数，实际得到 {len(values)} 个")
    return values


def extract_day_ahead_load_values(
    dataset: dict[str, Any],
    run_date: date,
) -> list[int | float]:
    values: list[int | float] = []
    for load_type in ACTUAL_LOAD_TYPES:
        values.extend(summarize_day_ahead_load_type(dataset, load_type, run_date))
    if len(values) != 20:
        raise RuntimeError(f"日前市场负荷信息应为 20 个数，实际得到 {len(values)} 个")
    return values


def fetch_actual_load_values(
    context,
    run_date: date,
    *,
    province_area_id: str = DEFAULT_PROVINCE_AREA_ID,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdfire/api/data/net/load",
        params={
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
            "provinceAreaId": province_area_id,
            "timeSegment": 2,
        },
        timeout=30000,
    )
    dataset = online_export_module.parse_json_response(
        response,
        f"读取市场负荷信息 {run_date.isoformat()}",
    )
    return extract_actual_load_values(dataset, run_date)


def fetch_day_ahead_load_values(
    context,
    run_date: date,
    *,
    province_area_id: str = DEFAULT_PROVINCE_AREA_ID,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdfire/api/data/net/load",
        params={
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
            "type": 1,
            "provinceAreaId": province_area_id,
            "timeSegment": 2,
        },
        timeout=30000,
    )
    dataset = online_export_module.parse_json_response(
        response,
        f"读取日前市场负荷信息 {run_date.isoformat()}",
    )
    return extract_day_ahead_load_values(dataset, run_date)


def extract_day_ahead_market_values(
    payload: list[dict[str, Any]],
    run_date: date,
) -> list[int | float]:
    return extract_report_market_values(
        payload,
        run_date,
        fields=DAY_AHEAD_MARKET_FIELDS,
        expected_count=12,
        label="日前市场",
    )


def extract_realtime_market_values(
    payload: list[dict[str, Any]],
    run_date: date,
) -> list[int | float]:
    return extract_report_market_values(
        payload,
        run_date,
        fields=REALTIME_MARKET_FIELDS,
        expected_count=4,
        label="实时市场",
    )


def extract_generation_settlement_values(
    payload: list[dict[str, Any]],
    run_date: date,
) -> list[int | float]:
    return extract_report_market_values(
        payload,
        run_date,
        fields=GENERATION_SETTLEMENT_FIELDS,
        expected_count=12,
        label="发电侧结算",
        percent_fields=GENERATION_SETTLEMENT_PERCENT_FIELDS,
    )


def extract_user_settlement_values(
    payload: list[dict[str, Any]],
    run_date: date,
) -> list[int | float]:
    return extract_report_market_values(
        payload,
        run_date,
        fields=USER_SETTLEMENT_FIELDS,
        expected_count=7,
        label="用电侧结算",
        percent_fields=USER_SETTLEMENT_PERCENT_FIELDS,
    )


def extract_report_market_values(
    payload: list[dict[str, Any]],
    run_date: date,
    *,
    fields: tuple[Any, ...],
    expected_count: int,
    label: str,
    percent_fields: set[str] | None = None,
) -> list[int | float]:
    rows = [row for row in payload if date_matches(row.get("date"), run_date)]
    if not rows:
        raise RuntimeError(f"{label}没有返回日期 {run_date.isoformat()} 的数据")

    info = rows[0].get("info") or {}
    values: list[int | float] = []
    percent_fields = percent_fields or set()
    for field in fields:
        field_names = field if isinstance(field, tuple) else (field,)
        value = next(
            (
                info.get(field_name)
                for field_name in field_names
                if info.get(field_name) not in (None, "")
            ),
            None,
        )
        if value in (None, ""):
            raise RuntimeError(f"{label}缺少字段：{'/'.join(field_names)}")
        values.append(
            normalize_report_number(
                value,
                convert_percent=any(field_name in percent_fields for field_name in field_names),
            )
        )

    if len(values) != expected_count:
        raise RuntimeError(f"{label}应为 {expected_count} 个数，实际得到 {len(values)} 个")
    return values


def fetch_day_ahead_market_values(
    context,
    run_date: date,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdfire/api/pub/data/report",
        params={
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
            "type": 1,
        },
        timeout=30000,
    )
    payload = online_export_module.parse_json_response(
        response,
        f"读取日前市场 {run_date.isoformat()}",
    )
    return extract_day_ahead_market_values(payload.get("data") or [], run_date)


def fetch_realtime_market_values(
    context,
    run_date: date,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdfire/api/pub/data/report",
        params={
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
            "type": 2,
        },
        timeout=30000,
    )
    payload = online_export_module.parse_json_response(
        response,
        f"读取实时市场 {run_date.isoformat()}",
    )
    return extract_realtime_market_values(payload.get("data") or [], run_date)


def fetch_generation_settlement_values(
    context,
    run_date: date,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdfire/api/pub/data/report",
        params={
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
            "type": 3,
        },
        timeout=30000,
    )
    payload = online_export_module.parse_json_response(
        response,
        f"读取发电侧结算 {run_date.isoformat()}",
    )
    return extract_generation_settlement_values(payload.get("data") or [], run_date)


def fetch_user_settlement_values(
    context,
    run_date: date,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdfire/api/pub/data/report",
        params={
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
            "type": 4,
        },
        timeout=30000,
    )
    payload = online_export_module.parse_json_response(
        response,
        f"读取用电侧结算 {run_date.isoformat()}",
    )
    return extract_user_settlement_values(payload.get("data") or [], run_date)


def extract_market_board_online_unit_values(
    payload: dict[str, Any],
    run_date: date,
) -> list[int | float]:
    rows = [
        row
        for row in payload.get("dataApplyConfigDOList") or []
        if date_matches(row.get("date"), run_date)
        and str(row.get("dataType")) == "1"
    ]
    if not rows:
        raise RuntimeError(
            f"市场行情看板没有返回日期 {run_date.isoformat()} 的日前在线机组数据"
        )

    row = rows[0]
    values = []
    for field, label in (
        ("onlineUnit", "在线机组数"),
        ("overHaulCapacity", "省内机组预测检修容量"),
    ):
        value = row.get(field)
        if value in (None, ""):
            raise RuntimeError(f"市场行情看板缺少{label}：{field}")
        values.append(normalize_report_number(value))
    return values


def fetch_market_board_online_unit_values(
    context,
    run_date: date,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdfire/api/spot/market/info",
        params={
            "provinceAreaId": DEFAULT_PROVINCE_AREA_ID,
            "provinceId": DEFAULT_PROVINCE_AREA_ID.lstrip("0"),
            "timeSegment": 2,
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
        },
        timeout=30000,
    )
    payload = online_export_module.parse_json_response(
        response,
        f"读取市场行情看板在线机组数与检修容量 {run_date.isoformat()}",
    )
    return extract_market_board_online_unit_values(payload.get("data") or {}, run_date)


def extract_spot_clearing_unit_count_values(
    payload: list[dict[str, Any]],
    run_date: date,
) -> list[int | float]:
    rows_by_group = {
        str(row.get("dataItemGroup")): row
        for row in payload
        if date_matches(row.get("date"), run_date)
        and str(row.get("dataType")) == "1"
    }

    coal_row = rows_by_group.get(DAY_AHEAD_CLEARING_UNIT_COUNT_GROUPS[0])
    gas_row = rows_by_group.get(DAY_AHEAD_CLEARING_UNIT_COUNT_GROUPS[1])
    if coal_row is None:
        raise RuntimeError(f"现货分时分类型出清电量缺少 {run_date.isoformat()} 日前燃煤数据")
    if gas_row is None:
        raise RuntimeError(f"现货分时分类型出清电量缺少 {run_date.isoformat()} 日前燃气数据")

    coal_counts = coal_row.get("unitCount") or []
    if len(coal_counts) < 24 or coal_counts[23] in (None, ""):
        raise RuntimeError("现货分时分类型出清电量日前燃煤缺少 23:00 开机台数")

    gas_counts = [
        normalize_report_number(value)
        for value in (gas_row.get("unitCount") or [])
        if value not in (None, "")
    ]
    if not gas_counts:
        raise RuntimeError("现货分时分类型出清电量日前燃气缺少开机台数")

    return [
        normalize_report_number(coal_counts[23]),
        normalize_number(max(gas_counts)),
    ]


def fetch_spot_clearing_unit_count_values(
    context,
    run_date: date,
) -> list[int | float]:
    response = context.request.post(
        f"{online_export_module.BASE_URL}/gdfire/api/spot/clear/timeType/data/list",
        data={
            "provinceAreaId": DEFAULT_PROVINCE_AREA_ID,
            "spotClearEleGroupList": list(DAY_AHEAD_CLEARING_UNIT_COUNT_GROUPS),
            "startDate": run_date.isoformat(),
            "endDate": run_date.isoformat(),
        },
        timeout=30000,
    )
    payload = online_export_module.parse_json_response(
        response,
        f"读取现货分时分类型出清电量日前燃煤和燃气 {run_date.isoformat()}",
    )
    return extract_spot_clearing_unit_count_values(payload.get("data") or [], run_date)


def extract_unit_cost_price_values(payload: list[dict[str, Any]]) -> list[int | float]:
    rows_by_unit_id: dict[str, dict[str, Any]] = {}
    for org_item in payload:
        for row in org_item.get("unitCostInfoDTOList") or []:
            unit_id = row.get("unitId")
            if unit_id:
                rows_by_unit_id[str(unit_id)] = row

    values: list[int | float] = []
    for unit_id in UNIT_COST_PRICE_UNIT_IDS:
        row = rows_by_unit_id.get(unit_id)
        if row is None:
            raise RuntimeError(f"机组成本信息未找到机组 ID：{unit_id}")
        value = row.get("priceOfDyCost")
        if value in (None, ""):
            raise RuntimeError(f"机组成本信息缺少单位变动成本对应电价：{unit_id}")
        values.append(normalize_number(value))

    if len(values) != 11:
        raise RuntimeError(f"单位变动成本对应电价应为 11 个数，实际得到 {len(values)} 个")
    return values


def fetch_unit_cost_price_values(
    context,
    run_date: date,
) -> list[int | float]:
    response = context.request.get(
        f"{online_export_module.BASE_URL}/gdgroup/fire/api/data/personal/approve/report/data",
        params={
            "runDate": run_date.isoformat(),
            "orgIds": ",".join(UNIT_COST_ORG_IDS),
        },
        timeout=30000,
    )
    payload = online_export_module.parse_json_response(
        response,
        f"读取机组成本信息 {run_date.isoformat()}",
    )
    return extract_unit_cost_price_values(payload.get("data") or [])


def write_actual_load_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    return write_market_load_values(
        workbook_path,
        run_date,
        values,
        start_column=ACTUAL_LOAD_START_COLUMN,
        label="市场负荷",
    )


def write_day_ahead_load_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    return write_market_load_values(
        workbook_path,
        run_date,
        values,
        start_column=DAY_AHEAD_LOAD_START_COLUMN,
        label="日前市场负荷",
    )


def write_day_ahead_market_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    return write_market_row_values(
        workbook_path,
        run_date,
        values,
        start_column=DAY_AHEAD_MARKET_START_COLUMN,
        expected_count=12,
        label="日前市场",
    )


def write_realtime_market_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    return write_market_row_values(
        workbook_path,
        run_date,
        values,
        start_column=REALTIME_MARKET_START_COLUMN,
        expected_count=4,
        label="实时市场",
    )


def write_generation_settlement_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    return write_market_row_values(
        workbook_path,
        run_date,
        values,
        start_column=GENERATION_SETTLEMENT_START_COLUMN,
        expected_count=12,
        label="发电侧结算",
        overwrite=True,
    )


def write_user_settlement_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    return write_market_row_values(
        workbook_path,
        run_date,
        values,
        start_column=USER_SETTLEMENT_START_COLUMN,
        expected_count=7,
        label="用电侧结算",
        overwrite=True,
    )


def write_market_load_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
    *,
    start_column: str,
    label: str,
) -> dict[str, Any]:
    return write_market_row_values(
        workbook_path,
        run_date,
        values,
        start_column=start_column,
        expected_count=20,
        label=label,
    )


def write_market_row_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
    *,
    start_column: str,
    expected_count: int,
    label: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    if len(values) != expected_count:
        raise ValueError(f"需要写入 {expected_count} 个{label}数值，实际收到 {len(values)} 个")

    path = Path(workbook_path).expanduser()
    row = find_date_row_in_workbook(path, run_date)
    workbook = workbook_for_write(path)
    if MARKET_SHEET_NAME not in workbook.sheetnames:
        raise RuntimeError(f"Excel 中未找到工作表：{MARKET_SHEET_NAME}")
    sheet = workbook[MARKET_SHEET_NAME]
    style_row = find_style_source_row(sheet, run_date, row)
    start_column_index = openpyxl.utils.column_index_from_string(start_column)

    written_count = 0
    skipped_count = 0
    for offset, value in enumerate(values):
        cell = sheet.cell(row=row, column=start_column_index + offset)
        if cell_is_non_empty(cell) and not overwrite:
            skipped_count += 1
            continue
        if style_row is not None:
            copy_numeric_cell_style(sheet.cell(row=style_row, column=start_column_index + offset), cell)
        cell.value = value
        record_written_cell(path, cell)
        written_count += 1

    save_written_workbook(path, workbook)
    end_column = start_column_index + len(values) - 1
    return {
        "workbook": str(path),
        "sheet": sheet.title,
        "row": row,
        "range": f"{start_column}{row}:{get_column_letter(end_column)}{row}",
        "count": written_count,
        "skipped": skipped_count,
    }


def write_unit_cost_price_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    if len(values) != 11:
        raise ValueError(f"需要写入 11 个单位变动成本对应电价，实际收到 {len(values)} 个")

    path = Path(workbook_path).expanduser()
    row = find_date_row_in_workbook(path, run_date, COST_SHEET_NAME)
    workbook = workbook_for_write(path)
    if COST_SHEET_NAME not in workbook.sheetnames:
        raise RuntimeError(f"Excel 中未找到工作表：{COST_SHEET_NAME}")
    sheet = workbook[COST_SHEET_NAME]
    style_row = find_style_source_row(sheet, run_date, row)
    start_column = openpyxl.utils.column_index_from_string(UNIT_COST_START_COLUMN)

    written_count = 0
    skipped_count = 0
    for offset, value in enumerate(values):
        cell = sheet.cell(row=row, column=start_column + offset)
        if cell_is_non_empty(cell):
            skipped_count += 1
            continue
        if style_row is not None:
            copy_numeric_cell_style(sheet.cell(row=style_row, column=start_column + offset), cell)
        cell.value = value
        record_written_cell(path, cell)
        written_count += 1

    save_written_workbook(path, workbook)
    end_column = start_column + len(values) - 1
    return {
        "workbook": str(path),
        "sheet": sheet.title,
        "row": row,
        "range": f"{UNIT_COST_START_COLUMN}{row}:{get_column_letter(end_column)}{row}",
        "count": written_count,
        "skipped": skipped_count,
    }


def write_market_board_online_unit_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    if len(values) != 2:
        raise ValueError(f"需要写入 2 个在线机组数与检修容量数值，实际收到 {len(values)} 个")

    path = Path(workbook_path).expanduser()
    row = find_date_row_in_workbook(path, run_date, COST_SHEET_NAME)
    workbook = workbook_for_write(path)
    if COST_SHEET_NAME not in workbook.sheetnames:
        raise RuntimeError(f"Excel 中未找到工作表：{COST_SHEET_NAME}")
    sheet = workbook[COST_SHEET_NAME]
    style_row = find_style_source_row(sheet, run_date, row)

    written_count = 0
    skipped_count = 0
    cells = []
    for column_name, value in zip(MARKET_BOARD_ONLINE_UNIT_COLUMNS, values):
        column = openpyxl.utils.column_index_from_string(column_name)
        cell = sheet.cell(row=row, column=column)
        cells.append(cell.coordinate)
        if cell_is_non_empty(cell):
            skipped_count += 1
            continue
        if style_row is not None:
            copy_numeric_cell_style(sheet.cell(row=style_row, column=column), cell)
        cell.value = value
        record_written_cell(path, cell)
        written_count += 1

    save_written_workbook(path, workbook)
    return {
        "workbook": str(path),
        "sheet": sheet.title,
        "row": row,
        "range": ",".join(cells),
        "count": written_count,
        "skipped": skipped_count,
    }


def write_spot_clearing_unit_count_values(
    workbook_path: Path | str,
    run_date: date,
    values: list[int | float],
) -> dict[str, Any]:
    if len(values) != 2:
        raise ValueError(f"需要写入 2 个燃煤和燃气开机台数，实际收到 {len(values)} 个")

    path = Path(workbook_path).expanduser()
    row = find_date_row_in_workbook(path, run_date, COST_SHEET_NAME)
    workbook = workbook_for_write(path)
    if COST_SHEET_NAME not in workbook.sheetnames:
        raise RuntimeError(f"Excel 中未找到工作表：{COST_SHEET_NAME}")
    sheet = workbook[COST_SHEET_NAME]
    style_row = find_style_source_row(sheet, run_date, row)
    start_column = openpyxl.utils.column_index_from_string(
        SPOT_CLEARING_UNIT_COUNT_START_COLUMN
    )

    written_count = 0
    skipped_count = 0
    for offset, value in enumerate(values):
        cell = sheet.cell(row=row, column=start_column + offset)
        if cell_is_non_empty(cell):
            skipped_count += 1
            continue
        if style_row is not None:
            copy_numeric_cell_style(sheet.cell(row=style_row, column=start_column + offset), cell)
        cell.value = value
        record_written_cell(path, cell)
        written_count += 1

    save_written_workbook(path, workbook)
    return {
        "workbook": str(path),
        "sheet": sheet.title,
        "row": row,
        "range": (
            f"{SPOT_CLEARING_UNIT_COUNT_START_COLUMN}{row}:"
            f"{SPOT_CLEARING_UNIT_COUNT_END_COLUMN}{row}"
        ),
        "count": written_count,
        "skipped": skipped_count,
    }


def copy_previous_day_unit_operation_mode(
    workbook_path: Path | str,
    run_date: date,
) -> dict[str, Any]:
    path = Path(workbook_path).expanduser()
    source_date = run_date - timedelta(days=1)
    target_row = find_date_row_in_workbook(path, run_date, COST_SHEET_NAME)
    source_row = find_date_row_in_workbook(path, source_date, COST_SHEET_NAME)

    workbook = workbook_for_write(path)
    if COST_SHEET_NAME not in workbook.sheetnames:
        raise RuntimeError(f"Excel 中未找到工作表：{COST_SHEET_NAME}")
    sheet = workbook[COST_SHEET_NAME]
    start_column = openpyxl.utils.column_index_from_string(OPERATION_MODE_START_COLUMN)
    end_column = openpyxl.utils.column_index_from_string(OPERATION_MODE_END_COLUMN)

    written_count = 0
    skipped_count = 0
    for column in range(start_column, end_column + 1):
        source = sheet.cell(row=source_row, column=column)
        target = sheet.cell(row=target_row, column=column)
        if cell_is_non_empty(target):
            skipped_count += 1
            continue
        copy_cell_style(source, target)
        target.value = source.value
        record_written_cell(path, target)
        written_count += 1

    save_written_workbook(path, workbook)
    return {
        "workbook": str(path),
        "sheet": sheet.title,
        "sourceDate": source_date.isoformat(),
        "sourceRow": source_row,
        "row": target_row,
        "range": operation_mode_range(target_row),
        "count": written_count,
        "skipped": skipped_count,
    }


def fetch_values_or_skip(label: str, fetcher, *, attempts: int = 3):
    for attempt in range(1, attempts + 1):
        try:
            return fetcher()
        except PlaywrightError as exc:
            if attempt < attempts:
                log(f"{label}读取失败，正在重试 {attempt + 1}/{attempts}：{exc}")
                continue
            log(f"跳过{label}：连续 {attempts} 次读取失败：{exc}")
            return None
        except (RuntimeError, ValueError) as exc:
            log(f"跳过{label}：{exc}")
            return None


def run_update(
    workbook_path: Path,
    run_date: date,
    *,
    headless: bool,
) -> dict[str, Any]:
    ensure_workbook_accessible(workbook_path)
    with workbook_write_session(workbook_path):
        return _run_update_with_session(workbook_path, run_date, headless=headless)


def _run_update_with_session(
    workbook_path: Path,
    run_date: date,
    *,
    headless: bool,
) -> dict[str, Any]:
    config = load_config()
    config.headless = headless

    with AuthStateLock():
        with sync_playwright() as playwright:
            context = launch_context(playwright, headless=config.headless)
            try:
                ensure_login(context, config)
                log(
                    "正在读取现货运行日报市场负荷信息-日前："
                    f"{run_date.isoformat()}（基准日期 D）"
                )
                day_ahead_values = fetch_values_or_skip(
                    "现货运行日报市场负荷信息-日前",
                    lambda: fetch_day_ahead_load_values(
                        context,
                        run_date,
                    ),
                )
                day_ahead_summary = None
                if day_ahead_values is not None:
                    log("已读取 20 个日前市场负荷数值。")
                    day_ahead_summary = write_day_ahead_load_values(
                        workbook_path,
                        run_date,
                        day_ahead_values,
                    )
                    log(
                        f"已写入：{day_ahead_summary['sheet']}!{day_ahead_summary['range']} "
                        f"（{write_count_text(day_ahead_summary)}）"
                    )
                market_date = day_ahead_market_source_date(run_date)
                log(
                    "正在读取现货运行日报日前市场："
                    f"{market_date.isoformat()}（基准日期 {run_date.isoformat()} 的 D-1）"
                )
                day_ahead_market_values = fetch_values_or_skip(
                    "现货运行日报日前市场",
                    lambda: fetch_day_ahead_market_values(
                        context,
                        market_date,
                    ),
                )
                day_ahead_market_summary = None
                if day_ahead_market_values is not None:
                    log("已读取发电侧申报均价起 12 个日前市场数值。")
                    day_ahead_market_summary = write_day_ahead_market_values(
                        workbook_path,
                        market_date,
                        day_ahead_market_values,
                    )
                    log(
                        f"已写入：{day_ahead_market_summary['sheet']}!{day_ahead_market_summary['range']} "
                        f"（{write_count_text(day_ahead_market_summary)}）"
                    )
                realtime_market_date = realtime_market_source_date(run_date)
                log(
                    "正在读取现货运行日报实时市场："
                    f"{realtime_market_date.isoformat()}（基准日期 {run_date.isoformat()} 的 D-2）"
                )
                realtime_market_values = fetch_values_or_skip(
                    "现货运行日报实时市场",
                    lambda: fetch_realtime_market_values(
                        context,
                        realtime_market_date,
                    ),
                )
                realtime_market_summary = None
                if realtime_market_values is not None:
                    log("已读取发电侧成交电量等 4 个实时市场数值。")
                    realtime_market_summary = write_realtime_market_values(
                        workbook_path,
                        realtime_market_date,
                        realtime_market_values,
                    )
                    log(
                        f"已写入：{realtime_market_summary['sheet']}!{realtime_market_summary['range']} "
                        f"（{write_count_text(realtime_market_summary)}）"
                    )
                generation_settlement_date = generation_settlement_source_date(run_date)
                log(
                    "正在读取现货运行日报发电侧结算："
                    f"{generation_settlement_date.isoformat()}（基准日期 {run_date.isoformat()} 的 D-6）"
                )
                generation_settlement_values = fetch_values_or_skip(
                    "现货运行日报发电侧结算",
                    lambda: fetch_generation_settlement_values(
                        context,
                        generation_settlement_date,
                    ),
                )
                generation_settlement_summary = None
                if generation_settlement_values is not None:
                    log("已读取 12 个发电侧结算数值。")
                    generation_settlement_summary = write_generation_settlement_values(
                        workbook_path,
                        generation_settlement_date,
                        generation_settlement_values,
                    )
                    log(
                        f"已写入：{generation_settlement_summary['sheet']}!{generation_settlement_summary['range']} "
                        f"（{write_count_text(generation_settlement_summary)}）"
                    )
                user_settlement_date = user_settlement_source_date(run_date)
                log(
                    "正在读取现货运行日报用电侧结算："
                    f"{user_settlement_date.isoformat()}（基准日期 {run_date.isoformat()} 的 D-6）"
                )
                user_settlement_values = fetch_values_or_skip(
                    "现货运行日报用电侧结算",
                    lambda: fetch_user_settlement_values(
                        context,
                        user_settlement_date,
                    ),
                )
                user_settlement_summary = None
                if user_settlement_values is not None:
                    log("已读取 7 个用电侧结算数值。")
                    user_settlement_summary = write_user_settlement_values(
                        workbook_path,
                        user_settlement_date,
                        user_settlement_values,
                    )
                    log(
                        f"已写入：{user_settlement_summary['sheet']}!{user_settlement_summary['range']} "
                        f"（{write_count_text(user_settlement_summary)}）"
                    )
                load_date = actual_load_source_date(run_date)
                log(
                    "正在读取现货运行日报市场负荷信息："
                    f"{load_date.isoformat()}（基准日期 {run_date.isoformat()} 的 D-2）"
                )
                values = fetch_values_or_skip(
                    "现货运行日报市场负荷信息-实际",
                    lambda: fetch_actual_load_values(
                        context,
                        load_date,
                    ),
                )
                summary = None
                if values is not None:
                    log("已读取 20 个市场负荷数值。")
                    summary = write_actual_load_values(workbook_path, load_date, values)
                    log(
                        f"已写入：{summary['sheet']}!{summary['range']} "
                        f"（{write_count_text(summary)}）"
                    )
                log(f"正在读取现货基础信息报送机组成本信息：{run_date.isoformat()}")
                cost_values = fetch_values_or_skip(
                    "现货基础信息报送机组成本信息",
                    lambda: fetch_unit_cost_price_values(
                        context,
                        run_date,
                    ),
                )
                cost_summary = None
                if cost_values is not None:
                    log("已读取 11 个单位变动成本对应电价。")
                    cost_summary = write_unit_cost_price_values(
                        workbook_path,
                        run_date,
                        cost_values,
                    )
                    log(
                        f"已写入：{cost_summary['sheet']}!{cost_summary['range']} "
                        f"（{write_count_text(cost_summary)}）"
                    )
                log(
                    "正在读取省内现货交易市场行情看板在线机组数与检修容量："
                    f"{run_date.isoformat()}（基准日期 D）"
                )
                market_board_values = fetch_values_or_skip(
                    "省内现货交易市场行情看板在线机组数与检修容量",
                    lambda: fetch_market_board_online_unit_values(context, run_date),
                )
                market_board_summary = None
                if market_board_values is not None:
                    log("已读取在线机组数与省内机组预测检修容量 2 个数值。")
                    market_board_summary = write_market_board_online_unit_values(
                        workbook_path,
                        run_date,
                        market_board_values,
                    )
                    log(
                        f"已写入：{market_board_summary['sheet']}!{market_board_summary['range']} "
                        f"（{write_count_text(market_board_summary)}）"
                    )
                log(
                    "正在读取公有数据管理现货分时分类型出清电量日前燃煤和燃气："
                    f"{run_date.isoformat()}（基准日期 D）"
                )
                spot_clearing_values = fetch_values_or_skip(
                    "公有数据管理现货分时分类型出清电量日前燃煤和燃气",
                    lambda: fetch_spot_clearing_unit_count_values(context, run_date),
                )
                spot_clearing_summary = None
                if spot_clearing_values is not None:
                    log("已读取日前燃煤 23:00 和燃气全天最大开机台数 2 个数值。")
                    spot_clearing_summary = write_spot_clearing_unit_count_values(
                        workbook_path,
                        run_date,
                        spot_clearing_values,
                    )
                    log(
                        f"已写入：{spot_clearing_summary['sheet']}!{spot_clearing_summary['range']} "
                        f"（{write_count_text(spot_clearing_summary)}）"
                    )
                log(
                    "正在复制机组运行方式："
                    f"{(run_date - timedelta(days=1)).isoformat()} -> {run_date.isoformat()}"
                )
                operation_summary = copy_previous_day_unit_operation_mode(
                    workbook_path,
                    run_date,
                )
                log(
                    f"已写入：{operation_summary['sheet']}!{operation_summary['range']} "
                    f"（{write_count_text(operation_summary)}，来自第 {operation_summary['sourceRow']} 行）"
                )
                return {
                    "dayAheadLoad": day_ahead_summary,
                    "dayAheadMarket": day_ahead_market_summary,
                    "realtimeMarket": realtime_market_summary,
                    "generationSettlement": generation_settlement_summary,
                    "userSettlement": user_settlement_summary,
                    "actualLoad": summary,
                    "unitCostPrices": cost_summary,
                    "marketBoardOnlineUnit": market_board_summary,
                    "spotClearingUnitCounts": spot_clearing_summary,
                    "unitOperationMode": operation_summary,
                }
            finally:
                context.close()


def login_only(*, headless: bool) -> None:
    config = load_config()
    config.headless = headless
    with AuthStateLock():
        with sync_playwright() as playwright:
            context = launch_context(playwright, headless=config.headless)
            try:
                interactive_login(context, config)
            finally:
                context.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取网页市场数据并更新市场 Excel。")
    parser.add_argument("--date", required=False, help="基准运行日期，格式 YYYY-MM-DD")
    parser.add_argument("--workbook", type=Path, help="要写入的 Excel 文件")
    parser.add_argument("--login", action="store_true", help="打开登录窗口并保存登录态")
    parser.add_argument("--headed", action="store_true", help="使用可见浏览器窗口")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    headless = not args.headed
    if args.login:
        login_only(headless=headless)
        return 0

    if not args.date or not args.workbook:
        raise SystemExit("请同时提供 --date 和 --workbook，或使用 --login。")

    run_update(
        args.workbook,
        parse_date(args.date),
        headless=headless,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
