#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = SCRIPT_DIR.parent
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))
from toolbox_shared.credentials import mark_browser_session, prepare_browser_session, read_login_config
from toolbox_shared.auth_loader import wait_for_login
from toolbox_shared.dates import normalize_date_input
import upload_files as _upload_files
from upload_files import (UPLOAD_TYPES, UploadFile, classify_file, daily_report_name,
                          extract_date_from_name, probe_standard_province_templates,
                          validate_date)
from toolbox_shared.province_excel import (
    number_or_none,
    format_formula_number,
    replace_capacity_in_formula,
    evaluate_simple_excel_formula,
    write_formula_result_as_value,
    write_province_workbook,
)

LOGIN_SOURCE_DIR = WORKSPACE_DIR / "上网电量抓取"

from page_navigation import (
    BASE_URL,
    DAY_REPORT_TENANT_NAMES,
    DAY_REPORT_URL,
    DEFAULT_DAILY_REPORT_TEMPLATE,
    DEFAULT_DAILY_REPORT_TEMPLATE_ID,
    HUANENG_GROUP_URL,
    REPORT_API_TIMEOUT_MS,
    REPORT_URL,
    USERCENTER_URL,
    body_text,
    click_button_by_text,
    create_daily_report,
    current_date_inputs,
    daily_report_exists,
    dump_page_diagnostics,
    enter_huaneng_marketing_platform,
    find_daily_report_template_id,
    find_report_page,
    json_or_text,
    load_day_report_application_tenants,
    open_day_report_page,
    open_report_page,
    page_needs_manual_navigation,
    parse_json_response,
    select_day_report_tenant,
    select_form_option_by_label,
    set_form_input_by_label,
    set_report_date,
    switch_to_day_report_tenant,
)
import page_navigation
import page_navigation as _pages
import upload_flow
import upload_flow as _flow
from upload_flow import (
    dump_upload_diagnostics,
    fetch_capacity_average,
    fetch_startup_max,
    file_payload,
    get_daily_status,
    import_daily_file,
    mark_upload_target,
    parse_api_payload,
    province_output_path,
    update_province_excel_for_upload,
    upload_one_file,
)

LOGIN_URL = f"{BASE_URL}/usercenter/#/login"
DEFAULT_PROVINCE_ID = "44"
DEFAULT_PROVINCE_NAME = "广东省"
DEFAULT_PUBLIC_PROVINCE_AREA_ID = "044"

from toolbox_shared.storage import data_path

PROFILE_DIR = data_path(SCRIPT_DIR / ".browser-profile")
AUTH_STATE_PATH = data_path(SCRIPT_DIR / "auth_state.json")
AUTH_LOCK_PATH = Path(str(AUTH_STATE_PATH) + ".lock")
CONFIG_PATH = data_path(SCRIPT_DIR / "config.json")
FALLBACK_CONFIG_PATH = data_path(LOGIN_SOURCE_DIR / "config.json")
BUILTIN_PROVINCE_TEMPLATE = SCRIPT_DIR / "templates" / "省内数据模板.xlsx"

@dataclass
class Config:
    username: str = ""
    password: str = ""
    headless: bool = False


LOG_SINK: Callable[[str], None] | None = None


def log(message: str) -> None:
    if LOG_SINK is not None:
        LOG_SINK(message)
    else:
        print(message, flush=True)


def short_error(exc: BaseException) -> str:
    return str(exc).split("Call log:")[0].strip()


