"""Shared command construction for the Tk fallback and desktop service.

No widgets or GUI imports belong in this module. Business scripts remain the
authority for workbook mappings, dates and overwrite behavior.
"""
from __future__ import annotations

from pathlib import Path
from .runtime import python_executable


def login(paths, tool_id):
    from toolbox_shared.login_tools import STATE_DIRS
    if tool_id == "summary-upload":
        from .runtime import node_executable
        return [node_executable(), str(paths.private_uploader), "--login", "--headed"]
    if tool_id not in STATE_DIRS:
        raise ValueError("当前工具不使用清能网站登录。")
    return [python_executable(), str(paths.workspace / "toolbox_shared/login_tools.py"),
            "--workspace", str(paths.workspace), "--tool", tool_id]


def dated_script(script: Path, run_date: str) -> list[str]:
    return [python_executable(), str(script), run_date]


def section_analysis(paths, files, output_root, topology_path, threshold):
    return [python_executable(), str(paths.section_analysis_runner),
            "--output-root", str(output_root), "--topology", str(topology_path),
            "--threshold", str(threshold), *(str(p) for p in files)]


def market_table(paths, run_date, workbook):
    return [python_executable(), str(paths.market_table_update), "--date",
            run_date.isoformat(), "--workbook", str(workbook)]


def transaction_info(paths, workbook, end_date, *, start_date, create_backup):
    command = [python_executable(), str(paths.transaction_info_update),
               "--workbook", str(workbook), "--end-date", end_date.isoformat()]
    if start_date is not None:
        command.extend(("--start-date", start_date.isoformat()))
    if not create_backup:
        command.append("--no-backup")
    return command


def power_benefit(paths, workbooks, end_date, *, start_date, create_backup):
    command = [python_executable(), str(paths.power_benefit_update),
               "--end-date", end_date.isoformat()]
    for path in workbooks:
        command.extend(("--workbook", str(path)))
    if start_date is not None:
        command.extend(("--start-date", start_date.isoformat()))
    if not create_backup:
        command.append("--no-backup")
    return command


def spot_price(paths, start_date, end_date, workbooks, *, overwrite, create_backup, selected):
    if not selected:
        raise ValueError("请至少选择一个更新场站。")
    options = {"归湖": "--guihu-workbook", "鮀莲": "--tuolian-workbook", "海风": "--haifeng-workbook"}
    command = [python_executable(), str(paths.spot_price_update), "--start-date",
               start_date.isoformat(), "--end-date", end_date.isoformat()]
    for label in selected:
        command.extend((options[label], str(workbooks[label])))
    command.extend(("--stations", *selected))
    if not overwrite:
        command.append("--skip-existing")
    if not create_backup:
        command.append("--no-backup")
    return command


def daily_pdf(paths, pdf_path, output_path, mode):
    return [python_executable(), str(paths.daily_pdf_parser), str(pdf_path),
            "--output", str(output_path), "--mode", mode]


def word_report(paths, report_date, online, day_ahead, daily, template):
    command = [python_executable(), str(paths.report_scripts_dir / "generate_red_marked_report.py"),
               report_date, "--online-workbook", str(online), "--day-ahead-workbook", str(day_ahead),
               "--daily-clearing-workbook", str(daily)]
    if template is not None:
        command.extend(("--template", str(template)))
    return command


def prediction(paths, input_path, output_dir, forecast_days, training_months, run_date):
    if forecast_days != 1:
        raise ValueError("本模块只输出 D+1 日前电价，预测天数必须为 1")
    return [python_executable(), str(paths.guangdong_price_forecast), "--input", str(input_path),
            "--output-dir", str(output_dir), "--as-of", run_date, "--forecast-days",
            str(forecast_days), "--training-months", str(training_months)]
