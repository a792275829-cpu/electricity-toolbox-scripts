"""Toolbox entry point for the two retained market report PDFs."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
import sys

WORKSPACE = Path(__file__).resolve().parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
from toolbox_shared.dates import parse_date_input

from gd_power_report.company_clearing_xlsx import (
    infer_day_ahead_comparison_date,
    infer_report_date,
    validate_day_ahead_comparison_workbook,
    validate_report_workbook,
)
from gd_power_report.config import AppConfig
from gd_power_report.pipeline import run_pipeline
from gd_power_report.workbook_reader import reuse_workbook_inputs


@reuse_workbook_inputs
def main() -> int:
    parser = argparse.ArgumentParser(description="生成南方分公司电力市场日报 PDF")
    parser.add_argument("report_date", type=parse_date_input)
    parser.add_argument("--day-report", type=Path, required=True)
    parser.add_argument("--previous-report", type=Path, required=True)
    parser.add_argument("--d2-report", type=Path, required=True)
    parser.add_argument("--dongguan-operation")
    parser.add_argument("--realtime-maintenance", type=float)
    parser.add_argument("--manual-market", type=json.loads, help="按报告日期绑定的市场补值 JSON")
    args = parser.parse_args()

    day_source = validate_report_workbook(args.day_report)
    previous_source = validate_day_ahead_comparison_workbook(args.previous_report)
    d2_source = validate_report_workbook(args.d2_report)
    for label, actual, expected in (
        ("D 日收益表", infer_report_date(day_source), args.report_date),
        ("上一日对比表", infer_day_ahead_comparison_date(previous_source), args.report_date - timedelta(days=1)),
        ("D-2 收益表", infer_report_date(d2_source), args.report_date - timedelta(days=2)),
    ):
        if actual != expected:
            parser.error(f"{label}日期为 {actual}，应为 {expected}")

    config = AppConfig.from_workspace(Path(__file__).resolve().parent.parent)
    result = run_pipeline(
        config,
        args.report_date,
        day_source,
        print,
        comparison_clearing_xlsx_path=previous_source,
        realtime_detail_xlsx_path=d2_source,
        dongguan_operation_override=args.dongguan_operation,
        realtime_maintenance_capacity=args.realtime_maintenance,
        manual_market=args.manual_market,
    )
    print(f"手机预览版：{result.preview_pdf_path}")
    print(f"打印版：{result.print_pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
