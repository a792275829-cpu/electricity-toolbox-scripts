"""GitHub updates: verified deltas with a full DMG fallback; no business data."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORY = "a792275829-cpu/electricity-toolbox-scripts"


class UpdateError(ValueError):
    pass


class Cancelled(UpdateError):
    pass


def version_parts(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,3}", value):
        raise UpdateError("版本号无效")
    parts = tuple(map(int, value.removeprefix("v").split(".")))
    return parts + (0,) * (4 - len(parts))


def trusted_url(value: str, repository: str) -> bool:
    url = urlsplit(value)
    return (url.scheme == "https" and url.netloc == "github.com"
            and url.path.startswith(f"/{repository}/releases/"))


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page: str
    asset: dict | None
    update_asset: dict | None = None


def parse_release(data: dict, current: str, repository: str = REPOSITORY) -> Release | None:
    try:
        tag = data["tag_name"]
        latest = version_parts(tag)
        if not trusted_url(data["html_url"], repository):
            raise UpdateError("版本发布页面无效")
        if data["draft"] or data["prerelease"] or latest <= version_parts(current):
            return None
        name = f"ElectricityToolbox-{tag.removeprefix('v')}-apple-silicon.dmg"
        assets = [a for a in data["assets"] if a["name"] == name]
        indices = [a for a in data["assets"] if a["name"] == "update-manifest.json"]
        if len(assets) > 1 or len(indices) > 1:
            raise UpdateError("版本安装包重复")
        return Release(tag, str(data.get("body") or "此版本没有提供更新说明。")[:100_000],
                       data["html_url"], assets[0] if assets else None,
                       indices[0] if indices else None)
    except (KeyError, TypeError, AttributeError) as exc:
        raise UpdateError("GitHub 版本信息无效") from exc


class GitHubUpdater:
    def __init__(self, current: str, repository: str = REPOSITORY, opener=None, app_bundle=None):
        self.current = current
        self.repository = repository
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository):
            raise UpdateError("更新仓库配置无效")
        if opener is None:
            def opener(request, timeout):
                system_ca = Path("/etc/ssl/cert.pem")
                context = ssl.create_default_context(cafile=str(system_ca) if system_ca.is_file() else None)
                return urllib.request.urlopen(request, timeout=timeout, context=context)
        self.open = opener
        bundle = app_bundle or os.environ.get("TOOLBOX_APP_BUNDLE")
        self.app_bundle = Path(bundle) if bundle else None
        self.fallback_reason = None

    def check(self) -> Release | None:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"ElectricityToolbox/{self.current}",
                     "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache"},
        )
        try:
            with self.open(request, timeout=20) as response:
                if response.status != 200:
                    raise UpdateError(f"更新服务返回错误（HTTP {response.status}）")
                data = json.loads(response.read(2 * 1024 * 1024))
            return parse_release(data, self.current, self.repository)
        except urllib.error.HTTPError as exc:
            messages = {403: "GitHub 请求次数已达上限，请稍后重试", 429: "GitHub 请求次数已达上限，请稍后重试",
                        404: "更新仓库尚未发布正式版本"}
            raise UpdateError(messages.get(exc.code, f"更新服务返回错误（HTTP {exc.code}）")) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise UpdateError("GitHub 版本信息无效") from exc

    def download(self, release: Release, cancel: threading.Event, progress) -> Path:
        self.fallback_reason = None
        if release.update_asset and release.asset and self.app_bundle:
            try:
                result = self.download_delta(release, cancel, progress)
                if result is not None:
                    return result
            except Cancelled:
                raise
            except Exception as exc:
                # Preserve the first meaningful error for diagnostics; full DMG is the fallback.
                self.fallback_reason = str(exc)
        return self.download_full(release, cancel, progress)

    def download_full(self, release, cancel, progress):
        asset = release.asset
        if asset is None:
            raise UpdateError("此版本尚未提供 Apple 芯片安装包，请查看发布页面")
        try:
            digest, size, url = asset["digest"], asset["size"], asset["browser_download_url"]
            valid = (isinstance(digest, str) and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest)
                     and type(size) is int and size > 0 and trusted_url(url, self.repository))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise UpdateError("安装包链接、摘要或大小无效")
        directory = Path(tempfile.mkdtemp(prefix="ElectricityToolboxUpdate-"))
        path = directory / f"ElectricityToolbox-{release.version.removeprefix('v')}-apple-silicon.dmg"
        try:
            return self.download_asset(asset, path, cancel, progress)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def download_asset(self, asset, path, cancel, progress):
        try:
            digest, size, url = asset["digest"], asset["size"], asset["browser_download_url"]
            if (not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest)
                    or type(size) is not int or size <= 0 or not trusted_url(url, self.repository)):
                raise UpdateError("更新文件链接、摘要或大小无效")
        except (KeyError, TypeError) as exc:
            raise UpdateError("更新文件元数据无效") from exc
        if cancel.is_set():
            raise Cancelled("已取消下载")
        try:
            count = 0
            checksum = hashlib.sha256()
            deadline = time.monotonic() + 1800
            request = urllib.request.Request(url, headers={"User-Agent": f"ElectricityToolbox/{self.current}"})
            with self.open(request, timeout=30) as source, path.open("wb") as target:
                if source.status != 200:
                    raise UpdateError(f"下载失败（HTTP {source.status}）")
                while True:
                    if cancel.is_set():
                        raise Cancelled("已取消下载")
                    if time.monotonic() > deadline:
                        raise UpdateError("下载超时，请重试")
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > size:
                        raise UpdateError("安装包大小校验失败，请重新下载")
                    checksum.update(chunk)
                    target.write(chunk)
                    progress(min(count / size, 1))
            if cancel.is_set():
                raise Cancelled("已取消下载")
            if count != size or checksum.hexdigest() != digest[7:].lower():
                raise UpdateError("安装包校验失败，请重新下载")
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def download_delta(self, release, cancel, progress):
        from delta_update import apply_patch, app_version, cancelled, select_route, INDEX_NAME
        from installer_image import build
        target = release.version.removeprefix("v")
        if app_version(self.app_bundle) != self.current.removeprefix("v"):
            raise UpdateError("当前安装版本不匹配")
        directory = Path(tempfile.mkdtemp(prefix="ElectricityToolboxDelta-"))
        try:
            index_asset = release.update_asset
            if type(index_asset.get("size")) is not int or not 0 < index_asset["size"] <= 2 * 1024 * 1024:
                raise UpdateError("更新索引大小无效")
            index_file = self.download_asset(index_asset, directory / INDEX_NAME, cancel, lambda _: None)
            index = json.loads(index_file.read_text(encoding="utf-8"))
            route = select_route(index, self.current.removeprefix("v"), target,
                                 release.asset["size"] - index_asset["size"], self.repository)
            if not route:
                return None
            # Only the temporary clone is modified. The running app and all user data stay untouched.
            import subprocess
            staged = directory / "staging"
            staged.mkdir()
            app = staged / "电力工具箱.app"
            cancelled(cancel)
            subprocess.run(["/usr/bin/ditto", "--norsrc", "--noextattr", "--noqtn", "--noacl",
                            str(self.app_bundle), str(app)], check=True, capture_output=True)
            installer = directory / "installer.py"
            total, downloaded = sum(edge["size"] for edge in route), 0
            for edge in route:
                cancelled(cancel)
                patch = self.download_asset(edge, directory / edge["name"], cancel,
                    lambda fraction: progress(.85 * (downloaded + fraction * edge["size"]) / total))
                apply_patch(app, patch, installer, edge["base"], edge["target"], cancel)
                downloaded += edge["size"]
                patch.unlink()
            cancelled(cancel)
            progress(.9)
            result = build(app, directory, installer)
            cancelled(cancel)
            progress(1)
            # Retain just the verified image and checksum for installation/re-opening.
            shutil.rmtree(staged)
            installer.unlink()
            index_file.unlink()
            return result
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            if directory.exists() and not list(directory.glob("*.dmg")):
                shutil.rmtree(directory, ignore_errors=True)
