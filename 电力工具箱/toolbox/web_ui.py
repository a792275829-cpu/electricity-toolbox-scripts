"""Headless adapters for the React UI; business execution stays in ToolboxService."""
from __future__ import annotations
from dataclasses import asdict
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path

from toolbox_shared.account_verifier import AccountVerifier
from toolbox_shared.credentials import CredentialStore
from toolbox_shared.dates import normalize_date_input, parse_date_input
from toolbox_shared.storage import data_path
from .runtime import load_module
from .file_dialogs import FileDialogState
from .file_rules import default_output_for_pdf


class WebUI:
    def __init__(self, service):
        self.service = service
        self.paths = service.paths
        self.store = CredentialStore(self.paths.workspace)
        self.verifier = AccountVerifier(self.store)
        self.dialogs = FileDialogState(self.paths.file_dialog_settings)

    def call(self, method, params):
        if method == "ui.bootstrap":
            catalog = self.service.call("catalog", {})
            overrides = {
                "market-table": {"workbook": str(Path.home()/"Desktop/日常工作/现货统调市场出清、结算数据2026.xlsx")},
                "report": {"online_workbook":"", "day_ahead_workbook":"", "daily_workbook":"", "template_report":""},
                "summary-upload": {"mode":"summary", "input_dir":str(Path.home()/"Desktop/日常工作/复盘"/f"{(date.today()+timedelta(days=1)).month}-{(date.today()+timedelta(days=1)).day}")},
                "guangdong-price": {"master_workbook":str(self.paths.guangdong_price_master_workbook),"prediction_output_dir":str(self.paths.guangdong_prediction_output_dir)},
            }
            initial = {}
            for tool in catalog:
                tool_id = tool["id"]
                values = initial.setdefault(tool_id, {})
                for field in tool["fields"]:
                    key = field["key"]
                    if key in overrides.get(tool_id, {}):
                        field["default"] = overrides[tool_id][key]
                    if tool_id == "private-batch-upload":
                        continue
                    preference = f"{tool_id}.{key}"
                    if field["kind"] == "file":
                        values[key] = self.dialogs.last_file(preference, field["default"])
                    elif field["kind"] == "files":
                        values[key] = self.dialogs.last_files(preference)
                    elif field["kind"] == "save":
                        values[key] = self.dialogs.last_file(preference, field["default"], save=True)
            daily = initial["daily-pdf-parser"]
            if daily["pdf_path"] and not daily["output_path"]:
                daily["output_path"] = str(default_output_for_pdf(daily["pdf_path"]))
            group = initial["group-upload"]
            initial["group-upload"] = self.call(
                "ui.group.inspect", group if group["files"] else
                self.service.call("auto_match", {"tool_id": "group-upload"}),
            )
            directories = {"online-energy":self.service.call("online_energy.settings",{})["output_dir"],"wind-forecast":self.paths.wind_forecast_output_dir,"market-report":self.paths.market_report_output_dir,"report":data_path(self.paths.report_root/"输出")}
            return dict(catalog=catalog, initial=initial, directories={key:str(value) for key,value in directories.items()}, version=os.environ.get("TOOLBOX_APP_VERSION","0.1.35"))
        if method == "ui.credentials.load":
            saved = self.store.load()
            return dict(username=saved.get("username",""),password=saved.get("password",""))
        if method == "ui.credentials.save":
            previous = self.store.load()
            saved = self.store.save(str(params.get("username","")),str(params.get("password","")))
            if previous.get("revision") != saved.get("revision"): self.verifier.cancel()
            return dict(text="账号已保存，待验证",kind="neutral",running=False,retry=True)
        if method == "ui.credentials.verify": return self.verifier.start()
        if method == "ui.credentials.status": return self.verifier.status()
        if method == "ui.credentials.cancel": return self.verifier.cancel()
        if method == "ui.date.normalize": return normalize_date_input(str(params["value"]))
        if method == "ui.requires_credentials":
            from .page_contracts import requires_qingneng
            return requires_qingneng(params["tool_id"],params.get("action","run"),params.get("values",{}))
        if method == "ui.dialog.directory":
            return str(self.dialogs.initial_directory(str(params["key"]),params.get("current")))
        if method == "ui.dialog.remember":
            key = str(params["key"])
            path = params.get("path")
            if params.get("directory"):
                self.dialogs.remember_directory(key, path)
            elif params.get("directory_only") or key.startswith("private-batch-upload."):
                if path:
                    self.dialogs.remember_file(key, path, directory_only=True)
            elif "paths" in params:
                self.dialogs.remember_files(key, params["paths"], chosen=path)
            else:
                self.dialogs.remember_file(
                    key, path,
                    reset_key="daily-pdf-parser.output_path" if key == "daily-pdf-parser.pdf_path" else None,
                )
            return True
        if method == "ui.diagnostics":
            from .diagnostics import diagnose_environment, diagnose_path
            report = diagnose_path(Path(params["path"]), require_write=True) if params.get("path") else diagnose_environment(self.paths)
            return dict(summary=report.summary(),time=report.generated_at.strftime("%H:%M"),errors=len(report.errors),warnings=len(report.warnings),issues=[dict(name=item.name,detail=item.recommendation or item.detail,status=item.status.value) for item in (*report.errors,*report.warnings)])
        if method == "ui.group.inspect":
            paths = [Path(path) for path in params.get("files", [])]
            if not paths: return dict(files=[])
            try:
                module = load_module("toolbox_group_upload", self.paths.group_upload)
                target_date, uploads = module.prepare_upload_files(paths)
                return dict(files=[str(item.path) for item in uploads if item.kind == "能销"],_summary=f"已识别日期：{target_date}；目标省份：广东省",_selection="\n".join(f"{module.UPLOAD_TYPES[item.kind]['name']}：{item.path.name}" for item in uploads))
            except Exception as exc:
                return dict(files=[str(path) for path in paths],_summary=f"文件校验失败：{exc}")
        if method == "ui.daily.output":
            return str(default_output_for_pdf(params["path"]))
        if method == "ui.business.directory":
            text = str(params["path"]).strip()
            if not text: raise ValueError("路径不能为空")
            path = Path(text).expanduser()
            if not path.is_absolute(): path = self.paths.guangdong_price_dir / path
            path = path.resolve()
            return str(path.parent if params.get("workbook") else path)
        if method == "ui.output.directory":
            tool = params["tool_id"]
            values = params.get("values", {})
            directories = {
                "online-energy": lambda: self.service.call("online_energy.settings", {})["output_dir"],
                "wind-forecast": lambda: self.paths.wind_forecast_output_dir,
                "report": lambda: data_path(self.paths.report_root / "输出"),
                "market-report": lambda: self.paths.market_report_output_dir,
            }
            if tool in directories:
                return str(Path(directories[tool]()).expanduser().resolve())
            key = {"guangdong-price":"prediction_output_dir", "summary-upload":"input_dir",
                   "trade-analysis":"output_dir", "section-analysis":"output_root"}.get(tool)
            if key:
                text = str(values.get(key, "")).strip()
                if not text: raise ValueError("请先选择输出目录")
                return str(Path(text).expanduser().resolve())
            if tool == "daily-pdf-parser":
                text = values.get("output_path") or (default_output_for_pdf(values["pdf_path"]) if values.get("pdf_path") else "")
            elif tool in {"transaction-info", "market-table"}:
                text = values.get("workbook", "")
            elif tool in {"power-benefit", "spot-price"}:
                keys = [key for key in ("guihu", "tuolian", "haifeng") if values.get(key + "_selected")]
                keys = keys or ["guihu", "tuolian", "haifeng"]
                text = next((values[key + "_workbook"] for key in keys if values.get(key + "_workbook")), "")
            else:
                raise ValueError("该工具没有独立输出目录")
            if not str(text).strip(): raise ValueError("请先选择工作簿或输出位置")
            return str(Path(text).expanduser().resolve().parent)
        if method == "ui.local.sheets":
            import openpyxl
            book = openpyxl.load_workbook(Path(params["path"]).expanduser(),read_only=True,data_only=True)
            try: return book.sheetnames
            finally: book.close()
        if method in {"ui.market.fields","ui.market.preview","ui.market.report_date"}:
            if method == "ui.market.report_date":
                from .market_report_dates import report_date_from_day_report
                path = str(params.get("path") or "").strip()
                if not path:
                    raise ValueError("请先选择 D 日收益表，报告日期将自动识别。")
                return report_date_from_day_report(Path(path).expanduser()).isoformat()
            module = load_module("web_manual_market",self.paths.workspace/"南方分公司电力市场日报/gd_power_report/manual_market.py")
            if method == "ui.market.fields": return [asdict(field) for field in module.MARKET_FIELDS]
            return module.preview_manual_market(params["data"],parse_date_input(params["date"]))
        if method == "ui.section.editor":
            from toolbox_shared.topology_web import BrowserEditor
            import webbrowser
            if not hasattr(self,"browser") or self.browser.closed:
                self.browser = BrowserEditor(input_path=params.get("input_path") or None)
            if not webbrowser.open_new_tab(self.browser.url): raise ValueError("无法打开默认浏览器，请检查系统默认浏览器设置。")
            return True
        if method == "ui.section.status":
            if not hasattr(self,"browser"): return dict(dirty=False,closed=True)
            import queue
            applied = None
            while True:
                try: applied = str(self.browser.applied.get_nowait())
                except queue.Empty: break
            if applied:
                self.dialogs.remember_file("section-analysis.topology_path", applied)
            return dict(dirty=self.browser.dirty,closed=self.browser.closed,applied=applied)
        if method == "ui.logs.save":
            from toolbox_shared.redaction import redact
            output = data_path(self.paths.workspace/"电力工具箱/logs")/str(params["tool_id"])
            output.mkdir(parents=True,exist_ok=True)
            path = output/(datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".log")
            path.write_text(redact(str(params["text"])),encoding="utf-8")
            return str(path)
        if method == "ui.updates.page":
            import webbrowser
            url = str(params["url"])
            if not url.startswith("https://github.com/a792275829-cpu/electricity-toolbox-scripts/releases"):
                raise ValueError("发布页面地址无效")
            return webbrowser.open_new_tab(url)
        raise ValueError("不支持的界面操作")

    def close(self):
        self.verifier.cancel()
        if hasattr(self,"browser"): self.browser.close()