# Trampolines keep the entry's globals (LOG_SINK, test patches) authoritative.
_pages.log = lambda message: log(message)
_pages.short_error = lambda exc: short_error(exc)
_pages.DIAGNOSTICS_DIR = data_path(SCRIPT_DIR)
_flow.log = lambda message: log(message)
_flow.BUILTIN_PROVINCE_TEMPLATE = BUILTIN_PROVINCE_TEMPLATE
_flow.DIAGNOSTICS_DIR = data_path(SCRIPT_DIR)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def parse_lock_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class AuthStateLock:
    def __init__(self, timeout_seconds: int = 15 * 60) -> None:
        self.timeout_seconds = timeout_seconds
        self.path = AUTH_LOCK_PATH
        self.acquired = False

    def __enter__(self) -> "AuthStateLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        notified = False
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner: dict[str, Any] = {}
                try:
                    owner = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    pass
                created_at = parse_lock_timestamp(owner.get("startedAt"))
                lock_age = time.time() - created_at if created_at else 0
                owner_pid = int(owner.get("pid") or 0)
                if lock_age > 6 * 60 * 60 or (owner_pid and not process_is_alive(owner_pid)):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.time() - started > self.timeout_seconds:
                    raise RuntimeError(f"等待登录态锁超时，请确认没有其他上传/抓取脚本仍在运行：{self.path}")
                if not notified:
                    log(f"检测到其他脚本正在使用同一登录态，等待其完成：{self.path}")
                    notified = True
                time.sleep(2)
                continue

            with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "startedAt": datetime.now().isoformat(),
                        "script": "upload_daily_report",
                    },
                    lock_file,
                    ensure_ascii=False,
                    indent=2,
                )
            self.acquired = True
            log(f"已获得登录态锁：{self.path}")
            return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except PermissionError as cleanup_error:
            if exc_type is None:
                raise
            log(f"清理登录态锁失败，将保留原始错误：{cleanup_error}")
        self.acquired = False


def load_config() -> Config:
    raw = read_login_config(CONFIG_PATH, FALLBACK_CONFIG_PATH)
    return Config(
        username=str(raw.get("username", "") or ""),
        password=str(raw.get("password", "") or ""),
        headless=False,
    )





