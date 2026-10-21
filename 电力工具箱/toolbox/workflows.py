"""GUI-independent validation and execution of the toolbox's existing tools."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from toolbox_dates import normalize_date_input, parse_date_input
from toolbox_storage import data_path
from . import commands
from .file_rules import default_output_for_pdf
from .runtime import load_module, node_executable, python_executable
from .service_catalog import desktop_catalog


def validate(paths, tool_id, action, values):
    tool = next((t for t in desktop_catalog(paths) if t["id"] == tool_id), None)
    if tool is None or action not in [a["id"] for a in tool["actions"]]:
        raise ValueError("未知工具或操作")
    if not isinstance(values, dict):
        raise ValueError("参数必须是对象")
    fields = {f["key"]: f for f in tool["fields"]}
    if set(values) - fields.keys():
        raise ValueError("包含未知参数")
    p = {k: values.get(k, f["default"]) for k, f in fields.items()}
    for key, f in fields.items():
        value = p[key]
        if f["kind"] == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{f['label']}必须为布尔值")
        elif f["kind"] == "files":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{f['label']}必须为文件列表")
        elif not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError(f"{f['label']}格式错误")
        if f["kind"] == "select" and value not in [v[0] for v in f["choices"]]:
            raise ValueError(f"{f['label']}选项无效")
        if f["kind"] == "date" and value:
            p[key] = normalize_date_input(str(value))
    if p.get("start_date") and p.get("end_date") and p["start_date"] > p["end_date"]:
        raise ValueError("结束日期不能早于开始日期")
    # Building the execution plan validates all inputs before a task is accepted.
    plan(paths, tool_id, action, p)
    return p


def required_path(p, key, *, directory=False, exists=True):
    value = str(p.get(key, "")).strip()
    if not value:
        raise ValueError(f"请选择或填写：{key}")
    path = Path(value).expanduser().resolve()
    if exists and not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"文件或目录不存在：{path}")
    return path


def selected_files(p):
    if not p.get("files"):
        raise ValueError("请至少选择一个文件")
    return [required_path({"file": v}, "file") for v in p["files"]]


def number(value, label, minimum=0):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label}必须是数字") from None
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{label}必须是不小于 {minimum} 的有限数值")
    return result


def plan(paths, tool_id, action, p):
    """Return existing CLI invocations, or validate an existing Python workflow."""
    py = python_executable()
    if action == "login" and tool_id != "wps-writer":
        script = {"online-energy": paths.online_energy, "wind-forecast": paths.wind_forecast,
                  "market-table": paths.market_table_update, "group-upload": paths.group_upload,
                  "spot-price": paths.spot_price_update, "transaction-info": paths.transaction_info_update}[tool_id]
        return [([py, str(script), "--login", *(["--headed"] if tool_id in {"market-table", "spot-price", "transaction-info"} else [])], script.parent)]
    day = lambda k: parse_date_input(str(p.get(k, "")))
    optional_start = day("start_date") if p.get("start_date") else None
    if tool_id in {"online-energy", "wind-forecast"}:
        script = paths.online_energy if tool_id == "online-energy" else paths.wind_forecast
        return [(commands.dated_script(script, normalize_date_input(str(p["run_date"]))), script.parent)]
    if tool_id == "section-analysis":
        files = selected_files(p)
        topology = required_path(p, "topology_path")
        output = required_path(p, "output_root", exists=False)
        threshold = number(p["threshold"], "价差阈值")
        return [(commands.section_analysis(paths, files, output, topology, threshold), paths.section_analysis_dir)]
    if tool_id == "trade-analysis":
        selected_files(p)
        required_path(p, "output_dir", exists=False)
    elif tool_id == "market-table":
        return [(commands.market_table(paths, day("run_date"), required_path(p, "workbook")), paths.market_table_update_dir)]
    elif tool_id == "transaction-info":
        return [(commands.transaction_info(paths, required_path(p, "workbook"), day("end_date"),
                start_date=optional_start, create_backup=p["create_backup"]), paths.transaction_info_update_dir)]
    elif tool_id == "power-benefit":
        workbooks = [required_path(p, key + "_workbook") for key in ("guihu", "tuolian", "haifeng") if p[key + "_selected"]]
        if not workbooks:
            raise ValueError("请至少选择一个工作簿")
        for path in workbooks:
            if path.suffix.lower() != ".xlsx" or sum(station in path.name for station in ("归湖", "鮀莲", "海风")) != 1:
                raise ValueError("文件名必须包含且仅包含归湖、鮀莲或海风中的一个名称，并使用 .xlsx 格式")
        if any("海风" in path.name for path in workbooks) and optional_start is None:
            raise ValueError("海风覆盖更新必须填写开始日期")
        return [(commands.power_benefit(paths, workbooks, day("end_date"), start_date=optional_start,
                create_backup=p["create_backup"]), paths.power_benefit_update_dir)]
    elif tool_id == "spot-price":
        selected = [(k, v) for k, v in [("guihu", "归湖"), ("tuolian", "鮀莲"), ("haifeng", "海风")] if p[k + "_selected"]]
        workbooks = {label: required_path(p, key + "_workbook") for key, label in selected}
        if len(set(workbooks.values())) != len(workbooks):
            raise ValueError("已选场站不能选择同一个工作簿")
        return [(commands.spot_price(paths, day("start_date"), day("end_date"), workbooks,
                overwrite=p["overwrite"], create_backup=p["create_backup"], selected=tuple(workbooks)), paths.spot_price_update_dir)]
    elif tool_id == "daily-pdf-parser":
        source = required_path(p, "pdf_path")
        if source.suffix.lower() != ".pdf":
            raise ValueError("请选择有效的 PDF 文件")
        output = required_path(p, "output_path", exists=False) if p["output_path"] else default_output_for_pdf(source)
        output = output.with_suffix(".xlsx")
        if source == output:
            raise ValueError("输出不能覆盖输入")
        if p["mode"] == "upload" and not output.is_file():
            raise ValueError("仅上传需要已生成的报价容量 Excel")
        p["output_path"] = str(output)
        return [(commands.daily_pdf(paths, source, output, p["mode"]), paths.daily_pdf_parser_dir)]
    elif tool_id == "market-report":
        sources = [required_path(p, key) for key in ("day_report", "previous_report", "d2_report")]
        command = [py, str(paths.market_report_runner), day("report_date").isoformat(),
                   "--day-report", str(sources[0]), "--previous-report", str(sources[1]), "--d2-report", str(sources[2])]
        if str(p["dongguan_operation"]).strip():
            command.extend(("--dongguan-operation", str(p["dongguan_operation"]).strip()))
        if str(p["realtime_maintenance"]).strip():
            command.extend(("--realtime-maintenance", str(number(p["realtime_maintenance"], "实时检修容量"))))
        return [(command, paths.market_report_dir)]
    elif tool_id == "report":
        command_list = []
        report_day = day("report_date").isoformat()
        if p["fetch_data"]:
            command_list.append(([py, str(paths.report_scripts_dir / "fetch_daily_data.py"), report_day], paths.report_scripts_dir))
        if p["generate_word"]:
            sources = [required_path(p, k) for k in ("online_workbook", "day_ahead_workbook", "daily_workbook")]
            template = required_path(p, "template_report") if p["template_report"] else None
            command_list.append((commands.word_report(paths, report_day, *sources, template), paths.report_scripts_dir))
        if not command_list:
            raise ValueError("请至少选择一个任务")
        return command_list
    elif tool_id == "guangdong-price":
        normalize_date_input(str(p["run_date"]))
        master = required_path(p, "master_workbook", exists=False) if p["master_workbook"] else paths.guangdong_price_master_workbook
        p["master_workbook"] = str(master)
        if master.suffix.lower() != ".xlsx":
            raise ValueError("数据总表必须是 .xlsx 文件")
        if action == "predict":
            required_path(p, "master_workbook")
            months = number(p["training_months"], "训练月数")
            if not months.is_integer() or (months != 0 and months < 2):
                raise ValueError("训练窗口填 0 自动选择，或填写至少 2 个月")
            output = required_path(p, "prediction_output_dir", exists=False) if p["prediction_output_dir"] else paths.guangdong_prediction_output_dir
            return [(commands.prediction(paths, master, output, 1, int(months), p["run_date"]), paths.guangdong_price_dir)]
    elif tool_id == "summary-upload":
        source = required_path(p, "input_dir", directory=True)
        output = required_path(p, "output_file", exists=False) if p["output_file"] else source / f"{source.name}_汇总.xlsx"
        if output.suffix.lower() != ".xlsx":
            raise ValueError("汇总输出必须是 .xlsx 文件")
        if p["mode"] == "both" and source in output.parents and output.parent != source:
            raise ValueError("汇总文件请保存在复盘目录根目录或目录之外")
        p["output_file"] = str(output)
        if action == "preview" or p["mode"] != "summary":
            node_executable()
    elif tool_id == "group-upload":
        selected_files(p)
    elif tool_id == "wps-writer":
        if p.get("config"):
            config = json.loads(str(p["config"]))
            if not isinstance(config, dict):
                raise ValueError("WPS 配置必须是对象")
        if action in {"run", "preview"}:
            indexes = [int(v.strip()) - 1 for v in str(p["selected_indexes"]).split(",")]
            if not indexes or min(indexes) < 0 or len(indexes) != len(set(indexes)):
                raise ValueError("配置序号必须是不重复的正整数")
    return []


def execute(paths, tool_id, action, p):
    """Run in an owned worker process; its children share its process group."""
    command_list = plan(paths, tool_id, action, p)
    for command, cwd in command_list:
        subprocess.run(command, cwd=cwd, check=True)
    if command_list:
        outputs = []
        if tool_id == "daily-pdf-parser":
            if not Path(p["output_path"]).is_file():
                raise RuntimeError("进程已结束，但未找到输出 Excel")
            outputs = [p["output_path"]]
        if tool_id == "section-analysis":
            outputs = [str(Path(p["output_root"]) / f"{Path(v).stem}_500kV分析.html") for v in p["files"]]
            if not all(Path(v).is_file() for v in outputs):
                raise RuntimeError("分析进程已结束，但部分分析网页未生成，请核对日志")
        directories = {
            "online-energy": data_path(paths.online_energy_dir / "输出"),
            "wind-forecast": paths.wind_forecast_output_dir,
            "report": data_path(paths.report_root / "输出"),
            "market-report": paths.market_report_output_dir,
        }
        if tool_id in directories and directories[tool_id].exists():
            outputs.append(str(directories[tool_id]))
        if tool_id in {"market-table", "transaction-info"} and action == "run":
            outputs.append(str(Path(p["workbook"]).expanduser()))
        return dict(message="任务已完成，请核对日志及输出文件", outputs=outputs)
    log = lambda text: print(str(text), flush=True)
    if tool_id == "trade-analysis":
        module = load_module("toolbox_trade_analysis", paths.trade_analysis)
        output_dir = Path(p["output_dir"]).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for source in selected_files(p):
            output = output_dir / f"{source.stem}_分析报告.html"
            module.generate_report(source, out_path=output)
            log(f"完成：{source.name} -> {output.name}")
            outputs.append(str(output))
        return dict(outputs=outputs)
    if tool_id == "group-upload":
        from .upload_service import run_group_upload_with_staging
        module = load_module("toolbox_group_upload", paths.group_upload)
        module.LOG_SINK = log
        run_group_upload_with_staging(module, selected_files(p), force=p["force"], on_log=log)
    elif tool_id == "guangdong-price":
        module = load_module("toolbox_guangdong_data_update", paths.guangdong_data_update)
        result = module.update_master_workbook(run_date=p["run_date"], master_workbook=Path(p["master_workbook"]))
        return dict(outputs=[str(v) for v in result.values()], values={k: str(v) for k, v in result.items()})
    elif tool_id == "summary-upload":
        from .review_classification import prepare_review_folder, missing_review_stations
        source = Path(p["input_dir"]).expanduser()
        output = Path(p["output_file"]).expanduser()
        from .tasks import CancellationToken
        prepared = prepare_review_folder(source, logger=log, token=CancellationToken(), excluded=(output,))
        missing = missing_review_stations(prepared)
        outputs = []
        if action != "preview" and p["mode"] != "upload":
            module = load_module("toolbox_summary", paths.summary)
            result = module.run_summary(input_dir=prepared, output_path=output, logger=log)
            outputs.append(str(result))
        if action == "preview" or p["mode"] != "summary":
            subprocess.run([node_executable(), str(paths.private_uploader), "--plan" if action == "preview" else "--execute",
                            "--source", str(prepared)], cwd=paths.private_uploader_dir, check=True)
        return dict(outputs=outputs, missing=list(missing))
    elif tool_id == "wps-writer":
        return execute_wps(paths, action, p, log)
    return dict(message="任务已完成")


def execute_wps(paths, action, p, log):
    module = load_module("toolbox_wps_writer", paths.wps_writer)
    operation = json.loads(p.get("operation", "{}"))
    if action == "inspect":
        method = operation.get("method")
        if method not in {"read_online_document_info", "read_online_ranges"}:
            raise ValueError("不支持的在线文档读取操作")
        arguments = operation.get("arguments", {})
        allowed = {"browser_mode", "cdp_url"} | ({"requests"} if method == "read_online_ranges" else {"url"})
        if not isinstance(arguments, dict) or set(arguments) - allowed:
            raise ValueError("在线文档读取参数无效")
        return getattr(module, method)(**arguments, log=log)
    config = module.normalize_runtime_config(json.loads(p["config"]) if p.get("config") else module.load_config())
    mode, cdp = config["browser_mode"], config["cdp_url"]
    if action == "status":
        status = module.check_wps_login_status(mode, cdp, log)
        return dict(message=status.message, state=status.state)
    if action == "login":
        result = module.ensure_wps_dedicated_chrome(cdp, log, headless=False,
            initial_url=operation.get("initial_url") or "https://www.kdocs.cn/latest", preload_url=True)
        status = module.check_wps_login_status(mode, cdp, log)
        return dict(message=status.message, state=status.state, started=result.get("started", False))
    mappings = []
    indexes = [int(v.strip()) - 1 for v in p["selected_indexes"].split(",")]
    for index in indexes:
        if index >= len(config["configs"]):
            raise ValueError("配置序号超出范围")
        row = config["configs"][index]
        for region in row["regions"]:
            mappings.append(module.MappingRow(**region, kdocs_url=row["kdocs_url"], local_file=row["local_file"],
                            source_type=row["source_type"], source_url=row["source_url"]))
    prepared = module.prepare_mappings(mappings, browser_mode=mode, cdp_url=cdp, log=log)
    if not prepared:
        raise ValueError("所选配置没有写入区域")
    if action == "preview":
        return dict(message=f"共 {len(prepared)} 个写入区域，涉及 {len({v.kdocs_url for v in prepared})} 份目标表格。", regions=[dict(index=v.index, sheet=v.target_sheet, range=v.target_range, values=v.values) for v in prepared])
    groups = {}
    for item in prepared:
        groups.setdefault(item.kdocs_url, []).append(item)
    for url, group in groups.items():
        result = module.write_to_wps(url, group, log, browser_mode=mode, cdp_url=cdp)
        rows = {entry["index"]: entry for entry in result.get("results", [])}
        if not result.get("saved"):
            raise RuntimeError("WPS 未确认保存成功")
        for item in group:
            if item.index not in rows or module.compare_matrix(item.values, rows[item.index].get("value"), item.rows, item.cols):
                raise RuntimeError(f"回读不一致：{item.target_sheet}!{item.target_range}")
    return dict(message=f"{len(prepared)} 个区域已保存，回读核对一致")
