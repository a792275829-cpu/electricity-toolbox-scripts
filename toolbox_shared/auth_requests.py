"""Reauthenticate once for explicitly known read endpoints; never replay writes."""
from __future__ import annotations

from urllib.parse import urlsplit

READ_GET = frozenset({
    "/gdfire/api/data/unit/power/energy", "/gdfire/api/data/unit/power/fire",
    "/gdfire/api/data/unit/power/qing-yuan/data",
    "/gdfire/api/pf/tenant/user/tenant/grantApplication", "/gdfire/api/unit/list",
    "/gdfire/api/data/net/load", "/gdfire/api/data/net/capacity", "/gdfire/api/data/spot/trade/unit",
    "/gdfire/api/pub/data/report", "/gdfire/api/spot/market/info", "/gdfire/api/cockpit/extremePrice",
    "/gdfire/api/data/price/avg/node/price",
    "/usercenter/web/pf/tenant/user/application",
})
READ_POST = frozenset({"/gdfire/api/spot/clear/timeType/data/list", "/gdfire/api/data/personal/newEnergy/basic/query/v1"})


def session_expired(response):
    if response.status == 401:
        return True
    url = getattr(response, "url", "")
    if isinstance(url, str) and urlsplit(url).path.rstrip("/") in {"/usercenter", "/usercenter/login"}:
        return True
    try:
        payload = response.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    message = str(payload.get("retMsg") or "")
    return str(payload.get("retCode")) in {"401", "F401", "T401", "UNAUTHORIZED"} or (
        "登录" in message and any(word in message for word in ("失效", "过期", "未登录")))


class AuthenticatedContext:
    def __init__(self, context, auth):
        object.__setattr__(self, "_context", context)
        object.__setattr__(self, "_auth", auth)
        object.__setattr__(self, "_recovering", False)
        object.__setattr__(self, "request", AuthenticatedRequest(self))

    def __getattr__(self, name):
        return getattr(self._context, name)

    def __setattr__(self, name, value):
        setattr(self._context, name, value)

    def close(self, *args, **kwargs):
        import sys
        original = sys.exc_info()[1]
        try:
            return self._context.close(*args, **kwargs)
        except Exception:
            if original is None:
                raise
            self._auth.log("关闭登录浏览器失败，保留原始任务异常。")


class AuthenticatedRequest:
    def __init__(self, context):
        self.context = context

    def __getattr__(self, method):
        raw = getattr(self.context._context.request, method)
        if method not in {"get", "post"}:
            return raw

        def call(url, **kwargs):
            context = self.context
            auth = context._auth
            path = urlsplit(url).path
            response = raw(url, **kwargs)
            if path == "/usercenter/web/switchTenant" and response.status < 400:
                context._qingneng_tenant = (kwargs.get("params") or {}).get("tenantId")
            readonly = (method == "get" and (path in READ_GET or path.startswith("/gdfire/api/data/personal/newEnergy/productRun/"))) or (method == "post" and path in READ_POST)
            readonly = readonly and urlsplit(url).netloc == urlsplit(auth.BASE_URL).netloc
            if not readonly or context._recovering or not session_expired(response):
                return response
            from .credentials import credentials_path, read_json
            from .qingneng_login import LoginError
            saved = read_json(credentials_path())
            if saved.get("revision", "") != getattr(context, "_qingneng_revision", ""):
                raise LoginError("account_changed", "任务运行期间账号已修改，请重新启动任务。")
            pair = getattr(context, "_qingneng_credentials", None)
            if not pair or not all(pair):
                raise LoginError("credentials", "登录态已失效，请保存账号后重新运行任务。")
            tenant = getattr(context, "_qingneng_tenant", None)
            object.__setattr__(context, "_recovering", True)
            try:
                auth.log("只读请求登录态失效，正在重新登录（仅重试一次）...")
                if not auth.try_auto_login(context, *pair):
                    raise LoginError("session", "重新登录未成功，请使用登录窗口检查。")
                if tenant:
                    restored = context._context.request.get(auth.BASE_URL + "/usercenter/web/switchTenant", params={"tenantId": tenant}, timeout=20000)
                    if restored.status >= 400 or session_expired(restored):
                        raise LoginError("session", "重新登录后恢复公司失败，已停止读取。")
                    auth.parse_json_response(restored, "恢复当前公司")
                retried = raw(url, **kwargs)
                if session_expired(retried):
                    raise LoginError("session", "重新登录后会话仍然失效，已停止重试。")
                return retried
            finally:
                object.__setattr__(context, "_recovering", False)
        return call
