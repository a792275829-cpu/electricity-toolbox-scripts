"""Build a self-contained resource payload on the native release runner."""

from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "desktop_build_support", Path(__file__).with_name("build_support.py")
)
build_support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_support)


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


MODULE_DIRECTORIES = {
    "电力工具箱", "上网电量抓取", "风力预测截图", "市场表更新", "现货价格更新",
    "交易系统信息更新", "功率调整收益统计表更新", "集团每日上传", "广东电价预测",
    "广东运行日报PDF解析", "南方分公司电力市场日报", "每日生产经营情况汇报自动生成工具",
    "500kV断面分析", "wps自动", "电力工具脚本",
}


def runtime_source(relative: Path) -> bool:
    if not build_support.exportable(relative):
        return False
    if relative.as_posix() in {"desktop/python/native_bridge.py", "desktop/python/github_update.py", "desktop/python/update_ui.py", "desktop/assets/app-icon-256.png"}:
        return True
    if len(relative.parts) == 1:
        return relative.name.startswith("toolbox_") and relative.suffix == ".py"
    return (relative.parts[0] in MODULE_DIRECTORIES
            and not {"tests", "docs", "scripts-dev"}.intersection(relative.parts)
            and not relative.name.startswith("test_")
            and relative.suffix.lower() != ".command")


def prepare_defaults(source: Path, defaults: Path) -> None:
    """Build first-install settings separately from mutable user configuration."""
    shutil.rmtree(defaults, ignore_errors=True)
    defaults.mkdir(parents=True)
    for example in source.rglob("config.example.json"):
        raw = json.loads(example.read_text(encoding="utf-8"))
        for key in ("username", "password"):
            if key in raw:
                raw[key] = ""
        example.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        path = defaults / example.relative_to(source).with_name("config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    wps_source = source / "wps自动/wps_excel_to_kdocs_config.defaults.json"
    wps_target = defaults / "wps自动/wps_excel_to_kdocs_config.json"
    wps_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wps_source, wps_target)
    config = defaults / "电力工具脚本/private-data-uploader-tool/upload.config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {"baseUrl": "https://xhxt.chng.com.cn", "sourceDir": ".", "headless": True}
        ),
        encoding="utf-8",
    )


