"""Export current sources and a verified Windows runtime as a maintenance kit."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("windows_package", Path(__file__).with_name("package_windows.py"))
windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows)

EXTRA_LOCKS = {"desktop/src-tauri/Cargo.lock", "packaging/tauri/requirements-macos.lock"}
EXCLUDED = {"runtime", "development", "target", "payload", "frontend-dist", "gen",
            "backups", "cache", ".pytest_cache", ".mypy_cache", ".worktrees", ".superpowers"}
ROOT_FILES = {"AGENTS.md", "README.md", ".gitignore", "requirements.txt", "setup_macos.command", "package_macos.command"}
DIRECTORIES = {".github", "00_启动", "packaging", "desktop", "电力工具箱", "500kV断面分析", "上网电量抓取",
               "市场表更新", "交易系统信息更新", "功率调整收益统计表更新", "广东电价预测", "风力预测截图",
               "现货价格更新", "广东运行日报PDF解析", "南方分公司电力市场日报",
               "每日生产经营情况汇报自动生成工具", "电力工具脚本", "集团每日上传", "wps自动"}
TEMPLATE = "每日生产经营情况汇报自动生成工具/模板/每日生产经营情况汇报（自动）.docx"
SOURCE_SUFFIXES = {".py", ".pyw", ".md", ".txt", ".bat", ".command", ".vbs", ".json", ".mjs",
                   ".ps1", ".html", ".css", ".j2", ".js", ".ts", ".tsx", ".rs", ".toml",
                   ".yml", ".yaml", ".png", ".ico", ".icns", ".ttf", ".lock"}


def source_file(relative: Path) -> bool:
    name = relative.as_posix()
    if EXCLUDED.intersection(relative.parts):
        return False
    if not windows.exportable(relative) and name not in EXTRA_LOCKS:
        return False
    if len(relative.parts) == 1:
        return relative.name in ROOT_FILES or (relative.name.startswith("toolbox_") and relative.suffix == ".py")
    if relative.parts[0] not in DIRECTORIES:
        return False
    if relative.name.startswith(".env") or relative.suffix.lower() not in SOURCE_SUFFIXES | {".docx"}:
        return relative.name == ".gitignore"
    if relative.suffix.lower() in {".xlsx", ".xls", ".csv", ".tsv", ".pdf", ".docx", ".doc", ".zip"}:
        return name == TEMPLATE
    return True


def copy_verified_runtime(origin: Path, stage: Path) -> int:
    manifest = json.loads((origin / "便携包清单.json").read_text(encoding="utf-8"))
    prefixes = ("runtime/", "电力工具脚本/private-data-uploader-tool/node_modules/")
    count = 0
    for name, expected in manifest["files"].items():
        if not name.startswith(prefixes):
            continue
        relative = Path(name)
        # Mac npm generated Unix-only command symlinks; the uploader uses Node
        # imports directly, so no .bin launcher is needed on the Windows target.
        if ".bin" in relative.parts:
            continue
        source = origin / relative
        if relative.is_absolute() or ".." in relative.parts or source.is_symlink() or not source.resolve().is_relative_to(origin.resolve()):
            raise ValueError("非法运行环境路径")
        if not source.is_file() or windows.sha256(source) != expected["sha256"]:
            raise ValueError(f"运行环境校验失败：{name}")
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    for name in ("runtime/python311/python.exe", "runtime/python311/Lib/venv/__init__.py",
                 "runtime/node/node.exe", "runtime/node/npm.cmd", "runtime/ms-playwright/chromium-1187/chrome-win/chrome.exe"):
        if not (stage / name).is_file():
            raise ValueError(f"运行环境不完整：{name}")
    # Same pinned uploader dependencies as the browser / Python runtime.
    uploader = Path("电力工具脚本/private-data-uploader-tool")
    for name in ("package.json", "package-lock.json"):
        source = origin / uploader / name
        expected = manifest["files"][(uploader / name).as_posix()]["sha256"]
        if windows.sha256(source) != expected:
            raise ValueError("Node 依赖清单校验失败")
        shutil.copy2(source, stage / uploader / name)
    return count


def build(origin: Path, output: Path) -> Path:
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    name = f"电力工具箱_Windows维护测试包_{date}"
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="maintenance-", dir=ROOT / "dist"))
    stage = staging / name
    stage.mkdir()
    count = windows.export_source(stage, predicate=source_file)
    print(f"Exported {count} source / test / asset files", flush=True)
    runtime_count = copy_verified_runtime(origin, stage)
    print(f"Verified and copied {runtime_count} runtime / Node dependency files", flush=True)
    wrappers = {
        "01_初始化维护环境.cmd": "init", "02_启动工具箱调试版.cmd": "start",
        "03_环境诊断.cmd": "doctor", "04_运行维护回归测试.cmd": "test",
        "05_准备桌面开发.cmd": "desktop-init", "06_启动Tauri开发版.cmd": "desktop-dev",
        "07_运行桌面前端测试.cmd": "desktop-test", "08_构建Windows测试安装包.cmd": "build",
        "09_导出诊断记录.cmd": "export", "00_校验维护包.cmd": "verify",
    }
    for filename, action in wrappers.items():
        script = ("@echo off\nchcp 65001 >nul\nsetlocal\n"
                  'set "PY=%~dp0runtime\\python311\\python.exe"\n'
                  'if exist "%~dp0.venv\\Scripts\\python.exe" set "PY=%~dp0.venv\\Scripts\\python.exe"\n'
                  'if not exist "%PY%" (\necho Extract the complete maintenance ZIP first.\npause\nexit /b 1\n)\n'
                  f'"%PY%" -u -B "%~dp0packaging\\windows\\maintenance.py" {action}\n'
                  'set "RESULT=%ERRORLEVEL%"\necho.\necho Exit code: %RESULT%\npause\nexit /b %RESULT%\n')
        (stage / filename).write_bytes(script.replace("\n", "\r\n").encode("ascii"))
    shutil.copy2(ROOT / "packaging/windows/MAINTENANCE.md", stage / "先读我_Windows维护测试说明.md")
    with (stage / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write("\n# Windows maintenance runtime and local data\n/runtime/\n/development/\n/维护包清单.json\n")
    # Scrub example account fields, while preserving exact config schemas.
    for path in stage.rglob("*config.example.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key in ("username", "password"):
            if key in raw:
                raw[key] = ""
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    files = {p.relative_to(stage).as_posix(): {"size": p.stat().st_size, "sha256": windows.sha256(p)}
             for p in sorted(stage.rglob("*")) if p.is_file()}
    manifest = {"kind": "windows-maintenance", "created": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "target": "Windows 10 22H2 / Windows 11 x64", "source_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "includes_uncommitted_source": True, "windows_native_validation": "not_run_on_macOS",
                "runtime": {"python": "3.11.15", "node": windows.NODE_VERSION, "playwright": windows.PLAYWRIGHT_VERSION},
                "files": files}
    (stage / "维护包清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = output / (name + ".zip")
    if archive.exists():
        archive = output / (name + "_" + datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H%M%S") + ".zip")
    print(f"Creating ZIP: {archive}", flush=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(staging))
    checksum = windows.sha256(archive)
    archive.with_suffix(".sha256.txt").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    print(f"Done: {archive.stat().st_size / 1024**2:.1f} MiB, {len(files)} files\nStaging: {stage}", flush=True)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-from", type=Path, required=True, help="Existing clean Windows portable folder with hash manifest")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.runtime_from.resolve(), args.output.resolve()))
