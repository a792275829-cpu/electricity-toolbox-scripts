"""Publish the signed macOS update index after the native build finishes."""

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
    raise SystemExit("已停止发布 App 更新包和 latest.json；请仅发布 DMG 及校验文件")
