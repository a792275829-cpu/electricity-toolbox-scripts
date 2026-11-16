"""Build an isolated Tk 9.1 experiment and show the existing export page.

Entry: 00_启动/源码调试版.command --accessibility-probe
The production runtime, venv, accounts and business data are not modified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".toolbox/accessibility"
PYTHON_VERSION = "3.14.6"
TK_VERSION = "9.1.0"
ARCHIVES = {
    "tcl": "4b6efbe457aa10515674b2570e5c2150998104c02a200020393094dc289de935",
    "tk": "23a5509d15f24adc901e10f24e0291108735eba156560812067346f13ebf4a85",
}
SOURCES = {
    "_tkinter.c": "b69ce1c30da3a8fde6d48edc0cf2e5dfa0b506325e36cc6682447e386450af9a",
    "tkappinit.c": "312be438c11b08dd334c9a64149e94aef9ac31d24b7ae9b428c37f93f67821d6",
    "tkinter.h": "229776646f467c4ed9ec1460a14b756bc2de5e5bd70ead431a495d21305f7675",
    "clinic/_tkinter.c.h": "7b7a43ef4badd3adf25a65867433458ab9e94438d07d0aac562abc8f3fe4c8bd",
}


def prepare():
    if sys.platform != "darwin":
        raise RuntimeError("辅助功能验证仅支持 macOS。")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("隔离验证需要 uv；正式版和普通源码启动不需要此步骤。")
    subprocess.run([uv, "python", "install", PYTHON_VERSION], check=True)
    python = subprocess.check_output(
        [uv, "python", "find", "--managed-python", PYTHON_VERSION], text=True
    ).strip()
    CACHE.mkdir(parents=True, exist_ok=True)
    # Reuse the audited Pillow version and wheel hashes, with a separate ABI.
    lock = (ROOT / "packaging/tauri/requirements-macos.lock").read_text()
    pillow = re.search(r"(?m)^pillow==[^\n]*(?:\n[ \t]+[^\n]*)*", lock)
    if pillow is None:
        raise RuntimeError("依赖锁中缺少 Pillow。")
    requirements = CACHE / "requirements.txt"
    requirements.write_text(pillow.group() + "\n")
    dependencies = CACHE / "dependencies"
    subprocess.run([uv, "pip", "install", "--python", python, "--target", str(dependencies),
                    "--no-deps", "--require-hashes", "--only-binary=:all:",
                    "-r", str(requirements)], check=True)
    location = CACHE / "build-directory.txt"
    build = Path(location.read_text().strip()) if location.exists() else None
    if (build is None or not build.is_dir()
            or not build.is_relative_to(Path(tempfile.gettempdir()))):
        build = Path(tempfile.mkdtemp(prefix="electricity-toolbox-tk91-"))
        location.write_text(str(build))
    prefix = build / "install"
    download = runpy.run_path(str(ROOT / "packaging/tauri/prepare_tk.py"))["download"]
    source = CACHE / "source"
    env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": "14.0"}
    log_path = CACHE / "build.log"
    with log_path.open("a") as log:
        if not (prefix / ".built").exists():
            for name, digest in ARCHIVES.items():
                archive = download(
                    f"https://codeload.github.com/tcltk/{name}/tar.gz/refs/tags/core-9-1-0",
                    source / f"{name}-{TK_VERSION}.tar.gz", digest,
                )
                with tarfile.open(archive) as tar:
                    tar.extractall(build, filter="data")
                directory = build / f"{name}-core-9-1-0/unix"
                options = [f"--prefix={prefix}", "--enable-shared"]
                if name == "tk":
                    options += ["--enable-aqua", f"--with-tcl={prefix / 'lib'}"]
                print(f"正在准备 {name} {TK_VERSION}；日志：{log_path}", flush=True)
                for command in (["./configure", *options],
                                ["make", f"-j{min(8, os.cpu_count() or 2)}"],
                                ["make", "install"]):
                    subprocess.run(command, cwd=directory, env=env,
                                   stdout=log, stderr=log, check=True)
            (prefix / ".built").touch()
        for name, digest in SOURCES.items():
            download(f"https://raw.githubusercontent.com/python/cpython/v{PYTHON_VERSION}/Modules/{name}",
                     source / name, digest)
        config = json.loads(subprocess.check_output([
            python, "-I", "-c", "import json,sysconfig; print(json.dumps(["
            "sysconfig.get_path('include'),sysconfig.get_config_var('EXT_SUFFIX')]))",
        ], text=True))
        extension = CACHE / "extension"
        extension.mkdir(exist_ok=True)
        subprocess.run([
            "clang", "-bundle", "-undefined", "dynamic_lookup", "-O2",
            "-DWITH_APPINIT=1", "-I", config[0], "-I", config[0] + "/internal",
            "-I", str(prefix / "include"), str(source / "_tkinter.c"),
            str(source / "tkappinit.c"), "-L", str(prefix / "lib"),
            "-ltcl9tk9.1", "-ltcl9.1", "-o", str(extension / ("_tkinter" + config[1])),
        ], env=env, stdout=log, stderr=log, check=True)
    # Tcl 9.1 uses its embedded zipfs library; inheriting an 8.6 library path
    # prevents interpreter initialization, including in copied debug binaries.
    runtime_env = {key: value for key, value in os.environ.items()
                   if key not in {"TCL_LIBRARY", "TK_LIBRARY"}}
    return python, {
        **runtime_env, "TOOLBOX_ACCESSIBILITY_PROBE": "1",
        "TOOLBOX_ACCESSIBILITY_EXTENSION": str(extension),
        "TOOLBOX_ACCESSIBILITY_DEPENDENCIES": str(dependencies),
    }


def run_page():
    # Set this before importing storage or any GUI/credential consumers.
    os.environ["TOOLBOX_DATA_ROOT"] = str(
        Path.home() / "Library/Application Support/com.electricity.toolbox/development/data/accessibility-probe"
    )
    import tkinter as tk
    from tkinter import ttk
    from unittest.mock import patch
    sys.path.insert(0, str(ROOT / "电力工具箱"))
    from toolbox.pages import OnlineEnergyPage
    from toolbox.runtime import TaskRegistry, ToolPaths
    from toolbox.ui import configure_theme

    root = tk.Tk()
    root.title("电力工具箱 · 辅助功能验证（不连接网站）")
    root.geometry("1000x700")
    configure_theme(ttk.Style(root))
    page = OnlineEnergyPage(root, ToolPaths(ROOT), TaskRegistry())
    page.pack(fill="both", expand=True)
    page.status_var.set("辅助功能验证：开始导出仅验证日期和命令，不连接网站。")
    report = ROOT / "logs/accessibility-probe-ui.json"
    report.parent.mkdir(exist_ok=True)
    evidence = {"business_execution": False, "export_requests": 0}

    def record(**extra):
        evidence.update(extra)
        payload = {"pid": os.getpid(), "python": sys.version.split()[0],
                   "tk": root.tk.call("package", "provide", "Tk"),
                   "voiceover": root.tk.call("::tk::accessible::check_screenreader"),
                   "start_date": page.start_date.get(), "end_date": page.end_date.get(),
                   "status": page.status_var.get(), **evidence}
        temporary = report.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        temporary.replace(report)

    def capture(command, **kwargs):
        record(command=command, export_requests=evidence["export_requests"] + 1)
        page.set_busy(True, "正在验证导出请求…")
        root.after(400, lambda: page.set_busy(False, "验证完成：未连接网站、未生成业务文件。"))

    # Exercise the original date validation and command builder, with only the
    # external execution boundary replaced. Never call the production service.
    record(business_execution=False)
    def heartbeat():
        record()
        root.after(250, heartbeat)
    root.after(250, heartbeat)
    with patch.object(page, "run_process", side_effect=capture), \
            patch("toolbox.pages._open_directory"):
        root.mainloop()


def audit(pid):
    """Cross-process acceptance: API success alone never counts as success."""
    logs = ROOT / "logs"
    ui_path = logs / "accessibility-probe-ui.json"
    before = json.loads(ui_path.read_text())
    if before["pid"] != pid or before.get("business_execution") is not False:
        raise RuntimeError("PID 必须对应当前隔离辅助功能验证窗口。")

    def ax(name, *args):
        result = subprocess.run([
            "swift", str(ROOT / "tooling/macos_ax_probe.swift"),
            "--pid", str(pid), *args,
        ], capture_output=True, text=True)
        path = logs / f"accessibility-ax-{name}.json"
        path.write_text(result.stdout)
        if not result.stdout:
            raise RuntimeError(f"AX 验证失败（退出码 {result.returncode}）：{result.stderr}")
        return json.loads(result.stdout)

    initial = ax("initial")
    names = {"导出开始日期", "导出结束日期", "开始导出", "打开输出目录", "取消任务", "导出任务状态"}
    controls = [node for node in initial.get("nodes", []) if node["description"] in names]
    target = "2000-01-01" if before["start_date"] != "2000-01-01" else "2000-01-02"
    written = ax("set", "--set", "导出开始日期", target)
    pressed = ax("press", "--press", "开始导出")
    time.sleep(1)
    after = json.loads(ui_path.read_text())
    checks = {
        "six_named_controls": len(controls) == 6 and {n["description"] for n in controls} == names,
        "stable_identifiers": len(controls) == 6 and all(n["identifier"] and n["identifierStable"] for n in controls),
        "enabled_export_button": any(n["description"] == "开始导出" and n["enabled"] == "1" for n in controls),
        "dates_settable": sum(n["valueSettable"] for n in controls if n["role"] == "AXTextField") == 2,
        "date_write_readback": written.get("result") == 0 and written.get("valueAfter") == target and after["start_date"] == target,
        "press_reaches_page": pressed.get("result") == 0 and after["export_requests"] > before["export_requests"],
    }
    summary = {"python": before["python"], "tk": before["tk"], "voiceover": before["voiceover"],
               "checks": checks, "compatible": all(checks.values()),
               "business_execution": False}
    (logs / "accessibility-probe-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["compatible"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-page", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--audit", type=int, metavar="PID")
    args = parser.parse_args()
    if args.audit is not None:
        return audit(args.audit)
    if args.run_page:
        run_page()
        return
    python, env = prepare()
    if not args.prepare_only:
        os.execve(python, [python, str(ROOT / "desktop/python/source_debug.py"),
                          "--accessibility-probe"], env)


if __name__ == "__main__":
    raise SystemExit(main())
