"""Offline acceptance checks executed using the bundled Windows runtimes."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def run(root: Path) -> int:
    report = {"time": datetime.now().isoformat(), "system": platform.platform(), "checks": []}

    def check(name, action):
        try:
            detail = action()
            report["checks"].append({"name": name, "passed": True, "detail": detail})
            print(f"[PASS] {name}: {detail}", flush=True)
        except Exception as exc:
            report["checks"].append({"name": name, "passed": False, "detail": str(exc)})
            print(f"[FAIL] {name}: {exc}", flush=True)

    def runtime():
        if sys.platform != "win32" or sys.maxsize <= 2**32:
            raise RuntimeError("本自检需在 Windows x64 运行")
        if Path(sys.executable).resolve().parent != root / "runtime" / "python311":
            raise RuntimeError("没有使用便携包内置 Python")
        return sys.version.split()[0]

    check("Windows x64 / bundled Python", runtime)
    for module in ("tkinter", "openpyxl", "docx", "pandas", "numpy", "sklearn", "scipy",
                   "cryptography", "pdfplumber", "pypdfium2", "pypdf", "PIL", "jinja2", "playwright"):
        check(f"import {module}", lambda module=module: str(importlib.import_module(module).__file__))

    def command(args, *, cwd=root):
        completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                   encoding="utf-8", timeout=180)
        if completed.returncode:
            raise RuntimeError(f"exit={completed.returncode}\n{completed.stderr[-2000:]}")
        return completed.stdout.strip()[-1000:] or completed.stderr.strip()[-1000:] or "OK"

    node = root / "runtime" / "node" / "node.exe"
    uploader = root / "电力工具脚本" / "private-data-uploader-tool"
    check("bundled Node", lambda: command([str(node), "--version"]))
    check("Node Playwright", lambda: command([str(node), "-e",
          "console.log(require('playwright/package.json').version)"], cwd=uploader))

    def pages():
        from toolbox.app import ToolboxApp
        from toolbox.catalog import default_catalog

        with tempfile.TemporaryDirectory(prefix="toolbox-ui-") as directory:
            app = ToolboxApp(workspace=Path(directory))
            app.withdraw()
            try:
                for item in default_catalog():
                    app.show_page(item.name)
                    app.update_idletasks()
                    assert app.pages[item.name].__class__.__name__ == item.page_class
                return f"{len(default_catalog())} pages"
            finally:
                app.destroy()

    check("Tk / all toolbox pages", pages)

    def browser():
        from playwright.sync_api import sync_playwright
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory(prefix="toolbox-browser-") as directory:
            pdf = Path(directory) / "test.pdf"
            with sync_playwright() as playwright:
                with playwright.chromium.launch() as owned:
                    page = owned.new_page()
                    page.set_content("<html><meta charset='utf-8'><body>电力工具箱离线自检</body></html>")
                    assert page.inner_text("body") == "电力工具箱离线自检"
                    page.pdf(path=str(pdf))
            assert len(PdfReader(pdf).pages) == 1
            return "offline browser / PDF readback OK"

    check("Chromium / PDF", browser)
    modules = ["test_windows_portable", "test_market_table_batch_write", "test_spot_price_update",
               "test_summary_atomic_save", "test_group_upload_shutdown", "test_report_pdf_browser",
               "test_wps_async_dialog"]
    for module in modules:
        check(module, lambda module=module: command(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", module + ".py"],
            cwd=root / "电力工具箱"))
    check("wind forecast / Chinese font rendering", lambda: command(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_wind_forecast_screenshot.py"],
        cwd=root / "风力预测截图"))
    check("Node login-state isolation", lambda: command([
        str(node), str(root / "电力工具箱" / "tests" / "test_private_uploader_credentials.mjs")]))
    report["passed"] = all(item["passed"] for item in report["checks"])
    destination = root / "logs" / "windows-self-test.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {destination}\nPassed: {report['passed']}", flush=True)
    return 0 if report["passed"] else 1

