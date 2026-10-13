"""A4 natural pagination of the concise daily report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import Browser
from gd_power_report.pdf_export import pdf_browser
from pypdf import PdfReader

from gd_power_report.legacy_concise_report import _sync_metric_card_heights, prepare_legacy_concise_context
from gd_power_report.print_tables import split_print_table_blocks


A4_WIDTH_PT = 595.28
A4_HEIGHT_PT = 841.89


def prepare_print_context(report_data: dict[str, Any]) -> dict[str, Any]:
    context = prepare_legacy_concise_context(report_data)
    context["day_blocks"] = split_print_table_blocks(context["day_blocks"])
    return context


def render_print_report(output_dir: Path, report_data: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    context = prepare_print_context(report_data)
    template_dir = Path(__file__).parent / "templates"
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = environment.get_template("legacy_print_report.html.j2")
    analysis_date = context["analysis_date"]
    path = output_dir / (
        f"{analysis_date.isoformat()}_南方分公司电力市场日报_打印版.html"
    )
    rendered = template.render(**context)
    path.write_text(
        "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n",
        encoding="utf-8",
    )
    return path


def export_print_pdf(html_path: str | Path, *, browser: Browser | None = None) -> Path:
    source = Path(html_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".html":
        raise ValueError(f"打印版HTML不存在或格式不正确：{source}")
    output = source.with_suffix(".pdf")
    with pdf_browser(browser) as renderer:
        page = None
        try:
            page = renderer.new_page(viewport={"width": 794, "height": 1123})
            page.emulate_media(media="print")
            page.goto(source.as_uri(), wait_until="networkidle")
            _sync_metric_card_heights(page)
            page.pdf(
                path=str(output),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "12mm", "right": "10mm", "bottom": "14mm", "left": "10mm"},
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=(
                    '<div style="width:100%;text-align:center;font-size:9px;'
                    'font-family:PingFang SC,sans-serif;color:#60738f;">'
                    '内部资料｜第<span class="pageNumber"></span>页 / '
                    '共<span class="totalPages"></span>页</div>'
                ),
            )
        finally:
            if page is not None:
                page.close()
    reader = PdfReader(str(output))
    if not reader.pages:
        raise ValueError("A4打印版没有生成有效页面")
    for index, pdf_page in enumerate(reader.pages, 1):
        width = float(pdf_page.mediabox.width)
        height = float(pdf_page.mediabox.height)
        if abs(width - A4_WIDTH_PT) > 1 or abs(height - A4_HEIGHT_PT) > 1:
            raise ValueError(
                f"打印版第{index}页尺寸异常：{width:.2f}×{height:.2f}pt"
            )
    return output
