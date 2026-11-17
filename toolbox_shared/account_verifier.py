"""Non-GUI account verification state using the shared Qingneng verifier."""
from __future__ import annotations
import threading
import time
from .qingneng_login import LoginError, verify_credentials


class AccountVerifier:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.cancel_event = None
        self.thread = None
        self.generation = 0
        self.last_attempt = None
        self.state = dict(text="填写账号密码后自动保存", kind="neutral", running=False, retry=False)

    def cancel(self):
        with self.lock:
            self.generation += 1
            if self.cancel_event: self.cancel_event.set()
            self.state = dict(text="验证已停止，可重试", kind="neutral", running=False, retry=True)
        return self.status()

    def status(self):
        with self.lock: return dict(self.state)

    def start(self):
        with self.lock:
            saved = self.store.load()
            pair = saved.get("username", ""), saved.get("password", "")
            revision = saved.get("revision", "")
            if not all(pair): return self.status()
            if self.thread and self.thread.is_alive() and not self.cancel_event.is_set(): return self.status()
            if self.last_attempt and self.last_attempt[0] == revision and time.monotonic()-self.last_attempt[1] < 10:
                self.state.update(text="操作过于频繁，请间隔10秒后重试", retry=True)
                return self.status()
            self.generation += 1
            generation = self.generation
            cancel = self.cancel_event = threading.Event()
            self.last_attempt = revision, time.monotonic()
            self.state = dict(text="正在验证账号…", kind="pending", running=True, retry=False)
            def run():
                try:
                    verify_credentials(*pair, cancel_event=cancel, timeout_seconds=20)
                    result = dict(text="✓ 账号有效", kind="valid", running=False, retry=False)
                except LoginError as exc:
                    messages = {"credentials":"✗ 账号或密码错误", "network":"✗ 连接失败，请重试", "server":"✗ 登录服务暂不可用，请重试", "verification":"⚠ 需要人工验证", "timeout":"✗ 验证超时，请重试"}
                    result = dict(text=messages.get(exc.kind,"✗ 暂未确认账号有效，请重试"), kind="manual" if exc.kind=="verification" else "error", running=False, retry=exc.kind!="verification")
                except Exception:
                    result = dict(text="✗ 暂未确认账号有效，请重试", kind="error", running=False, retry=True)
                with self.lock:
                    if generation != self.generation or cancel.is_set(): return
                    try:
                        current_revision = self.store.load().get("revision", "")
                    except Exception:
                        self.state = dict(text="无法读取账号配置，请重新保存", kind="error", running=False, retry=True)
                        return
                    if current_revision == revision:
                        self.state = result
                    else:
                        self.state = dict(text="账号已变更，待验证", kind="neutral", running=False, retry=True)
            self.thread = threading.Thread(target=run, name="web-account-verify", daemon=True)
            self.thread.start()
            return self.status()
