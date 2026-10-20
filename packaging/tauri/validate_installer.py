"""Fail release publication until the installer has a valid GitHub digest."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop/python"))
from github_update import REPOSITORY, trusted_url
import re


def validate(data):
    name = f"ElectricityToolbox-{data['tag_name'].removeprefix('v')}-apple-silicon.dmg"
    if len(data["assets"]) != 2 or {a["name"] for a in data["assets"]} != {name, name + ".sha256"}:
        raise ValueError("当前仅发布对应版本的 DMG 及校验文件，不允许 App 更新包或更新索引")
    assets = [asset for asset in data["assets"] if asset["name"] == name]
    if len(assets) != 1:
        raise ValueError("缺少当前版本唯一的 DMG 安装镜像")
    asset = assets[0]
    if not (isinstance(asset.get("digest"), str)
            and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset["digest"])
            and type(asset.get("size")) is int and asset["size"] > 0
            and trusted_url(asset["browser_download_url"], REPOSITORY)):
        raise ValueError("DMG 下载链接、大小或 GitHub SHA-256 摘要无效")
    if not any(a["name"] == asset["name"] + ".sha256" for a in data["assets"]):
        raise ValueError("缺少 DMG 校验文件")


if __name__ == "__main__":
    validate(json.loads(Path(sys.argv[1]).read_text()))
    print("DMG installer metadata validated")
