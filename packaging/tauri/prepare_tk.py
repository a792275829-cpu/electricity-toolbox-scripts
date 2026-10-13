"""Bundle the same Tcl/Tk 8.6.18 used by the original Mac GUI.

Build from pinned upstream source with a macOS 14 deployment target. Never
copy Homebrew dylibs (they can require the build machine's newer macOS).
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

SOURCES = {
    "_tkinter.c": "7cb1e0106b66b1e8794cea6a1b50fe3f8ecd98135a0791d413deca2cf3461eba",
    "tkappinit.c": "67cf71596cd57a2789a634680b0d547c18d3bc3a871565af130fc3aec951923f",
    "tkinter.h": "042dd93c97e27544e6ebee35757d8a1cab2c4d23332cc70ce16c6e5b4671a153",
    "clinic/_tkinter.c.h": "a2583d8fe174b46f823c44f5ed55d138f312cd39d719bd7e2c6044ac0c93e4d4",
}
ARCHIVES = {
    "tcl": "8b9b554f3539b37bea20ee6cef14faa63df1259896e466328f596eca9f0c49a5",
    "tk": "8c1d775126c39124c89752737c32c23636b3cd308d0445977aec58cfbc42c46c",
}


def download(url: str, path: Path, digest: str):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(url, timeout=90).read())
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Tk 源码校验失败：{path.name}")
    return path


def prepare(python_root: Path, cache: Path):
    cache = cache.resolve() / "tk86-source"
    cache.mkdir(parents=True, exist_ok=True)
    # Tcl's generated Makefiles do not quote shell metacharacters in prefixes.
    location = cache / "build-directory.txt"
    build = Path(location.read_text()) if location.exists() else None
    if (
        build is None
        or not build.is_dir()
        or not build.is_relative_to(Path(tempfile.gettempdir()))
    ):
        build = Path(tempfile.mkdtemp(prefix="electricity-toolbox-tk86-"))
        location.write_text(str(build))
    prefix = build / "install"
    stamp = prefix / ".built-macos14"
    env = {
        **os.environ,
        "MACOSX_DEPLOYMENT_TARGET": "14.0",
        "CFLAGS": "-O2 -mmacosx-version-min=14.0",
        "LDFLAGS": "-mmacosx-version-min=14.0 -Wl,-headerpad_max_install_names",
    }
    jobs = str(min(8, os.cpu_count() or 2))
    if not stamp.exists():
        for name, digest in ARCHIVES.items():
            archive = download(
                f"https://codeload.github.com/tcltk/{name}/tar.gz/refs/tags/core-8-6-18",
                cache / f"{name}-8.6.18.tar.gz",
                digest,
            )
            with tarfile.open(archive) as tar:
                # All members must remain beneath the pinned upstream directory.
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in Path(member.name).parts:
                        raise ValueError("Tk 源码包包含不安全路径")
                tar.extractall(build, filter="data")
            directory = build / f"{name}-core-8-6-18" / "unix"
            options = [f"--prefix={prefix}", "--enable-threads", "--enable-shared"]
            if name == "tk":
                options += ["--enable-aqua", f"--with-tcl={prefix / 'lib'}"]
            subprocess.run(
                ["./configure", *options], cwd=directory, env=env, check=True
            )
            subprocess.run(["make", f"-j{jobs}"], cwd=directory, env=env, check=True)
            subprocess.run(["make", "install"], cwd=directory, env=env, check=True)
        stamp.touch()
    for name, digest in SOURCES.items():
        download(
            f"https://raw.githubusercontent.com/python/cpython/v3.11.15/Modules/{name}",
            cache / name,
            digest,
        )
    python_root = python_root.resolve()
    python = python_root / "bin/python3"
    suffix = json.loads(
        subprocess.check_output(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import json,sysconfig; print(json.dumps(sysconfig.get_config_var('EXT_SUFFIX')))",
            ],
            text=True,
        )
    )
    extension = python_root / "lib/python3.11/lib-dynload" / f"_tkinter{suffix}"
    subprocess.run(
        [
            "clang",
            "-bundle",
            "-undefined",
            "dynamic_lookup",
            "-O2",
            "-DWITH_APPINIT=1",
            "-mmacosx-version-min=14.0",
            "-Wl,-headerpad_max_install_names",
            "-I",
            str(python_root / "include/python3.11"),
            "-I",
            str(prefix / "include"),
            str(cache / "_tkinter.c"),
            str(cache / "tkappinit.c"),
            "-L",
            str(prefix / "lib"),
            "-ltk8.6",
            "-ltcl8.6",
            "-o",
            str(extension),
        ],
        check=True,
    )
    lib = python_root / "lib"
    for name in ("tcl8.6", "tk8.6"):
        shutil.copytree(prefix / "lib" / name, lib / name, dirs_exist_ok=True)
    for name in ("libtcl8.6.dylib", "libtk8.6.dylib"):
        (lib / name).unlink(missing_ok=True)
        shutil.copy2(prefix / "lib" / name, lib / name)
        (lib / name).chmod(0o755)
    for binary in (extension, lib / "libtcl8.6.dylib", lib / "libtk8.6.dylib"):
        for name in ("libtcl8.6.dylib", "libtk8.6.dylib"):
            relative = (
                f"@loader_path/../../{name}"
                if binary == extension
                else f"@loader_path/{name}"
            )
            subprocess.run(
                [
                    "install_name_tool",
                    "-change",
                    str(prefix / "lib" / name),
                    relative,
                    str(binary),
                ],
                check=True,
            )
        if binary != extension:
            subprocess.run(
                ["install_name_tool", "-id", f"@rpath/{binary.name}", str(binary)],
                check=True,
            )
        subprocess.run(["codesign", "--force", "--sign", "-", str(binary)], check=True)
    licenses = python_root / "share/licenses/tcl-tk"
    licenses.mkdir(parents=True, exist_ok=True)
    for name in ARCHIVES:
        shutil.copy2(
            build / f"{name}-core-8-6-18/license.terms", licenses / f"{name}.txt"
        )
    # Give the Tk process its own macOS application identity. Sharing the
    # hidden Tauri updater's identity breaks Dock activation/accessibility.
    shutil.rmtree(python_root / "ToolboxUI.app", ignore_errors=True)
    contents = python_root / "电力工具箱.app/Contents"
    executable = contents / "MacOS/电力工具箱"
    executable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python.resolve(), executable)
    executable.chmod(0o755)
    version = json.loads(
        (Path(__file__).resolve().parents[2] / "desktop/package.json").read_text()
    )["version"]
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    shutil.copy2(Path(__file__).resolve().parents[2] / "desktop/src-tauri/icons/icon.icns",
                 resources / "icon.icns")
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "com.electricity.toolbox.native",
                "CFBundleExecutable": "电力工具箱",
                "CFBundleName": "电力工具箱",
                "CFBundleDisplayName": "电力工具箱",
                "CFBundleIconFile": "icon.icns",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
                "LSMinimumSystemVersion": "14.0",
                "NSHighResolutionCapable": True,
            }
        )
    )
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(contents.parent)], check=True
    )
    print("Bundled Tcl/Tk 8.6.18 with macOS 14 deployment target")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("python_root", type=Path)
    parser.add_argument("cache", type=Path)
    args = parser.parse_args()
    prepare(args.python_root, args.cache)
