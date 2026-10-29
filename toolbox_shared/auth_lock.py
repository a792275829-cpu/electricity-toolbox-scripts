"""macOS advisory file lock with identity checks before use and removal."""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


class AuthFileLock:
    def __init__(self, path: Path, *, timeout_seconds=900, log=print, script="toolbox"):
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self.log = log
        self.script = script
        self.acquired = False
        self._descriptor = None

    def _owns_path(self, descriptor):
        try:
            current = self.path.stat()
        except FileNotFoundError:
            return False
        held = os.fstat(descriptor)
        return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)

    def _legacy_owner_running(self, descriptor):
        # Old releases used O_EXCL without flock. During an upgrade, continue
        # honoring a live legacy owner rather than taking over its profile.
        try:
            owner = json.loads(os.pread(descriptor, 65536, 0))
            if not isinstance(owner, dict) or owner.get("lockProtocol") == 1:
                return False
            pid = owner.get("pid")
            if type(pid) is not int or pid <= 0:
                return False
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (ValueError, ProcessLookupError):
            return False

    def __enter__(self):
        if self._descriptor is not None:
            raise RuntimeError("登录态锁不能重复进入")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        notified = False
        while True:
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # A waiting descriptor may refer to an inode removed by the last
                # owner. It must never become a second lock on an unlinked file.
                if not self._owns_path(descriptor):
                    os.close(descriptor)
                    continue
                if self._legacy_owner_running(descriptor):
                    raise BlockingIOError("legacy owner is still running")
            except BlockingIOError:
                os.close(descriptor)
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"等待登录态锁超时，请确认没有其他上传/抓取脚本仍在运行：{self.path}") from None
                if not notified:
                    self.log(f"检测到其他脚本正在使用同一登录态，等待其完成：{self.path}")
                    notified = True
                time.sleep(min(.1, max(0, deadline - time.monotonic())))
                continue
            except BaseException:
                os.close(descriptor)
                raise
            self._descriptor = descriptor
            try:
                os.fchmod(descriptor, 0o600)
                os.ftruncate(descriptor, 0)
                with os.fdopen(os.dup(descriptor), "w", encoding="utf-8") as stream:
                    json.dump(dict(lockProtocol=1, pid=os.getpid(), startedAt=datetime.now().isoformat(),
                                   script=self.script), stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.acquired = True
                self.log(f"已获得登录态锁：{self.path}")
                return self
            except BaseException:
                self.__exit__(*sys.exc_info())
                raise

    def __exit__(self, exc_type, exc, tb):
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            if self._owns_path(descriptor):
                self.path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            if exc_type is None:
                raise
            self.log(f"清理登录态锁失败，将保留原始错误：{cleanup_error}")
        finally:
            self.acquired = False
            os.close(descriptor)
