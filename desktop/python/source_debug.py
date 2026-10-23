"""Give the source checkout's venv interpreter a distinct macOS GUI identity."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import sysconfig


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    bundle = root / ".toolbox/source-debug/电力工具箱源码调试.app"
    contents = bundle / "Contents"
    binary = contents / "MacOS/电力工具箱源码调试"
    binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(sys.executable).resolve(), binary)
    binary.chmod(0o755)
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    shutil.copy2(root / "desktop/src-tauri/icons/icon.icns", resources / "icon.icns")
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "com.electricity.toolbox.source-debug",
        "CFBundleExecutable": binary.name,
        "CFBundleName": "电力工具箱源码调试",
        "CFBundleDisplayName": "电力工具箱源码调试",
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "icon.icns",
        "NSHighResolutionCapable": True,
    }))
    subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True)
    # Use this checkout's existing venv packages and base standard library.
    # Only the interpreter is copied; all application code stays in main.
    env = {**os.environ, "PYTHONHOME": sys.base_prefix,
           "PYTHONPATH": os.pathsep.join(dict.fromkeys([
               str(root), sysconfig.get_path("purelib"), sysconfig.get_path("platlib"),
           ]))}
    env.pop("__PYVENV_LAUNCHER__", None)
    os.execve(binary, [str(binary), str(root / "电力工具箱/toolbox_launcher.pyw")], env)


if __name__ == "__main__":
    main()
