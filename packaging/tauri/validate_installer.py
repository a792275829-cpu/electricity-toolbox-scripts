"""Fail release publication until the installer has a valid GitHub digest."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop/python"))
from github_update import REPOSITORY, parse_release, trusted_url
import re


def validate(data):
    candidate = {**data, "draft": False, "prerelease": False}
    release = parse_release(candidate, "0.0.0")
    if not release or not release.asset:
        raise ValueError("缺少当前版本的 PKG 安装包")
    asset = release.asset
    if not (isinstance(asset.get("digest"), str)
            and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset["digest"])
            and type(asset.get("size")) is int and asset["size"] > 0
            and trusted_url(asset["browser_download_url"], REPOSITORY)):
        raise ValueError("PKG 下载链接、大小或 GitHub SHA-256 摘要无效")
    if not any(a["name"] == asset["name"] + ".sha256" for a in data["assets"]):
        raise ValueError("缺少 PKG 校验文件")


if __name__ == "__main__":
    validate(json.loads(Path(sys.argv[1]).read_text()))
    print("PKG installer metadata validated")
