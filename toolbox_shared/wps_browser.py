"""Dedicated WPS Chrome lifecycle and CDP transport; no GUI imports."""
from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
import time as time_module
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ImportError:
    PlaywrightTimeoutError = Exception

from .wps_paths import (
    BASE_DIR,
    DATA_DIR,
    CONFIG_PATH,
    DEFAULT_CONFIG_PATH,
    PROFILE_DIR,
    WPS_CHROME_PID_PATH,
    WPS_CHROME_STATE_PATH,
    WPS_CHROME_LAUNCH_VERSION,
    WPS_CHROME_START_LOCK,
    LOG_DIR,
    MAX_RECENT_URLS,
    DOCUMENT_CONFIGS_MIN_HEIGHT,
    CDP_CONNECT_TIMEOUT_MS,
    WPS_LOGIN_STATUS_URL,
    LOGIN_STATUS_POLL_MS,
)

def browser_has_page(browser, url: str) -> bool:
    target_key = url.rstrip("/").split("/")[-1]
    return any(
        target_key and target_key in page.url
        for context in browser.contexts
        for page in context.pages
    )



def ensure_background_cdp_target(browser, cdp_url: str, url: str) -> bool:
    if browser_has_page(browser, url):
        return False
    session = browser.new_browser_cdp_session()
    session.send("Target.createTarget", {"url": url, "background": True})
    target_key = url.rstrip("/").split("/")[-1]
    deadline = time_module.monotonic() + 10
    while time_module.monotonic() < deadline:
        _base_url, targets = cdp_page_targets(cdp_url)
        if any(
            target.get("type") == "page"
            and target_key
            and target_key in str(target.get("url") or "")
            for target in targets
        ):
            return True
        time_module.sleep(0.05)
    raise RuntimeError("已创建后台表格页面，但加载超时，请重试。")



def find_or_open_page(context, url: str):
    target_key = url.rstrip("/").split("/")[-1]
    for page in context.pages:
        if target_key and target_key in page.url:
            return page
    for page in context.pages:
        if "kdocs.cn" in page.url:
            page.goto(url, wait_until="commit", timeout=20000)
            return page
    page = next(
        (
            candidate
            for candidate in context.pages
            if candidate.url in {"", "about:blank", "chrome://newtab/"}
        ),
        None,
    )
    if page is None:
        page = context.pages[0] if context.pages else context.new_page()
    page.goto(url, wait_until="commit", timeout=20000)
    return page



def cdp_candidates(cdp_url: str) -> list[str]:
    raw = cdp_url.strip() or "http://localhost:9222"
    candidates = [raw]
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.port:
        for host in ("localhost", "[::1]", "127.0.0.1"):
            netloc = f"{host}:{parsed.port}"
            candidate = urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates



