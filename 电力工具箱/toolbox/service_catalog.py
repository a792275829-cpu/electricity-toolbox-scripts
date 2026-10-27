"""Desktop forms; labels and defaults mirror the existing Tk pages."""
from datetime import date, timedelta
from pathlib import Path
from .catalog import default_catalog


def field(key, label, kind="text", default="", **extra):
    return dict(key=key, label=label, kind=kind, default=default, **extra)


def desktop_catalog(paths):
    today = date.today()
    day = today.isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    downloads = str(Path.home() / "Downloads")
    work = Path.home() / "Desktop/日常工作"
    f = field
    workbook = lambda key, label, name="": f(key, label, "file", str(work / name) if name else "", extensions=["xlsx"])
    backup = f("create_backup", "写入前生成时间戳备份", "boolean", False)
    forms = {
        "online-energy": ([f("run_date", "导出日期", "date", yesterday)], [("run", "开始导出"), ("login", "登录")]),
        "wind-forecast": ([f("run_date", "运行日期", "date", tomorrow)], [("run", "生成截图"), ("login", "登录")]),
        "section-analysis": ([f("files", "节点电价文件（支持批量）", "files", [], extensions=["xlsx"]),
                              f("topology_path", "拓扑名单", "file", str(paths.section_analysis_topology), extensions=["md"]),
                              f("output_root", "结果目录", "directory", downloads), f("threshold", "价差阈值（元/MWh，严格大于）", "number", 80)], [("run", "开始分析")]),
        "trade-analysis": ([f("files", "出清 Excel（支持批量）", "files", [], extensions=["xlsx"]),
                            f("output_dir", "输出目录", "directory", downloads)], [("run", "开始分析")]),
        "market-table": ([f("run_date", "基准日期 D", "date", day), workbook("workbook", "市场 Excel")], [("run", "开始更新"), ("login", "登录")]),
        "transaction-info": ([workbook("workbook", "目标工作簿", "交易系统信息收集20260901.xlsx"),
                              f("start_date", "日前开始日期（留空接续）", "date"), f("end_date", "日前结束日期", "date", day), backup], [("run", "开始更新")]),
        "power-benefit": ([workbook("guihu_workbook", "归湖工作簿", "功率预测调整收益统计-归湖.xlsx"), f("guihu_selected", "更新归湖", "boolean", True),
                           workbook("tuolian_workbook", "鮀莲工作簿", "功率预测调整收益统计-鮀莲(2)(2).xlsx"), f("tuolian_selected", "更新鮀莲", "boolean", True),
                           workbook("haifeng_workbook", "海风工作簿", "海风博弈收益统计-新.xlsx"), f("haifeng_selected", "更新海风（覆盖五列）", "boolean", False),
                           f("start_date", "开始日期（海风覆盖必填）", "date"), f("end_date", "截止日期 D", "date", day),
                           f("create_backup", "写入前生成时间戳备份", "boolean", True)], [("run", "抓取并更新")]),
        "spot-price": ([f("start_date", "开始日期", "date", yesterday), f("end_date", "结束日期", "date", day),
                        *[item for key, label, name in [
                            ("guihu", "归湖", "2026年现货价格分析（2026.8.14更新）-归湖.xlsx"),
                            ("tuolian", "鮀莲", "2026年现货价格分析（2026.8.14更新）-鮀莲.xlsx"),
                            ("haifeng", "海风", "2026年现货价格分析-海风.xlsx")]
                          for item in (workbook(key + "_workbook", label + "工作簿", name), f(key + "_selected", "更新" + label, "boolean", True))],
                        f("overwrite", "覆盖已有电价", "boolean", True), backup], [("run", "开始更新")]),
        "market-report": ([f("report_date", "报告日期", "date", tomorrow), workbook("day_report", "d 日收益表"),
                           workbook("previous_report", "上一日对比表（必选）"), workbook("d2_report", "d-2 收益表"),
                           f("manual_market", "市场补录（网站优先，手填补缺）", "json")], [("run", "生成日报")]),
        "report": ([f("report_date", "报告日期", "date", day), workbook("online_workbook", "前一日 Excel（上网电量）"),
                    workbook("day_ahead_workbook", "报告日 Excel（日前电量/价格）"), workbook("daily_workbook", "前两日 Excel（日清数据）"),
                    f("template_report", "Word 模板（可选）", "file", extensions=["docx"]),
                    f("fetch_data", "抓取网站数据并汇总 Excel", "boolean", True), f("generate_word", "生成 Word 报告", "boolean", True)], [("run", "开始生成")]),
        "daily-pdf-parser": ([f("pdf_path", "运行日报 PDF", "file", extensions=["pdf"]), f("output_path", "输出 Excel（留空按源文件命名）", "save", extensions=["xlsx"]),
                              f("mode", "执行功能", "select", "both", choices=[["both", "解析并上传"], ["parse", "仅解析"], ["upload", "仅上传"]])], [("run", "开始执行")]),
        "summary-upload": ([f("mode", "执行功能", "select", "both", choices=[["summary", "仅汇总"], ["upload", "仅上传"], ["both", "汇总并上传"]]),
                            f("input_dir", "复盘文件夹", "directory", str(Path.home() / "Desktop/复盘" / f"{(today + timedelta(days=1)).month}-{(today + timedelta(days=1)).day}"))], [("preview", "预览上传文件"), ("run", "开始执行")]),
        "group-upload": ([f("files", "能销数据；也可同时选择省内数据", "files", [], extensions=["xlsx", "xls"]),
                          f("force", "已上传数据也覆盖", "boolean", False)], [("run", "开始上传"), ("login", "登录")]),
        "guangdong-price": ([f("run_date", "基准日期 D", "date", day), workbook("master_workbook", "数据总表（留空使用现有配置）"),
                             f("prediction_output_dir", "预测输出目录（留空使用现有配置）", "directory"), f("training_months", "滚动训练月数（0 自动，或至少 2）", "number", 0)], [("update", "补齐数据总表"), ("predict", "运行 D+1 预测")]),
        "wps-writer": ([f("config", "写入配置", "json", ""),
                        f("selected_indexes", "执行配置序号（从 1 开始，用逗号分隔）", default="1"),
                        f("operation", "在线文档读取参数", "json", "{}")], [("preview", "预览"), ("run", "写入并回读"), ("login", "登录 WPS"), ("status", "刷新登录状态"), ("inspect", "读取文档信息")]),
    }
    for tool_id in ("spot-price", "transaction-info"):
        forms[tool_id][1].append(("login", "登录"))
    notes = {
        "section-analysis": "相邻节点一涨一跌＋价差严格大于阈值＋全天累计至少4个时点。",
        "transaction-info": "来源口径：日前 D、实际 D-3；目标 Sheet1，沿用现有字段顺序与接续规则。",
        "power-benefit": "归湖/鮀莲：C 原始功率；J 申报功率；K 日前电量；L 日前电价；M 上网电量；N 实时电价。0–23 点，非空（含 0、公式）跳过。海风：C 原始功率、R 申报功率、N 上网电量、M 日前电价、O 实时电价；覆盖所选日期内五列的值及公式，开始日期必填。",
        "report": "三个 Excel 来源日期依次为 D-1、D、D-2。",
    }
    return [dict(id=t.tool_id, name=t.name, category=t.category, description=t.description,
                 fields=forms[t.tool_id][0], actions=[dict(id=k, label=v) for k, v in forms[t.tool_id][1]],
                 notes=notes.get(t.tool_id, "")) for t in default_catalog()]
