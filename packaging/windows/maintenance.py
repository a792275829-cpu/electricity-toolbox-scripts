"""Local Windows maintenance commands; never upload data or modify an installed app."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs/maintenance"


def environment(root: Path = ROOT) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "TOOLBOX_PORTABLE", "TOOLBOX_WORKSPACE"):
        env.pop(key, None)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(root),
               PLAYWRIGHT_BROWSERS_PATH=str(root / "runtime/ms-playwright"),
               TOOLBOX_CODE_ROOT=str(root), TOOLBOX_DATA_ROOT=str(root / "development/data"),
               TOOLBOX_APP_HOME=str(root / "development"),
               TOOLBOX_PYTHON=str(root / ".venv/Scripts/python.exe"))
    env["PATH"] = os.pathsep.join((str(root / ".venv/Scripts"), str(root / "runtime/node"),
                                    str(Path.home() / ".cargo/bin"), env.get("PATH", "")))
    return env


def python() -> Path:
    result = ROOT / ".venv/Scripts/python.exe"
    if not result.is_file():
        raise RuntimeError("请先运行 01_初始化维护环境.cmd")
    return result


def run(args, *, cwd=ROOT, name=None, timeout=None) -> int:
    """Stream output while retaining the first exception and process exit code."""
    command = [str(x) for x in args]
    if name is None:
        return subprocess.call(command, cwd=cwd, env=environment())
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / f"{name}.log"
    with path.open("w", encoding="utf-8") as log:
        log.write(f"time={datetime.now().isoformat()}\ncommand={command!r}\ncwd={cwd}\n")
        log.flush()
        # Test commands are bounded so a hung Tk process produces a failed report.
        try:
            result = subprocess.run(command, cwd=cwd, env=environment(), stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
            log.write("\nTIMEOUT\n")
        log.write(f"\nexit_code={code}\n")
    print(f"{'PASS' if code == 0 else 'FAIL'} {name}: exit={code}, log={path}", flush=True)
    return code


def verify(root: Path = ROOT) -> dict:
    manifest = json.loads((root / "维护包清单.json").read_text(encoding="utf-8"))
    missing, changed = [], []
    for name, expected in manifest["files"].items():
        relative = Path(name)
        target = root / relative
        if relative.is_absolute() or ".." in relative.parts or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("非法清单路径")
        if not target.is_file():
            missing.append(name)
        else:
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected["sha256"]:
                    changed.append(name)
    return {"passed": not missing and not changed, "checked": len(manifest["files"]),
            "missing": missing, "changed": changed}


def initialize() -> int:
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise RuntimeError("此初始化入口仅供 Windows x64 使用")
    bundled = ROOT / "runtime/python311/python.exe"
    if not bundled.is_file():
        raise RuntimeError("内置 Windows Python 缺失，请重新解压完整维护包")
    target = ROOT / ".venv"
    if not target.exists():
        if run([bundled, "-m", "venv", "--system-site-packages", target], name="initialize", timeout=120):
            return 1
    elif not (target / "Scripts/python.exe").is_file():
        raise RuntimeError("已有 .venv 不是 Windows 环境，请自行备份并移走后再初始化")
    # Keep the pinned runtime read-only; the local venv inherits its packages.
    imports = "import tkinter,cryptography,docx,openpyxl,pandas,playwright,sklearn,PIL,pdfplumber,pypdfium2; print('Python dependencies OK')"
    code = run([python(), "-c", imports], name="dependencies", timeout=120)
    print("初始化完成。数据保存到本文件夹 development/data；移动文件夹后需重新创建 .venv。")
    return code


def doctor() -> int:
    result = {"time": datetime.now().isoformat(), "system": platform.platform(),
              "python": sys.version, "checks": []}
    for module in ("tkinter", "openpyxl", "docx", "pandas", "numpy", "sklearn", "cryptography",
                   "pdfplumber", "pypdfium2", "pypdf", "PIL", "jinja2", "playwright"):
        try:
            start = time.perf_counter()
            importlib.import_module(module)
            row = {"name": module, "passed": True, "ms": round((time.perf_counter() - start) * 1000)}
        except Exception as exc:
            row = {"name": module, "passed": False, "error": str(exc)}
        result["checks"].append(row)
        print(row)
    for tool in ("node", "npm", "git", "cargo", "rustc", "uv"):
        result[tool] = shutil.which(tool)
    result["passed"] = all(row["passed"] for row in result["checks"])
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "environment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("环境报告:", LOGS / "environment.json")
    print("Git/Rust/uv 仅用于开发或打包；不作为 Python 页面测试的通过条件。")
    return 0 if result["passed"] else 1


def browser_test() -> int:
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader

    with tempfile.TemporaryDirectory(prefix="toolbox-maintenance-") as folder:
        output = Path(folder) / "offline.pdf"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content("<meta charset='utf-8'><main>电力工具箱离线测试</main>")
                assert page.inner_text("main") == "电力工具箱离线测试"
                page.pdf(path=str(output))
            finally:
                browser.close()
        assert len(PdfReader(output).pages) == 1
    print("Chromium / 中文文本 / PDF 回读通过")
    return 0


def tests() -> int:
    py = python()
    jobs = [
        ("startup-and-windows", [py, "-m", "unittest", "tests.test_ui_performance",
          "tests.test_catalog_loading", "tests.test_windows_portable", "tests.test_windows_maintenance", "-v"], ROOT / "电力工具箱"),
        ("desktop-python", [py, "-m", "unittest", "discover", "-s", "desktop/tests/python", "-v"], ROOT),
        ("excel-and-browser-regressions", [py, "-m", "unittest", "tests.test_market_table_batch_write",
          "tests.test_spot_price_update", "tests.test_summary_atomic_save", "tests.test_group_upload_shutdown",
          "tests.test_report_pdf_browser", "tests.test_wps_async_dialog", "-v"], ROOT / "电力工具箱"),
        ("task-responsiveness", [py, "-m", "unittest", "tests.test_task_widgets",
          "tests.test_tasks", "tests.test_wps_task_responsiveness", "tests.test_hang_monitor", "-v"], ROOT / "电力工具箱"),
        ("offline-browser", [py, __file__, "browser-test"], ROOT),
        ("node-session-isolation", [ROOT / "runtime/node/node.exe",
          ROOT / "电力工具箱/tests/test_private_uploader_credentials.mjs"], ROOT),
    ]
    checks = [{"name": name, "exit_code": run(command, cwd=cwd, name=name, timeout=240)}
              for name, command, cwd in jobs]
    report = {"time": datetime.now().isoformat(), "checks": checks,
              "passed": all(row["exit_code"] == 0 for row in checks),
              "frontend": "执行 05_准备桌面开发.cmd 后运行桌面 npm test 和 web:build；此处未包含 Rust 安装版测试"}
    (LOGS / "tests.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("测试汇总:", LOGS / "tests.json")
    return 0 if report["passed"] else 1


def export_diagnostics(root: Path = ROOT) -> Path:
    logs = root / "logs/maintenance"
    logs.mkdir(parents=True, exist_ok=True)
    target = root / "logs" / ("Windows维护诊断_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(logs.glob("*")):
            if source.is_file() and not source.is_symlink() and source.suffix in {".log", ".json", ".txt"}:
                archive.write(source, "maintenance/" + source.name)
        manifest = root / "维护包清单.json"
        if manifest.is_file():
            archive.write(manifest, manifest.name)
    return target


def desktop_init() -> int:
    python()
    git = shutil.which("git", path=environment()["PATH"])
    if not git:
        raise RuntimeError("桌面开发需 Git；请按维护说明安装 Git for Windows")
    if not (ROOT / ".git").exists():
        if run([git, "init"]):
            return 1
    (ROOT / "desktop/src-tauri/payload").mkdir(exist_ok=True)
    return run([ROOT / "runtime/node/npm.cmd", "ci"], cwd=ROOT / "desktop")


def build() -> int:
    for tool in ("git", "cargo"):
        if not shutil.which(tool, path=environment()["PATH"]):
            raise RuntimeError(f"缺少 {tool}，请先按维护说明配置 Windows 桌面开发工具")
    if not (ROOT / "desktop/node_modules").is_dir() or not (ROOT / ".git").exists():
        raise RuntimeError("请先执行 05_准备桌面开发.cmd")
    # Reuse the shipped Windows runtime; no cross-platform interpreter copy.
    if run([python(), ROOT / "packaging/tauri/prepare_payload.py", "--target",
            "x86_64-pc-windows-msvc", "--runtime-from", ROOT / "runtime"]):
        return 1
    # A local test installer must not require the production updater private key.
    LOGS.mkdir(parents=True, exist_ok=True)
    override = LOGS / "local-test-build.json"
    override.write_text(json.dumps({"bundle": {"createUpdaterArtifacts": False}}), encoding="utf-8")
    return run([ROOT / "runtime/node/npm.cmd", "run", "tauri", "build", "--",
                "--target", "x86_64-pc-windows-msvc", "--bundles", "nsis", "--config", override], cwd=ROOT / "desktop")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "doctor", "test", "start", "desktop-init", "desktop-dev",
                                          "desktop-test", "build", "verify", "export", "browser-test"))
    action = parser.parse_args().action
    if action == "init": return initialize()
    if action == "verify":
        report = verify()
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / "integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1
    if action == "export":
        print(export_diagnostics())
        return 0
    if action == "doctor":
        first = doctor()
        second = run([python(), __file__, "browser-test"], name="browser-environment", timeout=60)
        return first or second
    if action == "test": return tests()
    if action == "browser-test": return browser_test()
    if action == "start": return run([python(), "-X", "faulthandler", ROOT / "packaging/windows/debug_launch.py"])
    if action == "desktop-init": return desktop_init()
    if action == "desktop-dev":
        return run([ROOT / "runtime/node/npm.cmd", "run", "dev"], cwd=ROOT / "desktop")
    if action == "desktop-test":
        first = run([ROOT / "runtime/node/npm.cmd", "test"], cwd=ROOT / "desktop", name="frontend-tests", timeout=180)
        second = run([ROOT / "runtime/node/npm.cmd", "run", "web:build"], cwd=ROOT / "desktop", name="frontend-build", timeout=180)
        return first or second
    return build()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"维护操作失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
