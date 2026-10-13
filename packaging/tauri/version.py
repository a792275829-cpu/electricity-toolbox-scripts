"""Keep frontend, native app and tag versions in sync."""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check(tag=None):
    package = json.loads((ROOT / "desktop/package.json").read_text())["version"]
    config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text())[
        "version"
    ]
    cargo = re.search(
        r'^version = "([^"]+)"',
        (ROOT / "desktop/src-tauri/Cargo.toml").read_text(),
        re.M,
    ).group(1)
    lock = json.loads((ROOT / "desktop/package-lock.json").read_text())
    if (
        len({package, config, cargo, lock["version"], lock["packages"][""]["version"]})
        != 1
    ):
        raise ValueError("版本不一致：请执行 version.py set X.Y.Z")
    if not re.fullmatch(r"\d+\.\d+\.\d+", package):
        raise ValueError("稳定版须使用 X.Y.Z")
    if tag and tag != "v" + package:
        raise ValueError(f"标签必须为 v{package}")
    return "v" + package


def set_version(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("版本号须为 X.Y.Z")
    for name in (
        "desktop/package.json",
        "desktop/src-tauri/tauri.conf.json",
        "desktop/package-lock.json",
    ):
        path = ROOT / name
        data = json.loads(path.read_text())
        data["version"] = version
        if "packages" in data:
            data["packages"][""]["version"] = version
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    path = ROOT / "desktop/src-tauri/Cargo.toml"
    path.write_text(
        re.sub(
            r'^version = "[^"]+"',
            f'version = "{version}"',
            path.read_text(),
            count=1,
            flags=re.M,
        )
    )
    path = ROOT / "desktop/src-tauri/Cargo.lock"
    if path.exists():
        path.write_text(
            re.sub(
                r'(name = "electricity-toolbox"\nversion = ")[^"]+',
                lambda m: m.group(1) + version,
                path.read_text(),
                count=1,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "set"])
    parser.add_argument("version", nargs="?")
    parser.add_argument("--tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.action == "set":
        set_version(args.version or "")
    tag = check(args.tag)
    if args.github_output:
        with args.github_output.open("a") as stream:
            stream.write(f"tag={tag}\n")
    print(tag)
