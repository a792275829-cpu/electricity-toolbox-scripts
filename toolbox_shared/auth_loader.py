"""Load the shared login code with an independent browser state per tool."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from .storage import data_path


def wait_for_login(is_ready: Callable[[], bool], *, timeout_seconds: float = 300) -> None:
    """GUI-launched scripts have no stdin; detect completion in the browser."""
    deadline = time.monotonic() + timeout_seconds
    while not is_ready():
        if time.monotonic() >= deadline:
            raise RuntimeError("等待登录超时，请完成浏览器中的登录后重新运行任务。")
        time.sleep(2)


def standalone_login(
    auth: ModuleType,
    config: object,
    *,
    app_url: str,
    log: Callable[[str], None],
    label: str = "独立",
    timeout_seconds: float = 300,
    page_timeout_ms: float = 60_000,
) -> None:
    """Headed login for standalone CLI tools; one implementation for every tool.

    ``auth`` is the module returned by :func:`load_login_module`, so each tool keeps
    its own state file, lock and browser profile while sharing this flow.
    """
    from playwright.sync_api import sync_playwright

    username = str(getattr(config, "username", "") or "")
    password = str(getattr(config, "password", "") or "")
    with auth.AuthStateLock():
        with sync_playwright() as playwright:
            context = auth.launch_context(playwright, headless=False)
            try:
                if username and password and auth.try_auto_login(context, username, password):
                    auth.save_auth_state(context)
                    log(f"自动登录成功，{label}登录态已保存。")
                    return
                page = context.new_page()
                try:
                    # Matching the longest navigation wait used by the original tools.
                    page.goto(app_url, wait_until="domcontentloaded", timeout=page_timeout_ms)
                    log(f"请在打开的浏览器中完成登录；工具会自动检测并保存登录态（最长等待{int(timeout_seconds // 60)}分钟）。")
                    wait_for_login(lambda: auth.is_logged_in(context), timeout_seconds=timeout_seconds)
                    auth.save_auth_state(context)
                finally:
                    page.close()
                log(f"登录成功，{label}登录态已保存。")
            finally:
                context.close()


def load_login_module(
    workspace: Path,
    *,
    module_name: str,
    state_dir: Path,
    config_path: Path | None = None,
    app_url: str | None = None,
    copy_initial_state: bool = False,
) -> ModuleType:
    """Reuse the login implementation without sharing cookies, locks or profiles."""
    script = workspace / "上网电量抓取" / "export_online_energy.py"
    if not script.is_file():
        raise FileNotFoundError(f"找不到工具箱登录脚本：{script}")
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载工具箱登录脚本：{script}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise

    state_dir = data_path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    target_state = state_dir / "auth_state.json"
    if copy_initial_state:
        source_state = module.AUTH_STATE_PATH
        if not target_state.exists() and source_state.is_file():
            descriptor, temporary_name = tempfile.mkstemp(prefix=".auth_state.", dir=state_dir)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as destination, source_state.open("rb") as source:
                    shutil.copyfileobj(source, destination)
                temporary.chmod(0o600)
                temporary.replace(target_state)
            finally:
                temporary.unlink(missing_ok=True)

    module.PROFILE_DIR = state_dir / ".browser-profile"
    module.AUTH_STATE_PATH = target_state
    module.AUTH_LOCK_PATH = Path(str(target_state) + ".lock")
    if config_path is not None:
        module.CONFIG_PATH = data_path(config_path)
    if app_url is not None:
        module.FIRE_APP_URL = app_url
    return module
