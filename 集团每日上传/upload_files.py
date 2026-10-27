"""Group daily upload: file classification, template probing and upload planning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from toolbox_shared.dates import normalize_date_input

UPLOAD_TYPES = {
    "省内": {
        "name": "省内数据",
        "filename_keywords": ("省内",),
        "page_keywords": ("省内",),
        "data_type": "DAY_MARKET_INFO",
    },
    "能销": {
        "name": "能销数据",
        "filename_keywords": ("能销",),
        "page_keywords": ("能销",),
        "data_type": "DAY_ENERGY_SCALE",
    },
}


@dataclass
class UploadFile:
    path: Path
    kind: str
    date: str
    auto_template: bool = False
    builtin_template: bool = False


def validate_date(value: str) -> str:
    return normalize_date_input(value)


def daily_report_name(run_date: str) -> str:
    validated = validate_date(run_date)
    compact_date = validated.replace("-", "")
    return f"广东电力现货市场监测评估日报（{compact_date}）"


def extract_date_from_name(path: Path) -> str:
    name = path.stem
    compact = re.search(r"(20\d{2})([01]\d)([0-3]\d)", name)
    if compact:
        y, m, d = compact.groups()
        return validate_date(f"{y}-{m}-{d}")

    separated = re.search(r"(20\d{2})[-_.年 ]([01]?\d)[-_.月 ]([0-3]?\d)", name)
    if separated:
        y, m, d = separated.groups()
        return validate_date(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")

    raise ValueError(f"文件名里没有识别到日期：{path.name}")


def classify_file(path: Path) -> str:
    name = path.name
    matches = [
        kind
        for kind, spec in UPLOAD_TYPES.items()
        if any(keyword in name for keyword in spec["filename_keywords"])
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"无法从文件名判断上传类型，请包含“省内”或“能销”：{path.name}")
    raise ValueError(f"文件名同时匹配多个上传类型：{path.name}")


def choose_files_interactively() -> list[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("无法打开文件选择窗口，请改用命令行传入两个文件路径。") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askopenfilenames(
        title="请选择能销数据 Excel；也可同时选择省内数据",
        initialdir=str(SCRIPT_DIR),
        filetypes=[
            ("Excel 文件", "*.xlsx *.xls"),
            ("所有文件", "*.*"),
        ],
    )
    root.destroy()
    return [Path(item) for item in selected]


def probe_standard_province_templates(folder: Path, target_date: str, *, days: int = 45) -> list[Path]:
    center = datetime.strptime(target_date, "%Y-%m-%d").date()
    candidates: list[Path] = []
    for offset in range(days + 1):
        offsets = [0] if offset == 0 else [-offset, offset]
        for day_offset in offsets:
            day = center + timedelta(days=day_offset)
            path = folder / f"广东-省内数据-{day:%Y%m%d}.xlsx"
            if path.exists() and not path.name.startswith("~$") and "自动更新" not in path.stem:
                candidates.append(path)
    return candidates


def find_province_template(folder: Path, target_date: str, *, template: Path,
                           log: Callable[[str], None]) -> UploadFile:
    candidates: list[UploadFile] = []
    visible_excel_files: list[str] = []
    paths = list(folder.glob("*.xlsx"))
    if not paths:
        paths = probe_standard_province_templates(folder, target_date)
    for path in paths:
        if path.name.startswith("~$") or "自动更新" in path.stem:
            continue
        visible_excel_files.append(path.name)
        if "省内" not in path.name:
            continue
        try:
            candidates.append(
                UploadFile(
                    path=path.resolve(),
                    kind="省内",
                    date=extract_date_from_name(path),
                    auto_template=True,
                )
            )
        except ValueError:
            continue
    if not candidates:
        if template.is_file():
            log("同目录未找到可用省内数据，使用内置省内模板。")
            return UploadFile(
                path=template.resolve(), kind="省内",
                date=target_date, auto_template=True, builtin_template=True,
            )
        visible = "、".join(sorted(visible_excel_files)) or "无"
        raise ValueError(
            f"没有在 {folder} 找到可作为模板的省内数据 .xlsx 文件。"
            f"内置省内模板也不存在：{template}。"
            f"实际看到的 Excel 文件：{visible}"
        )
    target_day = datetime.strptime(target_date, "%Y-%m-%d").date()
    selected = min(
        candidates,
        key=lambda item: (
            abs((datetime.strptime(item.date, "%Y-%m-%d").date() - target_day).days),
            datetime.strptime(item.date, "%Y-%m-%d").date() > target_day,
            -item.path.stat().st_mtime,
        ),
    )
    log(f"自动选择省内模板：{selected.path.name}")
    return selected


def prepare_upload_files(paths: list[Path], *, template: Path,
                         log: Callable[[str], None]) -> tuple[str, list[UploadFile]]:
    if len(paths) not in (1, 2):
        raise ValueError("请选择一个能销数据文件，或同时选择一个省内数据和一个能销数据文件。")

    uploads: list[UploadFile] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = raw_path.expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"文件不存在：{path}")
        kind = classify_file(path)
        if kind in seen:
            raise ValueError(f"同一类型文件只能选一个，重复类型：{UPLOAD_TYPES[kind]['name']}")
        seen.add(kind)
        builtin = path == template.resolve()
        uploads.append(UploadFile(
            path=path, kind=kind, date="" if builtin else extract_date_from_name(path),
            auto_template=builtin, builtin_template=builtin,
        ))

    if len(uploads) == 1:
        only_upload = uploads[0]
        if only_upload.kind != "能销":
            raise ValueError("只选择一个文件时必须选择能销数据文件，省内数据会自动从同目录或内置模板生成。")
        uploads.append(find_province_template(
            only_upload.path.parent, only_upload.date, template=template, log=log))
        seen.add("省内")

    missing = set(UPLOAD_TYPES) - seen
    if missing:
        names = "、".join(UPLOAD_TYPES[kind]["name"] for kind in sorted(missing))
        raise ValueError(f"缺少文件：{names}")

    ordered = sorted(uploads, key=lambda item: 0 if item.kind == "省内" else 1)
    energy_upload = next(item for item in ordered if item.kind == "能销")
    for item in ordered:
        if item.builtin_template:
            item.date = energy_upload.date
    return energy_upload.date, ordered
