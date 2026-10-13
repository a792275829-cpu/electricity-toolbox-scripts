"""Publish one complete update index after the two native builds finish."""

import datetime
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def build_index(tag, assets, signatures, notes="", date=None):
    platforms = {}
    for key, suffix, needle in [
        ("darwin-aarch64", ".app.tar.gz", "aarch64"),
        ("windows-x86_64", "-setup.exe", "x64"),
    ]:
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
    tag = sys.argv[1]
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
        index = build_index(tag, assets, signatures, release["body"])
        path = Path(directory) / "latest.json"
        path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run("gh", "release", "upload", tag, str(path), "--clobber")
    print("Validated signed update assets for darwin-aarch64 and windows-x86_64")
