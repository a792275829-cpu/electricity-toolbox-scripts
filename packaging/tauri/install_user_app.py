"""Install a DMG application in ~/Applications, without administrator privileges."""
from __future__ import annotations
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

APP_ID = "com.electricity.toolbox"
APP_NAME = "电力工具箱.app"


def app_info(path: Path):
    data = plistlib.loads((path / "Contents/Info.plist").read_bytes())
    if data.get("CFBundleIdentifier") != APP_ID:
        raise ValueError("应用标识不匹配，已停止安装")
    version = data.get("CFBundleShortVersionString", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("应用版本无效")
    return tuple(map(int, version.split(".")))


def ensure_stopped():
    processes = subprocess.check_output(["/bin/ps", "-axo", "command="], text=True)
    if any(re.match(r"^.+\.app/Contents/MacOS/electricity-toolbox(?:\s|$)", line.strip()) for line in processes.splitlines()):
        raise ValueError("请先退出正在运行的电力工具箱（包括旧系统安装版），再重新安装；不会强制终止任务。")


def install(source: Path, destination: Path | None = None) -> Path:
    source = source.resolve()
    version = app_info(source)
    target = destination or Path.home() / "Applications" / APP_NAME
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("安装位置是符号链接，请先检查用户应用程序目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and app_info(target) > version:
        raise ValueError("已有更新版本，已保留原应用")
    ensure_stopped()
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(source)], check=True)
    folder = Path(tempfile.mkdtemp(prefix=".toolbox-install-", dir=target.parent))
    staged = folder / APP_NAME
    backup = folder / "previous.app"
    try:
        subprocess.run(["/usr/bin/ditto", "--norsrc", "--noextattr", "--noqtn", "--noacl", str(source), str(staged)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged)], check=True)
        ensure_stopped()
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        # Never delete the previous app if rollback itself failed.
        if not backup.exists():
            shutil.rmtree(folder, ignore_errors=True)
    return target


if __name__ == "__main__":
    try:
        target = install(Path(sys.argv[1]))
        print(f"安装完成：{target}\n后续更新请下载新版 DMG，退出工具箱后再次运行安装命令；原配置和数据保留。")
        subprocess.run(["/usr/bin/open", "-n", str(target)], check=True)
    except Exception as error:
        print(f"安装未完成：{error}", file=sys.stderr)
        sys.exit(1)
