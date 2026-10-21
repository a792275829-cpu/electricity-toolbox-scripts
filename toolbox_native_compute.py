"""Scoped native numeric kernels; binary float transport preserves precision."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps, lru_cache
import base64
import json
import os
import subprocess
import warnings

from toolbox_native_excel import native_binary, call

_session = ContextVar("toolbox_compute_session", default=None)


def compute_mode():
    mode = os.environ.get("TOOLBOX_COMPUTE_BACKEND", "auto")
    if mode not in {"auto", "python", "rust"}:
        raise ValueError(f"未知计算后端: {mode}")
    return mode


@lru_cache(maxsize=8)
def _supports_stream(binary, modified, size):
    response = call(binary, "capabilities", "")
    return response.get("stream") is True and "unmix" in response.get("operations", ())


@contextmanager
def native_session():
    if _session.get() is not None or compute_mode() == "python":
        yield
        return
    binary = native_binary()
    if binary is None:
        if compute_mode() == "rust":
            raise RuntimeError("未安装 Rust 计算后端")
        yield
        return
    try:
        state = binary.stat()
        if not _supports_stream(str(binary), state.st_mtime_ns, state.st_size):
            raise RuntimeError("Rust 后端不支持流式计算，请重新构建")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if compute_mode() == "rust":
            raise
        warnings.warn(f"Rust 计算后端版本不兼容，使用 Python: {exc}", RuntimeWarning)
        token = _session.set(False)
        try:
            yield
        finally:
            _session.reset(token)
        return
    try:
        process = subprocess.Popen([str(binary), "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    except OSError as exc:
        if compute_mode() == "rust":
            raise
        warnings.warn(f"Rust 计算进程不可用，使用 Python: {exc}", RuntimeWarning)
        yield
        return
    token = _session.set(process)
    try:
        yield
    finally:
        _session.reset(token)
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def with_native_session(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with native_session():
            return function(*args, **kwargs)
    return wrapped


def unmix(pixels, background, palette, *, profile=False):
    import numpy as np
    mode = compute_mode()
    if _session.get() is False:
        return None
    if mode == "python" or (mode == "auto" and pixels.shape[0] * pixels.shape[1] < 2048):
        return None
    binary = native_binary()
    if binary is None:
        if mode == "rust":
            raise RuntimeError("未安装 Rust 计算后端")
        return None
    encode = lambda a: base64.b64encode(np.asarray(a, dtype="<f8").tobytes(order="C")).decode("ascii")
    uint8 = pixels.dtype == np.uint8
    encoded_pixels = base64.b64encode(pixels.tobytes(order="C")).decode("ascii") if uint8 else encode(pixels)
    payload = dict(height=pixels.shape[0], width=pixels.shape[1], pixels=encoded_pixels,
                   uint8=uint8, profile=profile, background=encode(background),
                   palette=[None if c is None else encode(c) for c in palette])
    try:
        process = _session.get()
        if process is None:
            response = call(binary, "unmix", "", payload=payload)
        else:
            process.stdin.write(json.dumps(dict(protocol=1, operation="unmix", path="", payload=payload)) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("Rust 计算进程意外退出")
            response = json.loads(line)
        if response.get("protocol") != 1 or "error" in response:
            raise ValueError(response.get("error", "Rust 计算协议错误"))
        result = np.frombuffer(base64.b64decode(response["coverage"], validate=True), dtype="<f8")
        return result.reshape((pixels.shape[0], 1 if profile else pixels.shape[1], 3)).copy()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        if mode == "rust":
            raise
        warnings.warn(f"Rust 图表计算失败，使用 Python: {exc}", RuntimeWarning)
        return None
