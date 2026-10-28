"""Qingneng login policy shared by the isolated tool browser sessions."""

from __future__ import annotations

import base64
import os
import time

BASE_URL = "https://xhxt.chng.com.cn"


class LoginError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def rsa_encrypt_password(public_key: str, password: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_der_public_key(base64.b64decode(public_key))
    return base64.b64encode(key.encrypt(password.encode("utf-8"), padding.PKCS1v15())).decode("ascii")


def check_session(request, *, base_url: str = BASE_URL) -> bool:
    """Probe the lightweight user endpoint first, retaining the legacy fallback."""
    from playwright.sync_api import Error as PlaywrightError

    unavailable = 0
    for path in ("/usercenter/web/pf/tenant/user/info", "/gdfire/api/pf/tenant/user/info"):
        try:
            response = request.get(base_url + path, timeout=8000)
        except PlaywrightError:
            unavailable += 1
            continue
        if response.status >= 500:
            unavailable += 1
            continue
        if response.status != 200:
            continue
        try:
            payload = response.json()
        except (ValueError, PlaywrightError):
            continue
        if isinstance(payload, dict) and payload.get("retCode") == "T200":
            data = payload.get("data")
            if isinstance(data, dict) and data.get("tenantId"):
                return True
    if unavailable == 2:
        raise LoginError("network", "登录服务暂不可达，请检查内网、VPN 或代理后重试。")
    return False


def _login_payload(response):
    from playwright.sync_api import Error as PlaywrightError

    # Do not echo response bodies or Playwright call logs: they may contain credentials.
    if response.status >= 500 or response.status == 429:
        raise LoginError("server", f"登录服务暂不可用（HTTP {response.status}），请稍后重试。")
    try:
        payload = response.json()
    except (ValueError, PlaywrightError):
        raise LoginError("protocol", "登录服务返回格式异常，请稍后重试。") from None
    if not isinstance(payload, dict):
        raise LoginError("protocol", "登录服务返回格式异常，请稍后重试。")
    message = str(payload.get("retMsg") or "")
    if response.status >= 400 or payload.get("retCode") != "T200":
        if any(word in message.lower() for word in ("验证码", "验证", "captcha", "滑块", "短信")):
            raise LoginError("verification", "网站需要人工验证，请使用登录窗口完成验证后重试。")
        if any(word in message.lower() for word in ("密码", "账号", "账户", "用户", "password", "credential", "锁定")):
            raise LoginError("credentials", "账号或密码被拒绝，请检查账号、密码及账户是否锁定。")
        raise LoginError("rejected", "登录请求被网站拒绝，请检查账号状态或稍后重试。")
    return payload


def submit_api_login(request, username: str, password: str, *, base_url: str = BASE_URL,
                     encrypt=rsa_encrypt_password) -> None:
    """Only the idempotent public-key GET is retried; never repeat a rejected POST."""
    from playwright.sync_api import Error as PlaywrightError

    for attempt in range(2):
        try:
            response = request.get(base_url + "/usercenter/web/pf/login/info/publicKey", timeout=15000)
            payload = _login_payload(response)
            break
        except PlaywrightError:
            if attempt:
                raise LoginError("network", "获取登录公钥失败，请检查内网、VPN 或代理。") from None
        except LoginError as exc:
            if exc.kind != "server" or attempt:
                raise
        time.sleep(0.5)
    public_key = payload.get("data")
    if not isinstance(public_key, str) or not public_key:
        raise LoginError("protocol", "登录服务未返回有效公钥。")
    try:
        encrypted = encrypt(public_key, password)
    except (ValueError, TypeError):
        raise LoginError("protocol", "登录服务返回的公钥无效。") from None
    try:
        response = request.post(base_url + "/usercenter/web/login",
                                params={"loginMode": 2, "username": username, "password": encrypted},
                                timeout=20000)
    except PlaywrightError:
        raise LoginError("network", "提交登录请求失败，请检查网络后重试。") from None
    _login_payload(response)


def api_login(auth, context, username: str, password: str, *, app_url: str) -> bool:
    submit_api_login(context.request, username, password, base_url=auth.BASE_URL,
                     encrypt=auth.rsa_encrypt_password)
    if auth.is_logged_in(context):
        auth.save_auth_state(context)
        return True
    # Some deployments only initialize the application session on navigation.
    page = context.new_page()
    try:
        page.goto(app_url, wait_until="domcontentloaded", timeout=20000)
        logged_in = auth.is_logged_in(context)
        if logged_in:
            auth.save_auth_state(context)
        return logged_in
    finally:
        page.close()


def ui_login(auth, context, username: str, password: str) -> bool:
    from playwright.sync_api import Error as PlaywrightError

    page = context.new_page()
    try:
        page.goto(auth.LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
        try:
            page.locator("#username, input[placeholder*='工号'], input[placeholder*='手机号'], input[placeholder*='账号']").first.fill(username, timeout=5000)
            page.locator("#password, input[type='password']").first.fill(password, timeout=5000)
            page.locator("button:has-text('登 录'), button:has-text('登录'), button[type='submit']").first.click(timeout=5000)
        except PlaywrightError:
            pass
        auth.log("请在登录窗口中完成人工验证（最长等待45秒）。")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if auth.is_logged_in(context):
                auth.save_auth_state(context)
                return True
            time.sleep(1)
        return False
    finally:
        page.close()


def auto_login(auth, context, username: str, password: str) -> bool:
    from playwright.sync_api import Error as PlaywrightError

    context._qingneng_login_credentials = (username, password)
    auth.log("正在尝试接口登录...")
    try:
        if auth.try_api_login(context, username, password):
            return True
    except LoginError as exc:
        if exc.kind != "verification":
            raise
        if getattr(context, "_qingneng_headless", True) is not False:
            raise
        auth.log(str(exc))
        try:
            if auth.try_ui_login(context, username, password):
                return True
        except (PlaywrightError, LoginError):
            pass
        raise exc from None
    except PlaywrightError:
        raise LoginError("network", "登录页面连接失败，请检查网络后重试。") from None
    raise LoginError("session", "登录接口已返回，但未确认有效登录态，请使用登录窗口检查账号权限。")


def verify_credentials(username: str, password: str, *, base_url: str = BASE_URL) -> None:
    """Verify a draft in an ephemeral request context, without modifying any tool state."""
    from playwright.sync_api import sync_playwright

    if not username.strip() or not password:
        raise LoginError("credentials", "账号和密码需填写完整。")
    with sync_playwright() as playwright:
        proxy = next((os.environ[name] for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                      "https_proxy", "http_proxy", "all_proxy") if os.environ.get(name)), None)
        request = playwright.request.new_context(**({"proxy": {"server": proxy}} if proxy else {}))
        try:
            submit_api_login(request, username.strip(), password, base_url=base_url)
            if not check_session(request, base_url=base_url):
                raise LoginError("session", "登录接口已返回，但未确认账号权限，请使用登录窗口检查。")
        finally:
            request.dispose()
