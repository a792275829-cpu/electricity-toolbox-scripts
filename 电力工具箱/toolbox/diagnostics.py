from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import os
from pathlib import Path

from .runtime import ToolPaths, node_executable


class DiagnosticLevel(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str
    category: str = "运行环境"
    recommendation: str = ""
    level: DiagnosticLevel | None = None

    @property
    def status(self) -> DiagnosticLevel:
        if self.level is not None:
            return self.level
        return DiagnosticLevel.OK if self.ok else DiagnosticLevel.ERROR


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    checks: tuple[DiagnosticCheck, ...]
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.checks)

    @property
    def errors(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(item for item in self.checks if item.status is DiagnosticLevel.ERROR)

    @property
    def warnings(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(item for item in self.checks if item.status is DiagnosticLevel.WARNING)

    @property
    def passed(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(item for item in self.checks if item.status is DiagnosticLevel.OK)

    def summary(self) -> str:
        icons = {
            DiagnosticLevel.OK: "✓",
            DiagnosticLevel.WARNING: "!",
            DiagnosticLevel.ERROR: "✗",
        }
        lines = [
            f"诊断时间：{self.generated_at:%Y-%m-%d %H:%M:%S}",
            f"结果：{len(self.passed)} 项正常，{len(self.warnings)} 项提醒，{len(self.errors)} 项异常",
            "",
        ]
        for item in self.checks:
            lines.append(f"{icons[item.status]} [{item.category}] {item.name}：{item.detail}")
            if item.recommendation and item.status is not DiagnosticLevel.OK:
                lines.append(f"  建议：{item.recommendation}")
        return "\n".join(lines)


def _path_access_check(name: str, path: Path, *, category: str = "文件与权限") -> DiagnosticCheck:
    if not path.exists():
        return DiagnosticCheck(
            name,
            False,
            f"找不到：{path}",
            category,
            "确认工具箱目录完整；不要单独移动或删除脚本文件。",
        )
    readable = os.access(path, os.R_OK)
    if not readable:
        return DiagnosticCheck(
            name,
            False,
            f"没有读取权限：{path}",
            category,
            "在 macOS“隐私与安全性”中允许启动工具箱的程序访问该目录。",
        )
    return DiagnosticCheck(name, True, str(path), category)


def diagnose_path(path: Path, *, require_write: bool = True) -> DiagnosticReport:
    """只读检查一个用户文件的存在性与当前进程访问权限。"""
    path = Path(path).expanduser().resolve()
    checks = [_path_access_check("文件存在且可读取", path)]
    if path.exists():
        target = path if path.is_dir() else path.parent
        writable = os.access(target, os.W_OK)
        if path.is_file():
            writable = writable and os.access(path, os.W_OK)
        checks.append(
            DiagnosticCheck(
                "保存目录可写",
                writable or not require_write,
                str(target) if writable else f"没有写入权限：{target}",
                "文件与权限",
                "关闭可能占用文件的 WPS/Excel，并检查目录访问权限。",
                DiagnosticLevel.OK if writable else (
                    DiagnosticLevel.ERROR if require_write else DiagnosticLevel.WARNING
                ),
            )
        )
        if path.is_file() and path.name.startswith("~$"):
            checks.append(
                DiagnosticCheck(
                    "临时锁文件",
                    True,
                    "这是 Office/WPS 生成的临时文件，不应作为输入文件。",
                    "文件与权限",
                    "请选择不以 ~$ 开头的原始工作簿。",
                    DiagnosticLevel.WARNING,
                )
            )
    return DiagnosticReport(tuple(checks))


def diagnose_environment(paths: ToolPaths) -> DiagnosticReport:
    checks = [
        DiagnosticCheck(
            "Python",
            sys.version_info >= (3, 11),
            f"{sys.version.split()[0]} · {sys.executable}",
            "运行环境",
            "请通过 setup_macos.command 初始化项目自带的 Python 环境。",
        )
    ]
    workspace = paths.workspace
    checks.append(_path_access_check("工具箱工作目录", workspace))
    if workspace.exists():
        writable = os.access(workspace, os.W_OK)
        checks.append(
            DiagnosticCheck(
                "工作目录写入权限",
                writable,
                str(workspace) if writable else f"没有写入权限：{workspace}",
                "文件与权限",
                "检查目录权限，或将完整工具箱移动到当前用户可写的目录。",
            )
        )
        try:
            free_bytes = shutil.disk_usage(workspace).free
        except OSError as exc:
            checks.append(
                DiagnosticCheck(
                    "可用磁盘空间",
                    True,
                    f"无法读取：{exc}",
                    "运行环境",
                    "如生成文件失败，请检查磁盘剩余空间。",
                    DiagnosticLevel.WARNING,
                )
            )
        else:
            free_gib = free_bytes / (1024 ** 3)
            low_space = free_gib < 1.0
            checks.append(
                DiagnosticCheck(
                    "可用磁盘空间",
                    True,
                    f"{free_gib:.1f} GB",
                    "运行环境",
                    "建议至少保留 1 GB 空间用于浏览器缓存和报表输出。",
                    DiagnosticLevel.WARNING if low_space else DiagnosticLevel.OK,
                )
            )
    tool_files: tuple[tuple[str, Path], ...] = (
        ("上网电量", paths.online_energy), ("交易分析", paths.trade_analysis),
        ("500kV断面分析", paths.section_analysis_runner),
        ("500kV拓扑名单", paths.section_analysis_topology),
        ("电量汇总", paths.summary), ("报告工具", paths.report_gui),
        ("私有上传", paths.private_uploader), ("集团上传", paths.group_upload),
        ("市场表更新", paths.market_table_update), ("WPS写入", paths.wps_writer),
        ("广东数据抓取", paths.guangdong_data_update),
        ("广东电价预测", paths.guangdong_price_forecast),
    )
    for name, path in tool_files:
        checks.append(_path_access_check(name, path, category="工具文件"))

    node = node_executable()
    node_path = Path(node)
    node_available = node != "node" or shutil.which("node") is not None
    if node_path.is_absolute():
        node_available = node_path.is_file() and os.access(node_path, os.X_OK)
    checks.append(
        DiagnosticCheck(
            "Node.js（仅私有上传需要）",
            True,
            node if node_available else "未找到；其他工具不受影响",
            "运行环境",
            "私有数据上传不可用时，运行 setup_macos.command 或配置 Node.js 路径。",
            DiagnosticLevel.OK if node_available else DiagnosticLevel.WARNING,
        )
    )
    return DiagnosticReport(tuple(checks))
