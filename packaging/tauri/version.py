"""Keep frontend, native app and tag versions in sync."""

import argparse
import datetime
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]


def release_changes(version):
    path = ROOT / "CHANGELOG.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    sections = list(re.finditer(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", content, re.M))
    matches = [(index, match) for index, match in enumerate(sections) if match[1] == version]
    if len(matches) != 1:
        raise ValueError(f"CHANGELOG.md 必须包含唯一的 {version} 版本记录")
    index, match = matches[0]
    datetime.date.fromisoformat(match[2])
    end = sections[index + 1].start() if index + 1 < len(sections) else len(content)
    body = content[match.end():end].strip()
    if not re.search(r"^- \S.+", body, re.M):
        raise ValueError(f"CHANGELOG.md 中 {version} 缺少具体更新内容")
    return content[match.start():end].strip()


def update_changelog(version, notes, date=None):
    notes = [note.strip() for note in notes if note.strip()]
    if not notes or any("\n" in note or "\r" in note for note in notes):
        raise ValueError("更新版本须使用 --notes 提供具体变更，每项一行")
    date = date or datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    datetime.date.fromisoformat(date)
    path = ROOT / "CHANGELOG.md"
    content = path.read_text(encoding="utf-8") if path.exists() else "# 更新日志\n\n"
    if re.search(rf"^## \[{re.escape(version)}\]", content, re.M):
        raise ValueError(f"{version} 已有更新日志，请编辑已有记录或递增版本")
    section = f"## [{version}] - {date}\n\n" + "\n".join(f"- {note}" for note in notes) + "\n\n"
    first = re.search(r"^## ", content, re.M)
    position = first.start() if first else len(content.rstrip()) + 2
    content = content[:position].rstrip() + "\n\n" + section + content[position:]
    return path, content.rstrip() + "\n"


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


def set_version(version, notes=(), date=None):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("版本号须为 X.Y.Z")
    changelog_path, changelog_content = update_changelog(version, notes, date)
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
    changelog_path.write_text(changelog_content, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "set"])
    parser.add_argument("version", nargs="?")
    parser.add_argument("--tag")
    parser.add_argument("--notes", action="append", default=[])
    parser.add_argument("--date", help="更新日志日期 YYYY-MM-DD，默认上海日期")
    parser.add_argument("--require-notes", action="store_true")
    parser.add_argument("--release-notes", type=Path, help="导出当前版本更新内容及安装说明")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.action == "set":
        set_version(args.version or "", args.notes, args.date)
    tag = check(args.tag)
    if args.require_notes or args.release_notes:
        changes = release_changes(tag.removeprefix("v"))
        if args.release_notes:
            install = (ROOT / "packaging/tauri/release_notes.md").read_text(encoding="utf-8").strip()
            args.release_notes.write_text(changes + "\n\n" + install + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a") as stream:
            stream.write(f"tag={tag}\n")
    print(tag)
