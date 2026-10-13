"""Publish signed update metadata for the explicitly selected platforms."""

import argparse
import datetime
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

PLATFORMS = {
    "windows-x86_64": {
        "runner": "windows-2022", "target": "x86_64-pc-windows-msvc",
        "requirements": "packaging/windows/requirements.lock", "bundles": "nsis",
    },
    "darwin-aarch64": {
        "runner": "macos-14", "target": "aarch64-apple-darwin",
        "requirements": "packaging/tauri/requirements-macos.lock", "bundles": "app,dmg",
    },
}


def release_plan(selection="windows"):
    selections = {"windows": ("windows-x86_64",), "macos": ("darwin-aarch64",),
                  "both": tuple(PLATFORMS)}
    if selection not in selections:
        raise ValueError(f"未知发布平台：{selection}")
    targets = selections[selection]
    return targets, {"include": [dict(PLATFORMS[target]) for target in targets]}


def selected_notes(notes, targets):
    """Keep installation instructions consistent with the selected attachments."""
    lines = notes.splitlines()
    if "darwin-aarch64" not in targets:
        lines = [line for line in lines
                 if not line.startswith("| macOS") and not line.startswith("当前 Mac 安装包")]
        lines = [line.replace("、`.app.tar.gz`", "") for line in lines]
    if "windows-x86_64" not in targets:
        lines = [line for line in lines if not line.startswith("| Windows")]
    return "\n".join(lines).rstrip() + "\n"


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def build_index(tag, assets, signatures, notes="", date=None, *, targets=("windows-x86_64",)):
    if not targets or len(set(targets)) != len(targets) or set(targets) - PLATFORMS.keys():
        raise ValueError("发布平台不能为空、重复或包含未知平台")
    platforms = {}
    for key in targets:
        suffix, needle = ("-setup.exe", "x64") if key == "windows-x86_64" else (".app.tar.gz", "aarch64")
        candidates = [
            a
            for a in assets
            if a["name"].endswith(suffix)
            and (needle in a["name"] or key.startswith("darwin"))
        ]
        if len(candidates) != 1:
            raise ValueError(f"{key} 更新包数量异常：{len(candidates)}")
        asset = candidates[0]
        signature = signatures.get(asset["name"] + ".sig", "").strip()
        if not signature:
            raise ValueError(f"更新包缺少签名：{asset['name']}")
        url = asset["browser_download_url"]
        # Draft assets can carry an untagged-* URL which disappears on publish.
        marker = "/releases/download/"
        if marker in url:
            url = (url.split(marker, 1)[0] + marker + quote(tag, safe="")
                   + "/" + quote(asset["name"], safe=""))
        platforms[key] = {"url": url, "signature": signature}
    return {
        "version": tag.removeprefix("v"),
        "notes": notes,
        "pub_date": date
        or datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "platforms": platforms,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?")
    parser.add_argument("--plan", choices=("windows", "macos", "both"))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--notes-output", type=Path)
    parser.add_argument("--platforms", default="windows-x86_64")
    args = parser.parse_args()
    if args.plan:
        targets, matrix = release_plan(args.plan)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write("platforms=" + ",".join(targets) + "\n")
                output.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
        if args.notes_file and args.notes_output:
            args.notes_output.write_text(selected_notes(args.notes_file.read_text(encoding="utf-8"), targets), encoding="utf-8")
        print(json.dumps(matrix))
        raise SystemExit(0)
    if not args.tag:
        parser.error("发布索引需要版本标签")
    tag = args.tag
    targets = tuple(args.platforms.split(","))
    release = json.loads(
        run("gh", "release", "view", tag, "--json", "assets,body,isDraft")
    )
    if not release["isDraft"]:
        raise ValueError("只能为草稿发行版生成索引")
    # gh CLI assets use name/url; translate to REST naming.
    repository = run(
        "gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"
    )
    release_id = run(
        "gh", "release", "view", tag, "--json", "databaseId", "--jq", ".databaseId"
    )
    assets = json.loads(run("gh", "api", f"repos/{repository}/releases/{release_id}"))[
        "assets"
    ]
    with tempfile.TemporaryDirectory() as directory:
        run("gh", "release", "download", tag, "--pattern", "*.sig", "--dir", directory)
        signatures = {p.name: p.read_text() for p in Path(directory).glob("*.sig")}
        index = build_index(tag, assets, signatures, release["body"], targets=targets)
        path = Path(directory) / "latest.json"
        path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run("gh", "release", "upload", tag, str(path), "--clobber")
    print("Validated signed update assets for " + ", ".join(targets))
