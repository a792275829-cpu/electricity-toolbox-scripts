"""Group daily upload: file upload, province workbook update and daily status API.

The entry script injects ``log``, ``BUILTIN_PROVINCE_TEMPLATE`` and ``DIAGNOSTICS_DIR``
so the GUI log sink and managed data directory stay owned by the entry point.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from page_navigation import BASE_URL, HUANENG_GROUP_URL
from toolbox_shared.province_excel import (
    format_formula_number,
    number_or_none,
    write_province_workbook,
)
from upload_files import UPLOAD_TYPES, UploadFile

SCRIPT_DIR = Path(__file__).resolve().parent
BUILTIN_PROVINCE_TEMPLATE = SCRIPT_DIR / "templates" / "省内数据模板.xlsx"
DEFAULT_PUBLIC_PROVINCE_AREA_ID = "044"
DIAGNOSTICS_DIR = Path.cwd()


def log(message: str) -> None:
    print(message, flush=True)


def mark_upload_target(page: Page, keywords: tuple[str, ...]) -> str | None:
    return page.evaluate(
        """
        ({ keywords }) => {
          const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const textOf = el => String(el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
          const hasKeyword = text => keywords.some(keyword => text.includes(keyword));
          const uploadWords = ['选择文件', '上传', '导入', '请选择文件'];
          const hasUploadWord = text => uploadWords.some(word => text.includes(word));

          document.querySelectorAll('[data-codex-upload-target]').forEach(el => el.removeAttribute('data-codex-upload-target'));

          const fileInputs = [...document.querySelectorAll('input[type="file"]')];
          let bestInput = null;
          let bestInputScore = -1;
          for (const input of fileInputs) {
            let score = 0;
            let node = input;
            for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
              const text = textOf(node);
              if (hasKeyword(text)) score += 100 - depth * 8;
              if (hasUploadWord(text)) score += 20 - depth;
            }
            if (score > bestInputScore) {
              bestInput = input;
              bestInputScore = score;
            }
          }
          if (bestInput && bestInputScore >= 80) {
            bestInput.setAttribute('data-codex-upload-target', 'file-input');
            return 'file-input';
          }

          const clickables = [...document.querySelectorAll('button,[role="button"],.ant-upload,.el-upload,label')].filter(visible);
          let best = null;
          let bestScore = -1;
          for (const el of clickables) {
            const own = textOf(el);
            let score = hasUploadWord(own) ? 40 : 0;
            if (!score && el.tagName !== 'LABEL') continue;
            let node = el;
            for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
              const text = textOf(node);
              if (hasKeyword(text)) score += 100 - depth * 8;
              if (hasUploadWord(text)) score += 10 - depth;
            }
            if (score > bestScore) {
              best = el;
              bestScore = score;
            }
          }
          if (best && bestScore >= 80) {
            best.setAttribute('data-codex-upload-target', 'chooser');
            return 'chooser';
          }

          if (fileInputs.length === 2) {
            const index = keywords.some(keyword => keyword.includes('能销')) ? 1 : 0;
            fileInputs[index].setAttribute('data-codex-upload-target', 'file-input');
            return 'file-input';
          }
          return null;
        }
        """,
        {"keywords": list(keywords)},
    )


def dump_upload_diagnostics(page: Page) -> str:
    data = page.evaluate(
        """
        () => {
          const compact = s => String(s || '').replace(/\\s+/g, ' ').trim().slice(0, 180);
          const fileInputs = [...document.querySelectorAll('input[type="file"]')].map((el, index) => {
            let node = el;
            const parts = [];
            for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
              const text = compact(node.innerText || node.textContent || '');
              if (text) parts.push(text);
            }
            return { index, accept: el.accept || '', name: el.name || '', text: parts.join(' | ') };
          });
          const buttons = [...document.querySelectorAll('button,[role="button"],label')].map((el, index) => ({
            index,
            text: compact(el.innerText || el.textContent || ''),
            className: compact(el.className || ''),
          })).filter(item => item.text).slice(0, 80);
          return { url: location.href, title: document.title, fileInputs, buttons };
        }
        """
    )
    path = data_path(SCRIPT_DIR) / f"upload_page_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def upload_one_file(page: Page, upload: UploadFile) -> None:
    spec = UPLOAD_TYPES[upload.kind]
    log(f"正在上传 {spec['name']}：{upload.path.name}")
    target_kind = mark_upload_target(page, spec["page_keywords"])
    if not target_kind:
        diagnostics = dump_upload_diagnostics(page)
        raise RuntimeError(f"没有找到“{spec['name']}”对应的选择文件按钮。诊断已保存：{diagnostics}")

    if target_kind == "file-input":
        page.locator('[data-codex-upload-target="file-input"]').set_input_files(str(upload.path), timeout=15000)
    else:
        with page.expect_file_chooser(timeout=15000) as chooser_info:
            page.locator('[data-codex-upload-target="chooser"]').click(timeout=10000)
        chooser_info.value.set_files(str(upload.path))

    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(2000)
    log(f"{spec['name']} 已选择/上传。")


def parse_api_payload(response, label: str) -> dict[str, Any]:
    text = response.text()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} 返回的不是 JSON：HTTP {response.status}，{text[:300]}") from exc
    if response.status >= 400:
        raise RuntimeError(f"{label} HTTP {response.status}: {json.dumps(payload, ensure_ascii=False)[:500]}")
    ret_code = payload.get("retCode")
    if ret_code and ret_code != "T200":
        raise RuntimeError(f"{label} 失败：{payload.get('retMsg') or payload}")
    return payload


def fetch_capacity_average(context, run_date: str, province_area_id: str) -> float:
    response = context.request.get(
        f"{BASE_URL}/gdfire/api/data/net/capacity",
        params={
            "startDate": run_date,
            "endDate": run_date,
            "type": "1",
            "provinceAreaId": province_area_id,
        },
        timeout=30000,
    )
    payload = parse_api_payload(response, "读取容量信息")
    rows = payload.get("data", {}).get("dataNetCapacityDTOList") or []
    values = [number_or_none(row.get("capacity")) for row in rows if isinstance(row, dict)]
    values = [value for value in values if value is not None]
    if not values:
        raise RuntimeError(f"容量信息没有返回 {run_date} 的“日前/省内机组预测检修”数据。")
    return sum(values) / len(values)


def fetch_startup_max(context, run_date: str, province_area_id: str) -> int:
    groups_by_type = {
        "日前": ["DAY_AHEAD_CLEARANCE_COAL", "DAY_AHEAD_CLEARANCE_GAS"],
        "实时": ["REAL_TIME_CLEARANCE_COAL", "REAL_TIME_CLEARANCE_GAS"],
    }
    max_values: dict[str, int] = {}
    for label, groups in groups_by_type.items():
        response = context.request.post(
            f"{BASE_URL}/gdfire/api/spot/clear/timeType/data/list",
            data=json.dumps(
                {
                    "provinceAreaId": province_area_id,
                    "spotClearEleGroupList": groups,
                    "startDate": run_date,
                    "endDate": run_date,
                }
            ),
            headers={"Content-Type": "application/json"},
            timeout=30000,
        )
        payload = parse_api_payload(response, f"读取{label}出清电量开机台数")
        rows = [
            row
            for row in (payload.get("data") or [])
            if isinstance(row, dict)
            and str(row.get("date") or "").startswith(run_date)
            and str(row.get("dataItemGroup") or "") in groups
        ]
        rows_by_group = {str(row.get("dataItemGroup")): row for row in rows}
        missing_groups = [group for group in groups if group not in rows_by_group]
        if missing_groups:
            log(
                f"提醒：{label}出清电量未完整返回 {run_date} 的燃煤/燃气开机台数，"
                "将使用另一类型的完整数据。"
            )
            continue

        curves = []
        for group in groups:
            unit_count = rows_by_group[group].get("unitCount")
            if not isinstance(unit_count, list) or len(unit_count) != 24:
                break
            curve = []
            for raw_value in unit_count:
                value = number_or_none(raw_value)
                if (value is None or isinstance(raw_value, bool) or not math.isfinite(value)
                        or value < 0 or not value.is_integer()):
                    break
                curve.append(value)
            if len(curve) != 24:
                break
            curves.append(curve)
        if len(curves) != len(groups):
            log(f"提醒：{label}出清电量缺少完整24点有效燃煤/燃气开机台数，将使用另一类型的完整数据。")
            continue
        hourly_totals = [sum(values) for values in zip(*curves)]
        max_values[label] = int(round(max(hourly_totals)))

    if not max_values:
        raise RuntimeError(f"日前和实时出清电量都没有返回 {run_date} 的完整燃煤/燃气开机台数。")

    detail = "，".join(f"{label}最大{value}" for label, value in max_values.items())
    final_value = max(max_values.values())
    log(f"火电机组台数：{detail}，取 {final_value}")
    return final_value


def province_output_path(source_path: Path, run_date: str, *, output_dir: Path | None = None) -> Path:
    compact_date = run_date.replace("-", "")
    name = source_path.name
    new_name, replacements = re.subn(r"(20\d{2})([01]\d)([0-3]\d)", compact_date, name, count=1)
    if replacements == 0:
        new_name, replacements = re.subn(
            r"(20\d{2})[-_.年 ]([01]?\d)[-_.月 ]([0-3]?\d)",
            compact_date,
            name,
            count=1,
        )
    if replacements == 0:
        new_name = f"{source_path.stem}-{compact_date}{source_path.suffix}"

    if source_path.resolve() == BUILTIN_PROVINCE_TEMPLATE.resolve():
        if output_dir is None:
            raise ValueError("使用内置省内模板时必须指定输出目录。")
        new_name = f"广东-省内数据-{compact_date}.xlsx"
    target_path = (output_dir or source_path.parent) / new_name
    lock_path = target_path.with_name(f"~${target_path.name}")
    if lock_path.exists():
        raise RuntimeError(f"省内数据目标文件正在被 Excel 打开，请先关闭：{target_path.name}")

    if target_path.exists() and target_path.resolve() != source_path.resolve():
        log(f"省内数据目标文件已存在，将覆盖：{target_path.name}")
    return target_path


def update_province_excel_for_upload(
    context,
    run_date: str,
    uploads: list[UploadFile],
    province_area_id: str = DEFAULT_PUBLIC_PROVINCE_AREA_ID,
) -> list[UploadFile]:
    try:
        from openpyxl import load_workbook
    except Exception as exc:
        raise RuntimeError("当前 Python 环境缺少 openpyxl，无法自动更新省内数据 Excel。") from exc

    province_upload = next(item for item in uploads if item.kind == "省内")
    energy_upload = next(item for item in uploads if item.kind == "能销")
    if province_upload.path.suffix.lower() != ".xlsx":
        raise RuntimeError("省内数据自动更新只支持 .xlsx 文件。")

    log("开始自动更新省内数据 Excel...")
    if province_upload.date != run_date:
        log(f"省内文件日期 {province_upload.date} 与能销日期 {run_date} 不一致，将按能销日期另存。")

    startup_max = fetch_startup_max(context, run_date, province_area_id)
    capacity_average = fetch_capacity_average(context, run_date, province_area_id)
    log(f"市场化运行容量公式替换值：{format_formula_number(capacity_average)}")

    target_path = province_output_path(
        province_upload.path, run_date,
        output_dir=energy_upload.path.parent if province_upload.builtin_template else None,
    )
    write_province_workbook(province_upload.path, target_path, run_date,
                            startup_max, capacity_average, log=log)

    log(f"省内数据已更新：{target_path}")
    return [
        UploadFile(path=target_path.resolve(), kind="省内", date=run_date),
        UploadFile(path=energy_upload.path, kind="能销", date=run_date),
    ]


def get_daily_status(context, run_date: str, data_type: str, province_id: str) -> dict[str, Any] | None:
    response = context.request.get(
        f"{HUANENG_GROUP_URL}/api/group/private/data/page",
        params={"date": run_date, "groupDataType": data_type, "provinceId": province_id},
        timeout=30000,
    )
    payload = parse_api_payload(response, "查询日报上传状态")
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise RuntimeError("查询日报上传状态返回格式不正确。")
    return rows[0] if rows else None


def file_payload(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "buffer": path.read_bytes(),
    }


def import_daily_file(context, upload: UploadFile, run_date: str, province_id: str) -> dict[str, Any]:
    spec = UPLOAD_TYPES[upload.kind]
    data_type = str(spec["data_type"])
    response = context.request.post(
        f"{HUANENG_GROUP_URL}/api/group/data/import/create/multi",
        multipart={
            "files": file_payload(upload.path),
            "date": run_date,
            "dataType": data_type,
            "type": data_type,
            "provinceId": province_id,
        },
        timeout=120000,
    )
    return parse_api_payload(response, f"导入{spec['name']}")