def prepare(target: str, runtime_from: Path | None = None):
    native = (
        "aarch64-apple-darwin"
        if sys.platform == "darwin" and platform.machine() == "arm64"
        else None
    )
    if target != "aarch64-apple-darwin":
        raise ValueError("仅支持 Apple 芯片 Mac 构建目标")
    if target != native and not runtime_from:
        raise RuntimeError(
            "运行环境必须在对应原生系统上构建，或提供已验证的 --runtime-from"
        )
    payload = ROOT / "desktop/src-tauri/payload"
    cache = ROOT / "dist/tauri-build"
    cache.mkdir(parents=True, exist_ok=True)
    source = payload / "app"
    if source.exists():
        shutil.rmtree(source)
    source.mkdir(parents=True)
    build_support.export_source(source, predicate=runtime_source)
    runtime = payload / "runtime"
    marker = runtime / "target.txt"
    if not marker.is_file() or marker.read_text() != target:
        if runtime.exists():
            shutil.rmtree(runtime)
        runtime.mkdir()
        if runtime_from:
            for name in ("python", "node", "ms-playwright"):
                origin = runtime_from / name
                shutil.copytree(origin, runtime / name, symlinks=True)
        else:
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError("构建机需要 uv")
            identity = "cpython-3.11.15-macos-aarch64-none"
            downloads = cache / "python-download"
            run(
                [
                    uv,
                    "python",
                    "install",
                    identity,
                    "--install-dir",
                    downloads,
                    "--no-bin",
                ]
            )
            shutil.copytree(downloads / identity, runtime / "python", symlinks=True)
            python = runtime / "python/bin/python3"
            run(
                [
                    python,
                    "-m",
                    "pip",
                    "install",
                    "--break-system-packages",
                    "--disable-pip-version-check",
                    "--no-compile",
                    "--no-deps",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "-r",
                    ROOT / "packaging/tauri/requirements-macos.lock",
                ]
            )
            node_name = f"node-v{build_support.NODE_VERSION}-darwin-arm64"
            archive_name = node_name + ".tar.gz"
            base = f"https://nodejs.org/dist/v{build_support.NODE_VERSION}/"
            checksums = build_support.download(
                base + "SHASUMS256.txt",
                cache / f"node-{build_support.NODE_VERSION}-SHASUMS256.txt",
            )
            expected = next(
                line.split()[0]
                for line in checksums.read_text().splitlines()
                if line.split()[-1] == archive_name
            )
            archive = build_support.download(
                base + archive_name, cache / archive_name, expected
            )
            unpack = cache / "node-unpack"
            shutil.rmtree(unpack, ignore_errors=True)
            unpack.mkdir()
            build_support.extract(archive, unpack)
            shutil.move(unpack / node_name, runtime / "node")
            env = {
                **os.environ,
                "PLAYWRIGHT_BROWSERS_PATH": str(runtime / "ms-playwright"),
            }
            run([python, "-m", "playwright", "install", "chromium"], env=env)
        marker.write_text(target)
    from prepare_tk import prepare as prepare_tk

    prepare_tk(runtime / "python", cache)
    uploader = source / "电力工具脚本/private-data-uploader-tool"
    package = json.loads((uploader / "package.json").read_text())
    package["dependencies"]["playwright"] = build_support.PLAYWRIGHT_VERSION
    (uploader / "package.json").write_text(
        json.dumps(package, indent=2), encoding="utf-8"
    )
    (uploader / "package-lock.json").unlink(missing_ok=True)
    npm = shutil.which("npm")
    run(
        [
            npm,
            "install",
            "--ignore-scripts",
            "--omit=optional",
            "--no-audit",
            "--no-fund",
        ],
        cwd=uploader,
    )
    python = runtime / "python/bin/python3"
    registry = (
        runtime
        / "python"
        / "lib/python3.11/site-packages"
        / "playwright/driver/package/browsers.json"
    )
    if json.loads(registry.read_text()) != json.loads(
        (uploader / "node_modules/playwright-core/browsers.json").read_text()
    ):
        raise RuntimeError("Python / Node Playwright 浏览器版本不一致")
    defaults = payload / "defaults"
    prepare_defaults(source, defaults)
    bootstrap = payload / "bootstrap"
    bootstrap.mkdir(exist_ok=True)
    for name in ("prepare_workspace.py", "workspace_layout.py"):
        shutil.copy2(Path(__file__).with_name(name), bootstrap / name)
    # Remove only the former generated layout when reusing a local staging folder.
    shutil.rmtree(payload / "workspace", ignore_errors=True)
    (payload / "prepare_workspace.py").unlink(missing_ok=True)
    for path in list(payload.rglob("__pycache__")):
        shutil.rmtree(path)
    version = json.loads((ROOT / "desktop/package.json").read_text())["version"]
    files = {
        p.relative_to(source).as_posix(): build_support.sha256(p)
        for p in sorted(source.rglob("*"))
        if p.is_file()
    }
    build = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    manifest = {
        "layout": 2,
        "version": version,
        "target": target,
        "build": build,
        "files": files,
        "defaults": [
            p.relative_to(defaults).as_posix()
            for p in defaults.rglob("*")
            if p.is_file()
        ],
    }
    (payload / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    build_support.normalize_permissions(payload)
    if target == native:
        run(
            [
                python,
                "-B",
                "-c",
                'import openpyxl,playwright,pandas,sklearn,tkinter,PIL,pdfplumber,pypdfium2,docx; print("Bundled Python dependencies OK")',
            ]
        )
    print(
        f"Payload ready: {payload} ({len(files)} managed files, {target})", flush=True
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        required=True,
        choices=["aarch64-apple-darwin"],
    )
    parser.add_argument("--runtime-from", type=Path)
    args = parser.parse_args()
    prepare(args.target, args.runtime_from)