def choose_files_interactively() -> list[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("无法打开文件选择窗口，请改用命令行传入两个文件路径。") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askopenfilenames(
        title="请选择能销数据 Excel；也可同时选择省内数据",
        initialdir=str(SCRIPT_DIR),
        filetypes=[
            ("Excel 文件", "*.xlsx *.xls"),
            ("所有文件", "*.*"),
        ],
    )
    root.destroy()
    return [Path(item) for item in selected]


def find_province_template(folder: Path, target_date: str) -> UploadFile:
    return _upload_files.find_province_template(
        folder, target_date, template=BUILTIN_PROVINCE_TEMPLATE, log=log)


def prepare_upload_files(paths: list[Path]) -> tuple[str, list[UploadFile]]:
    return _upload_files.prepare_upload_files(
        paths, template=BUILTIN_PROVINCE_TEMPLATE, log=log)






def load_saved_auth_state(context) -> None:
    if not AUTH_STATE_PATH.exists():
        return
    try:
        state = json.loads(AUTH_STATE_PATH.read_text(encoding="utf-8"))
        cookies = state.get("cookies") or []
        if cookies:
            context.add_cookies(cookies)
    except Exception as exc:
        log(f"读取 auth_state.json 失败，将忽略：{exc}")


def save_auth_state(context) -> None:
    log("正在保存集团上传登录态...")
    AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_STATE_PATH.write_text(
        json.dumps(context.storage_state(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    mark_browser_session(context, AUTH_STATE_PATH)
    log("集团上传登录态已保存。")


def close_upload_context(context) -> None:
    """Close task pages explicitly and keep slow browser shutdown visible."""
    original_error = sys.exc_info()[1]
    log("正在关闭集团上传浏览器...")
    reminder = threading.Timer(
        15,
        lambda: log("仍在等待集团上传浏览器退出；请勿因等待而重复提交数据。"),
    )
    reminder.daemon = True
    reminder.start()
    try:
        # Stop the report application's background activity before closing the
        # persistent context. Do not run website beforeunload confirmation UI.
        for page in list(context.pages):
            try:
                page.close(run_before_unload=False)
            except PlaywrightError as exc:
                log(f"关闭上传页面失败，继续关闭浏览器：{short_error(exc)}")
        context.close()
    except Exception as exc:
        if original_error is None:
            raise
        log(f"关闭上传浏览器失败，将保留原始错误：{short_error(exc)}")
    else:
        log("集团上传浏览器已关闭。")
    finally:
        reminder.cancel()


def playwright_proxy() -> dict[str, str] | None:
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(name)
        if value:
            return {"server": value}
    return None


def launch_context(playwright, *, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    launch_options = {
        "headless": headless,
        "accept_downloads": True,
    }
    proxy = playwright_proxy()
    if proxy is not None:
        launch_options["proxy"] = proxy
    try:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            channel=None if os.environ.get("TOOLBOX_PORTABLE") == "1" else "chrome",
            **launch_options,
        )
    except PlaywrightError:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            **launch_options,
        )
    if not prepare_browser_session(context, AUTH_STATE_PATH):
        load_saved_auth_state(context)
    return context


def is_logged_in(context) -> bool:
    urls = [
        f"{BASE_URL}/gdfire/api/pf/tenant/user/info",
        f"{BASE_URL}/usercenter/web/pf/tenant/user/info",
    ]
    for url in urls:
        try:
            response = context.request.get(url, timeout=30000)
        except PlaywrightError:
            continue
        if response.status != 200:
            continue
        try:
            data = response.json()
        except Exception:
            continue
        if data.get("retCode") == "T200" and bool(data.get("data", {}).get("tenantId")):
            return True
    return False


def rsa_encrypt_password(public_key_b64: str, password: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_der_public_key(base64.b64decode(public_key_b64))
    encrypted = public_key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("ascii")


def try_api_login(context, username: str, password: str) -> bool:
    key_response = context.request.get(f"{BASE_URL}/usercenter/web/pf/login/info/publicKey", timeout=60000)
    key_data = parse_json_response(key_response, "读取登录公钥")
    public_key = key_data.get("data")
    if not isinstance(public_key, str) or not public_key:
        raise RuntimeError("读取登录公钥失败：返回中没有公钥")

    encrypted_password = rsa_encrypt_password(public_key, password)
    login_response = context.request.post(
        f"{BASE_URL}/usercenter/web/login",
        params={"loginMode": 2, "username": username, "password": encrypted_password},
        timeout=60000,
    )
    payload = json_or_text(login_response)
    if login_response.status >= 400:
        raise RuntimeError(f"登录接口 HTTP {login_response.status}: {str(payload)[:300]}")
    if isinstance(payload, dict) and payload.get("retCode") not in (None, "T200"):
        raise RuntimeError(f"登录失败：{payload.get('retMsg') or payload}")

    page = context.new_page()
    try:
        page.goto(USERCENTER_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    finally:
        page.close()

    logged_in = is_logged_in(context)
    if logged_in:
        save_auth_state(context)
    return logged_in


def try_ui_login(context, username: str, password: str) -> bool:
    page = context.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    try:
        page.locator("#username, input[placeholder*='工号'], input[placeholder*='手机号'], input[placeholder*='账号']").first.fill(
            username, timeout=10000
        )
        page.locator("#password, input[type='password']").first.fill(password, timeout=10000)
        page.locator("button:has-text('登 录'), button:has-text('登录'), button[type='submit']").first.click(timeout=10000)
    except (PlaywrightTimeoutError, PlaywrightError):
        pass

    deadline = time.time() + 45
    while time.time() < deadline:
        if is_logged_in(context):
            save_auth_state(context)
            page.close()
            return True
        time.sleep(2)
    page.close()
    return False


def try_auto_login(context, username: str, password: str) -> bool:
    context._qingneng_login_credentials = (username, password)
    try:
        log("正在尝试接口登录...")
        if try_api_login(context, username, password):
            return True
        log("接口登录后仍未检测到登录态，改用页面登录...")
    except Exception as exc:
        log(f"接口登录失败：{short_error(exc)}")
        log("改用页面登录...")
    return try_ui_login(context, username, password)


def ensure_login(context, config: Config) -> None:
    if getattr(context, "_qingneng_account_changed", False) is not True and is_logged_in(context):
        return
    if not config.username or not config.password:
        raise RuntimeError("未检测到有效登录态，请在任务中心下方填写清能网站账号密码。")
    log("正在使用已配置的清能网站账号密码自动登录...")
    if not try_auto_login(context, config.username, config.password):
        raise RuntimeError("自动登录失败，请在清能网站账号区检查账号密码后重新运行任务。")
    log("自动登录成功。")


def interactive_login(context, config: Config) -> None:
    if getattr(context, "_qingneng_account_changed", False) is True or not is_logged_in(context):
        if config.username and config.password and try_auto_login(context, config.username, config.password):
            log("自动登录成功。")
        else:
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            log("请在打开的浏览器里完成登录；工具会自动检测并保存登录态（最长等待5分钟）。")
            try:
                wait_for_login(lambda: is_logged_in(context))
            finally:
                page.close()

    save_auth_state(context)
    log("日报系统登录态已保存。")


































def smoke_daily_report_page(*, headless: bool = True) -> None:
    config = load_config()
    config.headless = headless
    with AuthStateLock():
        with sync_playwright() as playwright:
            context = launch_context(playwright, headless=config.headless)
            try:
                ensure_login(context, config)
                page = open_day_report_page(context, allow_manual=not headless)
                try:
                    count = page.get_by_text("新建报表", exact=True).count()
                except PlaywrightError:
                    count = 0
                if count <= 0:
                    diagnostics = dump_page_diagnostics(page, "daily_report_smoke_failed")
                    raise RuntimeError(f"已打开页面但没有找到“新建报表”入口。诊断已保存：{diagnostics}")
                save_auth_state(context)
                log("日报管理页面自检通过：已找到“新建报表”。")
            finally:
                context.close()




























def run_api_upload(
    paths: list[Path],
    *,
    force: bool = False,
    province_id: str = DEFAULT_PROVINCE_ID,
    create_report: bool = True,
) -> None:
    target_date, uploads = prepare_upload_files(paths)
    log(f"本次上传日期：{target_date}")
    log(f"目标省份：{DEFAULT_PROVINCE_NAME}（provinceId={province_id}）")

    config = load_config()
    config.headless = True

    with AuthStateLock():
        with sync_playwright() as playwright:
            context = launch_context(playwright, headless=config.headless)
            try:
                login_refreshed = False
                try:
                    uploads = update_province_excel_for_upload(context, target_date, uploads)
                except PermissionError as exc:
                    raise PermissionError(
                        "macOS 拒绝访问所选 Excel。请在工具箱中重新选择该文件；"
                        "如仍失败，请在“系统设置 > 隐私与安全性 > 文件和文件夹”中"
                        "允许电力工具箱访问桌面。"
                    ) from exc
                except Exception:
                    if login_refreshed:
                        raise
                    log("读取网页接口失败，正在刷新登录态后重试...")
                    ensure_login(context, config)
                    login_refreshed = True
                    uploads = update_province_excel_for_upload(context, target_date, uploads)

                for item in uploads:
                    log(f"- {UPLOAD_TYPES[item.kind]['name']}：{item.path}")

                for upload in uploads:
                    spec = UPLOAD_TYPES[upload.kind]
                    data_type = str(spec["data_type"])
                    try:
                        status = get_daily_status(context, target_date, data_type, province_id)
                    except Exception:
                        if login_refreshed:
                            raise
                        log("日报接口暂不可用，正在刷新登录态后重试...")
                        ensure_login(context, config)
                        login_refreshed = True
                        status = get_daily_status(context, target_date, data_type, province_id)
                    uploaded = bool(status and status.get("dataStatus") == 1)
                    if uploaded and not force:
                        update_time = status.get("updateTime") or "未知时间"
                        log(f"{spec['name']} 已上传，跳过。更新时间：{update_time}")
                        continue

                    if uploaded and force:
                        log(f"{spec['name']} 已上传，按 --force 要求重新导入。")
                    else:
                        log(f"{spec['name']} 未上传，开始导入。")

                    payload = import_daily_file(context, upload, target_date, province_id)
                    errors = payload.get("data") or []
                    if errors:
                        raise RuntimeError(f"{spec['name']} 导入返回校验信息：{json.dumps(errors, ensure_ascii=False)[:1000]}")
                    log(f"{spec['name']} 导入成功。")
                if create_report:
                    create_daily_report(context, target_date, headless=config.headless)
                log("数据上传和日报步骤已完成，正在收尾...")
                save_auth_state(context)
            finally:
                close_upload_context(context)
        log("集团上传浏览器驱动已退出。")

    log("集团上传登录态锁已释放。")
    log("\n处理完成。")


def run_browser_upload(paths: list[Path], *, headless: bool, create_report: bool = True) -> None:
    target_date, uploads = prepare_upload_files(paths)
    log(f"本次上传日期：{target_date}")

    config = load_config()
    config.headless = headless

    with AuthStateLock():
        with sync_playwright() as playwright:
            context = launch_context(playwright, headless=config.headless)
            try:
                ensure_login(context, config)
                uploads = update_province_excel_for_upload(context, target_date, uploads)
                for item in uploads:
                    log(f"- {UPLOAD_TYPES[item.kind]['name']}：{item.path}")
                page = open_report_page(context, allow_manual=not headless)
                set_report_date(page, target_date)
                for upload in uploads:
                    upload_one_file(page, upload)
                if create_report:
                    create_daily_report(context, target_date, headless=headless)
                save_auth_state(context)
                log("\n两个文件处理完成，请在网页上确认上传结果。")
                if not headless:
                    log("浏览器窗口会保持打开；确认无误后可手动关闭。")
                    input("按 Enter 结束脚本...")
            finally:
                context.close()


def run_gui() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        raise RuntimeError("当前 Python 环境不可用 tkinter，无法打开图形界面。") from exc

    root = tk.Tk()
    root.title("集团日报上传")
    root.geometry("760x520")
    root.minsize(680, 460)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    selected_paths: list[Path] = []
    log_events: queue.Queue[tuple[str, str]] = queue.Queue()
    force_var = tk.BooleanVar(value=False)
    summary_var = tk.StringVar(value="请选择能销数据 Excel；省内数据缺省时使用内置模板")
    file_lines_var = tk.StringVar(value="未选择文件")

    def gui_log(message: str) -> None:
        log_events.put(("log", message))

    def append_log(message: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", message + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def refresh_selection_text() -> None:
        if not selected_paths:
            file_lines_var.set("未选择文件")
            summary_var.set("请选择能销数据 Excel；省内数据缺省时使用内置模板")
            return
        try:
            target_date, uploads = prepare_upload_files(selected_paths)
            lines = [f"{UPLOAD_TYPES[item.kind]['name']}：{item.path.name}" for item in uploads]
            file_lines_var.set("\n".join(lines))
            summary_var.set(f"已识别日期：{target_date}；目标省份：{DEFAULT_PROVINCE_NAME}")
        except Exception as exc:
            file_lines_var.set("\n".join(path.name for path in selected_paths))
            summary_var.set(f"文件校验失败：{exc}")

    def choose_files() -> None:
        nonlocal selected_paths
        selected = filedialog.askopenfilenames(
            title="请选择能销数据 Excel；也可同时选择省内数据",
            initialdir=str(SCRIPT_DIR),
            filetypes=[
                ("Excel 文件", "*.xlsx *.xls"),
                ("所有文件", "*.*"),
            ],
        )
        if not selected:
            return
        selected_paths = [Path(item) for item in selected]
        refresh_selection_text()

    def set_busy(is_busy: bool) -> None:
        state = "disabled" if is_busy else "normal"
        choose_button.configure(state=state)
        upload_button.configure(state=state)
        login_button.configure(state=state)
        force_check.configure(state=state)

    def start_upload() -> None:
        if not selected_paths:
            messagebox.showwarning("缺少文件", "请先选择能销数据 Excel 文件。")
            return
        try:
            prepare_upload_files(selected_paths)
        except Exception as exc:
            messagebox.showerror("文件校验失败", str(exc))
            return

        set_busy(True)
        append_log("")
        append_log("开始处理...")

        def worker() -> None:
            global LOG_SINK
            old_sink = LOG_SINK
            LOG_SINK = gui_log
            try:
                run_api_upload(selected_paths, force=force_var.get())
                log_events.put(("done", "处理完成。"))
            except Exception as exc:
                log_events.put(("error", str(exc)))
            finally:
                LOG_SINK = old_sink
                log_events.put(("idle", ""))

        threading.Thread(target=worker, daemon=True).start()

    def start_login() -> None:
        set_busy(True)
        append_log("")
        append_log("打开浏览器登录窗口...")

        def worker() -> None:
            global LOG_SINK
            old_sink = LOG_SINK
            LOG_SINK = gui_log
            try:
                config = load_config()
                with sync_playwright() as playwright:
                    context = launch_context(playwright, headless=False)
                    try:
                        interactive_login(context, config)
                    finally:
                        context.close()
                log_events.put(("done", "登录态已保存。"))
            except Exception as exc:
                log_events.put(("error", str(exc)))
            finally:
                LOG_SINK = old_sink
                log_events.put(("idle", ""))

        threading.Thread(target=worker, daemon=True).start()

    def poll_log_events() -> None:
        try:
            while True:
                kind, message = log_events.get_nowait()
                if kind == "log":
                    append_log(message)
                elif kind == "done":
                    append_log(message)
                    messagebox.showinfo("完成", message)
                elif kind == "error":
                    append_log(f"失败：{message}")
                    messagebox.showerror("失败", message)
                elif kind == "idle":
                    set_busy(False)
        except queue.Empty:
            pass
        root.after(120, poll_log_events)

    outer = ttk.Frame(root, padding=16)
    outer.pack(fill="both", expand=True)

    header = ttk.Frame(outer)
    header.pack(fill="x")
    ttk.Label(header, text="集团日报上传", font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
    ttk.Label(header, text=f"{DEFAULT_PROVINCE_NAME}  接口模式", foreground="#555").pack(side="right")

    file_panel = ttk.LabelFrame(outer, text="文件", padding=12)
    file_panel.pack(fill="x", pady=(14, 10))
    ttk.Label(file_panel, textvariable=summary_var).pack(anchor="w")
    ttk.Label(file_panel, textvariable=file_lines_var, foreground="#333", justify="left").pack(anchor="w", pady=(8, 0))

    actions = ttk.Frame(outer)
    actions.pack(fill="x", pady=(0, 10))
    from toolbox_shared.file_button import FileSelectButton
    choose_button = FileSelectButton(actions, text="选择文件", command=choose_files)
    choose_button.pack(side="left")
    upload_button = ttk.Button(actions, text="开始上传", command=start_upload)
    upload_button.pack(side="left", padx=(8, 0))
    login_button = ttk.Button(actions, text="登录/刷新登录态", command=start_login)
    login_button.pack(side="left", padx=(8, 0))
    force_check = ttk.Checkbutton(actions, text="已上传也覆盖", variable=force_var)
    force_check.pack(side="right")

    log_panel = ttk.LabelFrame(outer, text="日志", padding=8)
    log_panel.pack(fill="both", expand=True)
    log_box = tk.Text(log_panel, height=12, wrap="word", state="disabled", font=("Consolas", 10))
    log_scroll = ttk.Scrollbar(log_panel, orient="vertical", command=log_box.yview)
    log_box.configure(yscrollcommand=log_scroll.set)
    log_box.pack(side="left", fill="both", expand=True)
    log_scroll.pack(side="right", fill="y")

    append_log("操作：选择能销 Excel -> 开始上传。上传完成后会自动新建集团日报。")
    poll_log_events()
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="上传集团日报省内数据和能销数据")
    parser.add_argument("files", nargs="*", help="可选：两个 Excel 文件路径。不传则弹出文件选择窗口。")
    parser.add_argument("--login", action="store_true", help="登录并保存会话")
    parser.add_argument("--headless", action="store_true", help="无界面运行")
    parser.add_argument("--browser", action="store_true", help="使用浏览器页面上传模式")
    parser.add_argument("--force", action="store_true", help="接口模式下即使当天已上传也重新导入")
    parser.add_argument("--skip-report", action="store_true", help="只上传数据，不自动新建集团日报")
    parser.add_argument("--smoke-report", action="store_true", help="只测试能否进入日报管理页面，不上传也不新建报表")
    parser.add_argument("--cli", action="store_true", help="不打开图形界面，使用命令行/文件选择模式")
    args = parser.parse_args()

    config = load_config()
    try:
        if args.login:
            with AuthStateLock():
                with sync_playwright() as playwright:
                    context = launch_context(playwright, headless=False)
                    try:
                        interactive_login(context, config)
                    finally:
                        context.close()
            return 0

        if args.smoke_report:
            smoke_daily_report_page(headless=args.headless)
            return 0

        if not args.files and not args.cli and not args.browser and not args.headless:
            run_gui()
            return 0

        paths = [Path(item) for item in args.files] if args.files else choose_files_interactively()
        if args.browser:
            run_browser_upload(paths, headless=args.headless, create_report=not args.skip_report)
        else:
            run_api_upload(paths, force=args.force, create_report=not args.skip_report)
        return 0
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:
        print(f"\n失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
