"""Build the user-install DMG with a portable, unprivileged first-install command."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def build(app: Path, output: Path) -> Path:
    app = app.resolve()
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info["CFBundleIdentifier"] != "com.electricity.toolbox":
        raise ValueError("应用标识不匹配")
    version = info["CFBundleShortVersionString"]
    output.mkdir(parents=True, exist_ok=True)
    package = output.resolve() / f"ElectricityToolbox-{version}-apple-silicon.dmg"
    with tempfile.TemporaryDirectory(prefix="toolbox-dmg-") as folder:
        root = Path(folder) / "image"
        root.mkdir()
        subprocess.run(["/usr/bin/ditto", "--norsrc", "--noextattr", "--noqtn", str(app), str(root / "电力工具箱.app")], check=True)
        shutil.copy2(Path(__file__).with_name("install_user_app.py"), root / "安装到用户应用程序.py")
        command = root / "安装到我的应用程序.command"
        command.write_text('''#!/bin/zsh
set -euo pipefail
image_root=${0:A:h}
"$image_root/电力工具箱.app/Contents/Resources/payload/runtime/python/bin/python3" -I -B "$image_root/安装到用户应用程序.py" "$image_root/电力工具箱.app"
''', encoding="utf-8")
        command.chmod(0o755)
        (root / "安装说明.txt").write_text(
            "电力工具箱 macOS Apple 芯片版\n\n"
            "1. 先退出正在运行的电力工具箱。\n"
            "2. 双击“安装到我的应用程序.command”，安装到 ~/Applications 并自动打开。\n"
            "   也可将“电力工具箱.app”拖到个人用户目录的 Applications 文件夹。\n"
            "3. 弹出本磁盘映像，今后从用户应用程序目录启动。\n\n"
            "后续点击“下载并更新”后自动下载、验签、替换及重启，无需管理员授权。\n"
            "已有 PKG 版需这样迁移一次；配置与业务数据继续使用原用户目录。\n"
            "旧 /Applications 应用不会自动删除，请确认新版正常后手动删除旧副本、更新 Dock 图标。\n"
            "首次打开若被 macOS 拦截，请按系统“隐私与安全性”提示允许。\n", encoding="utf-8")
        subprocess.run(["/usr/bin/hdiutil", "create", "-ov", "-volname", "电力工具箱", "-srcfolder", str(root), "-format", "UDZO", str(package)], check=True)
    with package.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    package.with_suffix(".dmg.sha256").write_text(f"{digest}  {package.name}\n")
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.app, args.output))
