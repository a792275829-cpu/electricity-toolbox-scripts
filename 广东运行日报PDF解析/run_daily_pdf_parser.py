#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from gd_pdf_tool import parse_pdf, write_quote_excel
from gd_pdf_tool.uploader import build_upload_plan, upload_report_files, verify_quote_workbook


def default_output_path(pdf_path: str | Path, report_date: str) -> Path:
    source = Path(pdf_path).expanduser().resolve()
    suffix = report_date if report_date != "unknown" else source.stem
    return source.parent / f"现货运行日报报价容量区间{suffix}.xlsx"


def convert_pdf_to_excel(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    baseline_capacity: float | None = None,
    logger=print,
) -> Path:
    source = Path(pdf_path).expanduser().resolve()
    logger(f"正在解析：{source.name}")
    result = parse_pdf(source, baseline_capacity)
    output = (
        Path(output_path).expanduser().resolve()
        if output_path
        else default_output_path(source, result.report_date)
    )
    written = write_quote_excel(result.rows, output)
    verify_quote_workbook(written, result.rows)
    logger(f"报告日期：{result.report_date}")
    logger(f"申报容量基准值：{result.baseline_capacity:.4f} MW")
    if result.diagnostics.get("declaration_info_source") == "image-labels":
        logger("申报信息来源：图片标签识别 + PDF 原始数值")
    logger(f"报价区间数：{len(result.rows)}")
    logger(f"图表来源：{result.diagnostics.get('chart_image_source', 'unknown')}")
    low_segments = result.diagnostics.get("low_height_segments", [])
    if low_segments:
        pixel_mw = result.diagnostics["scale_mw_per_pixel"]
        logger(
            f"精度提示：{len(low_segments)} 个电源分段不足 3 个原图像素，已按颜色覆盖率估算；"
            f"汇总校准前 1 像素约 {pixel_mw:.1f} MW，细小分段仍受原图分辨率限制。"
        )
    logger(f"已生成：{written}")
    return written


def process_report(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    baseline_capacity: float | None = None,
    *,
    mode: str = "both",
    logger=print,
) -> Path:
    if mode not in ("both", "parse", "upload"):
        raise ValueError(f"不支持的执行功能：{mode}")
    if mode == "upload":
        if output_path is None:
            from gd_pdf_tool.parser import extract_report_date
            output_path = default_output_path(pdf_path, extract_report_date(Path(pdf_path)))
        output = Path(output_path).expanduser().resolve()
    else:
        output = convert_pdf_to_excel(pdf_path, output_path, baseline_capacity, logger=logger)
    if mode != "parse":
        plan = build_upload_plan(pdf_path, output)
        logger(f"上传日期：{plan.report_date.isoformat()}（PDF 报告日期）")
        upload_report_files(plan, logger=logger)
        logger("源 PDF 与报价容量 Excel 均已上传并回读确认。")
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="广东运行日报 PDF 报价容量解析工具")
    parser.add_argument("pdf", help="广东电力现货市场运行日报 PDF")
    parser.add_argument("-o", "--output", help="输出 Excel 路径")
    parser.add_argument("--baseline", type=float, help="手动指定申报容量基准值")
    parser.add_argument(
        "--mode", choices=("both", "parse", "upload"), default="both",
        help="both=解析并上传（默认），parse=仅解析，upload=上传源 PDF 和已有 Excel",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    process_report(args.pdf, args.output, args.baseline, mode=args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
