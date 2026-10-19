"""Package the built Tauri application for the macOS system Installer."""
from __future__ import annotations

import argparse
import hashlib
import plistlib
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build(app: Path, output: Path) -> Path:
    app = app.resolve()
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info["CFBundleIdentifier"] != "com.electricity.toolbox":
        raise ValueError("应用标识不匹配")
    version = info["CFBundleShortVersionString"]
    output.mkdir(parents=True, exist_ok=True)
    package = output.resolve() / f"ElectricityToolbox-{version}-apple-silicon.pkg"
    with tempfile.TemporaryDirectory(prefix="toolbox-pkg-") as folder:
        staging = Path(folder)
        root = staging / "root"
        target = root / "Applications/电力工具箱.app"
        target.parent.mkdir(parents=True)
        subprocess.run(["/usr/bin/ditto", "--norsrc", "--noextattr", "--noqtn", str(app), str(target)], check=True)
        components = staging / "components.plist"
        subprocess.run(["/usr/bin/pkgbuild", "--analyze", "--root", str(root), str(components)], check=True)
        data = plistlib.loads(components.read_bytes())
        for entry in data:
            entry["BundleIsRelocatable"] = False
            entry["BundleOverwriteAction"] = "upgrade"
        components.write_bytes(plistlib.dumps(data))
        subprocess.run(["/usr/bin/pkgbuild", "--root", str(root), "--component-plist", str(components),
                        "--scripts", str(ROOT / "packaging/tauri/pkg-scripts"),
                        "--identifier", "com.electricity.toolbox.pkg", "--version", version,
                        "--install-location", "/", str(package)], check=True)
    with package.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    package.with_suffix(".pkg.sha256").write_text(f"{checksum}  {package.name}\n")
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.app, args.output))
