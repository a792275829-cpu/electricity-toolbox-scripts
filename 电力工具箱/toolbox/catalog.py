from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

from .runtime import TaskRegistry, ToolPaths

PageFactory = Callable[["tk.Misc", ToolPaths, TaskRegistry], "tk.Frame"]


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    name: str
    category: str
    page_class: str
    description: str
    page_module: str = ".pages"

    def factory(self) -> PageFactory:
        class_name = self.page_class
        module_name = self.page_module

        def build(parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry) -> tk.Frame:
            module = import_module(module_name, package=__package__)
            return getattr(module, class_name)(parent, paths, registry)

        return build


def default_catalog() -> tuple[ToolDescriptor, ...]:
    return (
        ToolDescriptor("online-energy", "导出上网电量", "数据采集", "OnlineEnergyPage", "抓取并汇总上网电量"),
        ToolDescriptor("wind-forecast", "风力预测截图", "数据采集", "WindForecastPage", "抓取连续7个预测日期并生成网站表格样式截图", page_module=".wind_forecast_page"),
        ToolDescriptor("market-table", "市场表更新", "数据采集", "MarketTableUpdatePage", "读取市场数据并更新年度表"),
        ToolDescriptor("spot-price", "现货价格分析表更新", "数据采集", "SpotPriceUpdatePage", "可选更新归湖、鮀莲、海风日前及实时24点价格", page_module=".spot_price_page"),
        ToolDescriptor("transaction-info", "交易系统信息更新", "数据采集", "TransactionInfoUpdatePage", "按日前 D、实际 D-3 接续更新市场负荷信息", page_module=".transaction_info_page"),
        ToolDescriptor("power-benefit", "功率调整收益统计表更新", "数据采集", "PowerBenefitUpdatePage", "归湖、鮀莲补齐六列；海风按日期覆盖五列24点数据", page_module=".power_benefit_page"),
        ToolDescriptor("guangdong-price", "广东电价预测", "数据采集", "GuangdongPricePage", "抓取广东现货数据并运行 D+1 日前电价预测"),
        ToolDescriptor("summary-upload", "出清结果汇总上传", "数据采集", "SummaryUploadPage", "共用复盘目录，汇总电量或上传出清结果", page_module=".summary_upload_page"),
        ToolDescriptor("daily-pdf-parser", "广东运行日报解析上传", "数据采集", "DailyPdfParserPage", "解析运行日报并上传 PDF 与报价容量 Excel", page_module=".daily_pdf_page"),
        ToolDescriptor("trade-analysis", "出力受限分析", "分析与报告", "TradeAnalysisPage", "批量生成出力受限分析报告"),
        ToolDescriptor("section-analysis", "500kV断面分析", "分析与报告", "SectionAnalysisPage", "清洗节点电价并生成拓扑断面分析网页", page_module=".section_analysis_page"),
        ToolDescriptor("market-report", "南方分公司电力市场日报", "分析与报告", "MarketReportPage", "生成手机预览版与打印版 PDF", page_module=".market_report_page"),
        ToolDescriptor("report", "早报生成", "分析与报告", "ReportPage", "生成生产经营情况报告"),
        ToolDescriptor("group-upload", "上传集团每日数据", "上传与写入", "GroupUploadPage", "上传能销和省内日报"),
        ToolDescriptor("wps-writer", "WPS写入工具", "上传与写入", "WpsWriterPage", "将本地或在线表格数据写入金山文档"),
    )


def catalog_factories() -> dict[str, PageFactory]:
    return {item.name: item.factory() for item in default_catalog()}
