"""Workspace-wide Qingneng credentials; browser sessions remain tool-local."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .storage import WORKSPACE_ROOT, data_path

WORKSPACE = WORKSPACE_ROOT


def credentials_path(workspace: Path = WORKSPACE) -> Path:
    return data_path(workspace / ".toolbox" / "qingneng" / "config.json")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ValueError(f"无法读取账号配置，请检查文件：{path}") from None
    if not isinstance(raw, dict):
        raise ValueError(f"账号配置应为 JSON 对象：{path}")
    return raw


def write_private_json(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(raw, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_credentials(raw: dict[str, Any], *, workspace: Path = WORKSPACE) -> dict[str, Any]:
    """Override only the login pair, preserving every per-tool business option."""
    path = credentials_path(workspace)
    if not path.exists():
        return raw
    saved = read_json(path)
    if not saved.get("username") or not saved.get("password"):
        raise ValueError("清能网站账号配置不完整，请在任务中心下方重新填写。")
    return {**raw, "username": saved["username"], "password": saved["password"]}


def read_login_config(path: Path, fallback: Path | None = None, *, workspace: Path = WORKSPACE) -> dict[str, Any]:
    """Read a tool-local config, keeping the shared login fallback in play.

    A tool-local file that only carries business options (for example ``headless``)
    must not cancel the shared login: the fallback file still supplies the account
    pair, while every key present locally wins.
    """
    path = data_path(path)
    fallback = data_path(fallback) if fallback is not None else None
    local = read_json(path)
    has_login = bool(local.get("username")) and bool(local.get("password"))
    if has_login or fallback is None:
        return apply_credentials(local, workspace=workspace)
    merged = read_json(fallback)
    merged.update(local)
    return apply_credentials(merged, workspace=workspace)


class CredentialStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.path = credentials_path(workspace)

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            return read_json(self.path)
        # The original online-energy config is the existing shared login default.
        for relative in (
            "上网电量抓取/config.json",
            "每日生产经营情况汇报自动生成工具/scripts/config.json",
            "集团每日上传/config.json",
            "广东电价预测/config.json",
            "现货价格更新/config.json",
            "交易系统信息更新/config.json",
            "风力预测截图/config.json",
        ):
            raw = read_json(data_path(self.workspace / relative))
            if raw.get("username") and raw.get("password"):
                return self.save(str(raw["username"]), str(raw["password"]))
        return {}

    def save(self, username: str, password: str) -> dict[str, Any]:
        username = username.strip()
        if not username or not password:
            raise ValueError("账号和密码均不能为空；填写完整后自动保存。")
        previous = read_json(self.path)
        if previous.get("username") == username and previous.get("password") == password:
            return previous
        saved = {"username": username, "password": password, "revision": uuid.uuid4().hex}
        write_private_json(self.path, saved)
        return saved


def session_marker_path(auth_state_path: Path) -> Path:
    return auth_state_path.parent / ".qingneng-session" / "config.json"


def prepare_browser_session(context, auth_state_path: Path) -> bool:
    """On an account edit, ignore old cookies and require login on the next run."""
    saved = read_json(credentials_path())
    revision = saved.get("revision", "")
    context._qingneng_revision = revision
    context._qingneng_expected_credentials = (saved.get("username"), saved.get("password"))
    try:
        previous_revision = read_json(session_marker_path(auth_state_path)).get("revision")
    except ValueError:
        previous_revision = None
    changed = bool(revision) and previous_revision != revision
    context._qingneng_account_changed = changed
    if changed:
        context.clear_cookies()
    return changed


def mark_browser_session(context, auth_state_path: Path) -> None:
    revision = getattr(context, "_qingneng_revision", "")
    matched = getattr(context, "_qingneng_login_credentials", None) == getattr(context, "_qingneng_expected_credentials", None)
    if isinstance(revision, str) and revision and matched:
        write_private_json(session_marker_path(auth_state_path), {"revision": revision})
        context._qingneng_account_changed = False
