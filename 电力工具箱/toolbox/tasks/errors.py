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


def classify_error(exc: BaseException, output: str = "") -> UserFacingError:
    import re
    from toolbox_log_console import explain_error, redact

    if isinstance(exc, TaskCancelled):
        return UserFacingError(ErrorCategory.CANCELLED, "任务已取消", "可以调整参数后重新运行。", exc)
    if isinstance(exc, FileNotFoundError):
        return UserFacingError(ErrorCategory.INPUT, "找不到输入文件或程序文件", "请重新选择文件，并确认项目目录完整。", exc)
    if isinstance(exc, PermissionError):
        return UserFacingError(ErrorCategory.PERMISSION, "文件无权访问或正被占用", "请关闭占用该文件的 Excel/WPS，并检查目录访问权限后重试。", exc)
    diagnosis = None
    # Remote task errors may contain the complete multiline subprocess output.
    # Diagnose its final concrete cause, just as for separately captured logs.
    for line in reversed((exc.__class__.__name__ + ": " + str(exc)).splitlines()):
        diagnosis = explain_error(line)
        if diagnosis:
            break
    if diagnosis is None:
        # Prefer the final concrete exception over earlier recovered failures.
        for line in reversed(output.splitlines()):
            diagnosis = explain_error(line)
            if diagnosis:
                break
    if diagnosis:
        summary, advice = diagnosis
        category = ErrorCategory.UNKNOWN
        for word, candidate in (("依赖", ErrorCategory.DEPENDENCY), ("登录", ErrorCategory.AUTHENTICATION),
                                ("超时", ErrorCategory.NETWORK), ("网络", ErrorCategory.NETWORK),
                                ("代理", ErrorCategory.NETWORK),
                                ("权限", ErrorCategory.PERMISSION), ("文件", ErrorCategory.INPUT)):
            if word in summary:
                category = candidate
                break
        return UserFacingError(category, summary, advice, exc)
    if isinstance(exc, ProcessExecutionError):
        for line in reversed(output.splitlines()):
            if re.search(r"(?:[A-Za-z_]+(?:Error|Exception)|失败|错误)[:：]\s*\S", line):
                return UserFacingError(ErrorCategory.EXTERNAL_PROCESS, redact(line.strip())[:300],
                                       "请根据上述原始错误检查参数或输入文件；点击“复制错误”可提供完整排查信息。", exc)
        return UserFacingError(ErrorCategory.EXTERNAL_PROCESS, "任务执行失败，暂未识别具体原因", "点击“复制错误”获取完整日志用于排查；上传或写入任务请先核对是否已有部分成功。", exc)
    return UserFacingError(ErrorCategory.UNKNOWN, str(exc) or exc.__class__.__name__, "请点击“复制错误”，提供完整日志以便排查。", exc)
