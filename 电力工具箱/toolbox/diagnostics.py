from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .runtime import ToolPaths


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    checks: tuple[DiagnosticCheck, ...]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.checks)

    def summary(self) -> str:
        return "\n".join(f"{'✓' if item.ok else '✗'} {item.name}：{item.detail}" for item in self.checks)


def diagnose_environment(paths: ToolPaths) -> DiagnosticReport:
    checks = [DiagnosticCheck("Python", sys.version_info >= (3, 11), sys.version.split()[0])]
    tool_files: tuple[tuple[str, Path], ...] = (
        ("上网电量", paths.online_energy), ("交易分析", paths.trade_analysis),
        ("电量汇总", paths.summary), ("报告工具", paths.report_gui),
        ("私有上传", paths.private_uploader), ("集团上传", paths.group_upload),
        ("市场表更新", paths.market_table_update), ("WPS写入", paths.wps_writer),
        ("广东数据抓取", paths.guangdong_data_update),
        ("广东电价预测", paths.guangdong_price_forecast),
    )
    for name, path in tool_files:
        checks.append(DiagnosticCheck(name, path.is_file(), str(path) if path.is_file() else f"找不到：{path}"))
    node = shutil.which("node")
    checks.append(DiagnosticCheck("Node.js（私有上传）", node is not None, node or "找不到 node；其他工具不受影响"))
    return DiagnosticReport(tuple(checks))
