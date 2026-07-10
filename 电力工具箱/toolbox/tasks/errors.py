from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import TaskCancelled
from .process import ProcessExecutionError


class ErrorCategory(str, Enum):
    INPUT = "input"
    DEPENDENCY = "dependency"
    AUTHENTICATION = "authentication"
    NETWORK = "network"
    PERMISSION = "permission"
    EXTERNAL_PROCESS = "external_process"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UserFacingError:
    category: ErrorCategory
    summary: str
    advice: str
    cause: BaseException


def classify_error(exc: BaseException) -> UserFacingError:
    text = str(exc).lower()
    if isinstance(exc, TaskCancelled):
        return UserFacingError(ErrorCategory.CANCELLED, "任务已取消", "可以调整参数后重新运行。", exc)
    if isinstance(exc, FileNotFoundError):
        return UserFacingError(ErrorCategory.INPUT, "找不到输入文件或程序文件", "请重新选择文件，并确认项目目录完整。", exc)
    if isinstance(exc, PermissionError):
        return UserFacingError(ErrorCategory.PERMISSION, "文件无权访问或正被占用", "请关闭占用该文件的 Excel/WPS 后重试。", exc)
    if isinstance(exc, ProcessExecutionError):
        return UserFacingError(ErrorCategory.EXTERNAL_PROCESS, str(exc), "请查看任务日志中的最后一段输出。", exc)
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return UserFacingError(ErrorCategory.DEPENDENCY, "缺少运行依赖", "请重新运行 setup_macos.command。", exc)
    if any(marker in text for marker in ("登录", "login", "auth", "认证")):
        return UserFacingError(ErrorCategory.AUTHENTICATION, "登录状态不可用", "请先执行登录或刷新登录状态，再手动重试。", exc)
    if any(marker in text for marker in ("timeout", "timed out", "network", "socket", "连接")):
        return UserFacingError(ErrorCategory.NETWORK, "网络请求失败", "请检查网络和代理后重试。上传任务需重新确认。", exc)
    return UserFacingError(ErrorCategory.UNKNOWN, str(exc) or exc.__class__.__name__, "请查看完整日志和 traceback。", exc)
