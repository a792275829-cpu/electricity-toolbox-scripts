from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import tkinter as tk

from .runtime import TaskRegistry, ToolPaths

PageFactory = Callable[[tk.Misc, ToolPaths, TaskRegistry], tk.Frame]


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    name: str
    category: str
    page_class: str
    description: str

    def factory(self) -> PageFactory:
        class_name = self.page_class

        def build(parent: tk.Misc, paths: ToolPaths, registry: TaskRegistry) -> tk.Frame:
            from . import pages
            return getattr(pages, class_name)(parent, paths, registry)

        return build


def default_catalog() -> tuple[ToolDescriptor, ...]:
    return (
        ToolDescriptor("online-energy", "导出上网电量", "数据采集", "OnlineEnergyPage", "抓取并汇总上网电量"),
        ToolDescriptor("market-table", "市场表更新", "数据采集", "MarketTableUpdatePage", "读取市场数据并更新年度表"),
        ToolDescriptor("guangdong-price", "广东电价预测", "数据采集", "GuangdongPricePage", "抓取广东现货数据并运行 D+1 日前电价预测"),
        ToolDescriptor("trade-analysis", "电力交易分析", "分析与报告", "TradeAnalysisPage", "批量生成交易分析报告"),
        ToolDescriptor("summary", "电量汇总", "分析与报告", "SummaryPage", "汇总日前和实时交易结果"),
        ToolDescriptor("report", "生成报告", "分析与报告", "ReportPage", "生成生产经营情况报告"),
        ToolDescriptor("private-upload", "私有数据上传", "上传与写入", "PrivateUploadPage", "预览并上传私有数据"),
        ToolDescriptor("group-upload", "上传集团每日数据", "上传与写入", "GroupUploadPage", "上传能销和省内日报"),
        ToolDescriptor("wps-writer", "WPS写入工具", "上传与写入", "WpsWriterPage", "写入 WPS/KDocs 表格"),
    )


def catalog_factories() -> dict[str, PageFactory]:
    return {item.name: item.factory() for item in default_catalog()}