def resolve_cdp_endpoint(cdp_url: str) -> str:
    if cdp_url.startswith("ws://") or cdp_url.startswith("wss://"):
        return cdp_url
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    errors: list[str] = []
    for candidate in cdp_candidates(cdp_url):
        version_url = candidate.rstrip("/") + "/json/version"
        try:
            with opener.open(version_url, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            ws_url = data.get("webSocketDebuggerUrl")
            if ws_url:
                return ws_url
            errors.append(f"{version_url}：浏览器未返回连接地址")
        except Exception as exc:
            errors.append(f"{version_url}: {exc}")
    raise RuntimeError("无法获取浏览器连接地址。" + " | ".join(errors))



def find_macos_chrome() -> Path | None:
    # Keep the automation browser on a distinct application bundle. If the
    # regular Chrome binary is used for a long-lived headless CDP process,
    # LaunchServices may route links from WeChat and other apps into that
    # invisible instance because both share com.google.Chrome.
    candidates = [
        Path("/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        Path.home() / "Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)



def managed_cdp_host_port(cdp_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(cdp_url.strip() or "http://127.0.0.1:9222")
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("专用浏览器的连接地址必须指向本机，例如 http://127.0.0.1:9222。")
    return "127.0.0.1", parsed.port or 9222



def build_wps_chrome_command(
    chrome_path: Path,
    cdp_url: str,
    initial_url: str = "https://www.kdocs.cn/",
    *,
    headless: bool = False,
) -> list[str]:
    host, port = managed_cdp_host_port(cdp_url)
    executable = [str(chrome_path)]
    # A macOS application can retain an "Open using Rosetta" preference even
    # when Chrome and Python are both universal/native. KDocs is CPU-heavy and
    # becomes dramatically slower when its renderer is translated. Force the
    # dedicated instance to use Chrome's native Apple Silicon slice.
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        executable = ["/usr/bin/arch", "-arm64", str(chrome_path)]
    command = executable + [
        f"--remote-debugging-address={host}",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        # Do not let Chrome keep a hidden app/background-mode process after
        # the last dedicated WPS window is closed.
        "--disable-background-mode",
        # The Tk config dialog normally covers Chrome on macOS. Keep the WPS
        # renderer at foreground speed while that dedicated window is occluded.
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    if headless:
        command.extend(["--headless=new", "--window-size=1400,900"])
    command.append(initial_url)
    return command



def try_resolve_cdp_endpoint(cdp_url: str, timeout: float = 0.5) -> str | None:
    if cdp_url.startswith("ws://") or cdp_url.startswith("wss://"):
        return cdp_url
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for candidate in cdp_candidates(cdp_url):
        try:
            with opener.open(candidate.rstrip("/") + "/json/version", timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            websocket_url = data.get("webSocketDebuggerUrl")
            if websocket_url:
                return str(websocket_url)
        except Exception:
            continue
    return None



def cdp_page_targets(
    cdp_url: str, timeout: float = 0.5
) -> tuple[str | None, list[dict[str, Any]]]:
    if cdp_url.startswith("ws://") or cdp_url.startswith("wss://"):
        return None, []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for candidate in cdp_candidates(cdp_url):
        try:
            with opener.open(candidate.rstrip("/") + "/json/list", timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, list):
                return candidate.rstrip("/"), [item for item in data if isinstance(item, dict)]
        except Exception:
            continue
    return None, []



def ensure_wps_cdp_page(
    cdp_url: str,
    initial_url: str = "https://www.kdocs.cn/",
    *,
    require_initial_url: bool = False,
) -> bool:
    base_url, targets = cdp_page_targets(cdp_url)
    page_targets = [target for target in targets if target.get("type") == "page"]
    target_key = initial_url.rstrip("/").split("/")[-1]
    if require_initial_url and target_key:
        if any(target_key in str(target.get("url") or "") for target in page_targets):
            return False
    elif page_targets:
        return False
    if base_url is None:
        raise RuntimeError("无法读取专用浏览器的页面列表。")

    encoded_url = urllib.parse.quote(initial_url, safe="")
    request = urllib.request.Request(
        f"{base_url}/json/new?{encoded_url}", method="PUT"
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        created = json.loads(response.read().decode("utf-8"))
    if not isinstance(created, dict) or created.get("type") != "page":
        raise RuntimeError("专用浏览器未能创建页面，请重试。")
    return True



def managed_chrome_state_version() -> int:
    try:
        state = json.loads(WPS_CHROME_STATE_PATH.read_text(encoding="utf-8"))
        return int(state.get("version") or 0)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0



def managed_chrome_command(pid: int) -> str:
    if sys.platform != "darwin":
        return ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except subprocess.SubprocessError:
        return ""
    return result.stdout.strip()



def is_managed_chrome_command(command: str) -> bool:
    profile_argument = f"--user-data-dir={PROFILE_DIR}"
    is_browser_main_process = (
        "/Contents/MacOS/Google Chrome" in command
        or "/Contents/MacOS/Chromium" in command
    )
    return (
        profile_argument in command
        and is_browser_main_process
        and "Google Chrome Helper" not in command
        and "--type=" not in command
    )



def discover_managed_chrome_pid() -> int | None:
    """Find a dedicated Chrome whose PID changed without updating our PID file."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.SubprocessError:
        return None

    candidates: list[tuple[bool, int]] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        pid = int(fields[0])
        command = fields[1]
        if is_managed_chrome_command(command):
            candidates.append(("--remote-debugging-port=" in command, pid))
    if not candidates:
        return None
    return max(candidates)[1]



def remember_managed_chrome(pid: int, version: int, *, headless: bool = False) -> None:
    WPS_CHROME_PID_PATH.write_text(str(pid), encoding="utf-8")
    WPS_CHROME_STATE_PATH.write_text(
        json.dumps(
            {"pid": pid, "version": version, "headless": headless},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )



def clear_managed_chrome_state(pid: int) -> None:
    try:
        recorded_pid = int(WPS_CHROME_PID_PATH.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        recorded_pid = None
    if recorded_pid != pid:
        return
    for path in (WPS_CHROME_PID_PATH, WPS_CHROME_STATE_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass



def managed_chrome_pid() -> int | None:
    recorded_pid: int | None = None
    try:
        recorded_pid = int(WPS_CHROME_PID_PATH.read_text(encoding="utf-8").strip())
        os.kill(recorded_pid, 0)
        if sys.platform == "darwin":
            if not is_managed_chrome_command(managed_chrome_command(recorded_pid)):
                raise ProcessLookupError
        return recorded_pid
    except (
        FileNotFoundError,
        ValueError,
        ProcessLookupError,
        PermissionError,
        subprocess.SubprocessError,
    ):
        pass

    discovered_pid = discover_managed_chrome_pid()
    if discovered_pid is None:
        if recorded_pid is not None:
            clear_managed_chrome_state(recorded_pid)
        return None
    try:
        remember_managed_chrome(discovered_pid, managed_chrome_state_version())
    except OSError:
        pass
    return discovered_pid



def managed_chrome_launch_version(pid: int) -> int:
    try:
        state = json.loads(WPS_CHROME_STATE_PATH.read_text(encoding="utf-8"))
        if int(state.get("pid") or 0) != pid:
            return 0
        return int(state.get("version") or 0)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0



def managed_chrome_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    if sys.platform != "darwin":
        return True
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except subprocess.SubprocessError:
        return True
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")



def stop_managed_chrome(pid: int, log, reason: str, timeout_seconds: float = 8) -> None:
    log(f"正在重启专用浏览器：{reason}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_managed_chrome_state(pid)
        return

    deadline = time_module.monotonic() + timeout_seconds
    while managed_chrome_is_running(pid) and time_module.monotonic() < deadline:
        time_module.sleep(0.2)
    if managed_chrome_is_running(pid):
        log("专用浏览器未正常关闭，正在结束其进程。")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            clear_managed_chrome_state(pid)
            return
        force_deadline = time_module.monotonic() + 2
        while managed_chrome_is_running(pid) and time_module.monotonic() < force_deadline:
            time_module.sleep(0.1)
    if managed_chrome_is_running(pid):
        raise RuntimeError(
            "专用浏览器无响应，自动重启失败。"
            "请关闭专用浏览器窗口后重试。"
        )
    clear_managed_chrome_state(pid)



def managed_chrome_is_headless(pid: int) -> bool:
    return "--headless" in managed_chrome_command(pid)



def stop_outdated_managed_chrome(
    log, cdp_url: str, *, headless: bool = False
) -> None:
    pid = managed_chrome_pid()
    if pid is None:
        return
    if managed_chrome_launch_version(pid) < WPS_CHROME_LAUNCH_VERSION:
        reason = "正在应用更新后的浏览器设置"
    elif managed_chrome_is_headless(pid) != headless:
        reason = (
            "正在切换为后台运行"
            if headless
            else "正在打开浏览器供您登录"
        )
    else:
        return
    stop_managed_chrome(
        pid,
        log,
        reason,
    )



def _ensure_wps_dedicated_chrome(
    cdp_url: str = "http://127.0.0.1:9222",
    log=lambda _message: None,
    timeout_seconds: float = 15,
    initial_url: str = "https://www.kdocs.cn/",
    preload_url: bool = False,
    headless: bool = False,
) -> dict[str, Any]:
    stop_outdated_managed_chrome(log, cdp_url, headless=headless)
    existing_endpoint = try_resolve_cdp_endpoint(cdp_url)
    if existing_endpoint:
        ensure_wps_cdp_page(
            cdp_url,
            initial_url,
            require_initial_url=preload_url,
        )
        log("已连接正在运行的专用浏览器。")
        return {
            "started": False,
            "endpoint": existing_endpoint,
            "headless": headless,
        }
    existing_pid = managed_chrome_pid()
    if existing_pid is not None:
        # Chrome can briefly stop answering /json/version while a heavy KDocs
        # page is busy. Give the owned process time to recover instead of
        # launching a second process against the same profile.
        recovery_deadline = time_module.monotonic() + 3
        while time_module.monotonic() < recovery_deadline:
            endpoint = try_resolve_cdp_endpoint(cdp_url)
            if endpoint:
                ensure_wps_cdp_page(
                    cdp_url,
                    initial_url,
                    require_initial_url=preload_url,
                )
                log("已连接正在运行的专用浏览器。")
                return {
                    "started": False,
                    "endpoint": endpoint,
                    "pid": existing_pid,
                    "headless": headless,
                }
            time_module.sleep(0.2)
        stop_managed_chrome(
            existing_pid,
            log,
            "浏览器仍在运行，但连接无响应",
        )
    if sys.platform != "darwin":
        raise RuntimeError("当前系统不支持自动启动专用浏览器，请手动打开后连接。")

    chrome_path = find_macos_chrome()
    if chrome_path is None:
        raise RuntimeError("未找到可用浏览器，请先安装谷歌浏览器。")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        PROFILE_DIR.chmod(0o700)
    except OSError:
        pass
    command = build_wps_chrome_command(
        chrome_path,
        cdp_url,
        initial_url,
        headless=headless,
    )
    mode_label = "后台运行" if headless else "供您登录"
    log(f"正在启动专用浏览器（{mode_label}）…")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    remember_managed_chrome(
        process.pid,
        WPS_CHROME_LAUNCH_VERSION,
        headless=headless,
    )
    deadline = time_module.monotonic() + timeout_seconds
    while time_module.monotonic() < deadline:
        endpoint = try_resolve_cdp_endpoint(cdp_url)
        if endpoint:
            ensure_wps_cdp_page(
                cdp_url,
                initial_url,
                require_initial_url=preload_url,
            )
            if headless:
                log("专用浏览器已在后台就绪。")
            else:
                log("专用浏览器已打开；如出现登录页面，请先完成登录。")
            return {
                "started": True,
                "endpoint": endpoint,
                "pid": process.pid,
                "headless": headless,
            }
        if process.poll() is not None:
            break
        time_module.sleep(0.2)
    raise RuntimeError(
        "无法连接专用浏览器。"
        "请关闭使用该登录资料的浏览器窗口后重试。"
    )



def ensure_wps_dedicated_chrome(
    cdp_url: str = "http://127.0.0.1:9222",
    log=lambda _message: None,
    timeout_seconds: float = 15,
    initial_url: str = "https://www.kdocs.cn/",
    preload_url: bool = False,
    headless: bool = False,
) -> dict[str, Any]:
    with WPS_CHROME_START_LOCK:
        return _ensure_wps_dedicated_chrome(
            cdp_url,
            log,
            timeout_seconds,
            initial_url,
            preload_url,
            headless,
        )



def open_browser_context(
    playwright,
    browser_mode: str,
    cdp_url: str,
    log,
    initial_url: str | None = None,
    *,
    for_login_check: bool = False,
):
    if browser_mode in {"managed_cdp", "cdp"}:
        if browser_mode == "managed_cdp":
            # Automatic reads/writes use the saved WPS profile in a headless
            # Chrome process. Only the explicit login button opens a window.
            if for_login_check and try_resolve_cdp_endpoint(cdp_url):
                # Do not close the visible browser while the user is logging in.
                ensure_wps_cdp_page(cdp_url)
            else:
                ensure_wps_dedicated_chrome(cdp_url, log, headless=True)
        else:
            log(f"正在连接已打开的浏览器：{cdp_url}")
        endpoint = resolve_cdp_endpoint(cdp_url)
        log("正在连接浏览器并检查表格页面…")
        try:
            browser = playwright.chromium.connect_over_cdp(
                endpoint, timeout=CDP_CONNECT_TIMEOUT_MS
            )
        except Exception as exc:
            if browser_mode != "managed_cdp":
                raise
            if "context management is not supported" in str(exc):
                log("专用浏览器中没有页面，正在打开金山文档并重新连接。")
                ensure_wps_cdp_page(cdp_url)
            elif isinstance(exc, PlaywrightTimeoutError) and not for_login_check:
                # The HTTP status endpoint can still respond while every renderer
                # is stuck. Restart only the browser owned by this tool, before
                # any spreadsheet operation has begun.
                with WPS_CHROME_START_LOCK:
                    pid = managed_chrome_pid()
                    if pid is None:
                        raise RuntimeError(
                            "浏览器连接超时，请关闭专用浏览器后重新打开。"
                        ) from exc
                    stop_managed_chrome(pid, log, "表格页面无响应，连接超时")
                    _ensure_wps_dedicated_chrome(
                        cdp_url, log, headless=True,
                        initial_url=initial_url or "https://www.kdocs.cn/",
                    )
            else:
                raise
            endpoint = resolve_cdp_endpoint(cdp_url)
            try:
                browser = playwright.chromium.connect_over_cdp(
                    endpoint, timeout=CDP_CONNECT_TIMEOUT_MS
                )
            except Exception as retry_error:
                raise RuntimeError(
                    "重新连接浏览器失败，请打开专用浏览器检查登录状态后重试。"
                    f"首次连接失败原因：{exc}"
                ) from retry_error
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        if (
            browser_mode == "cdp"
            and initial_url
            and ensure_background_cdp_target(browser, cdp_url, initial_url)
        ):
            # Playwright discovers background targets that existed when the CDP
            # connection was established. Reconnect once after creating it.
            browser = playwright.chromium.connect_over_cdp(
                endpoint, timeout=CDP_CONNECT_TIMEOUT_MS
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        return browser, context, False

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    log("正在启动临时浏览器，并载入已保存的登录信息…")
    try:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=for_login_check,
            channel=None if os.environ.get("TOOLBOX_PORTABLE") == "1" else "chrome",
            viewport={"width": 1400, "height": 900},
        )
    except Exception:
        log("未能启动系统浏览器，正在尝试使用内置浏览器。")
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=for_login_check,
            viewport={"width": 1400, "height": 900},
        )
    return None, context, True
