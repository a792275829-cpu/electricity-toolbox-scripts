"""Python transport and form labels for the Rust market-data kernel."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from toolbox_native_excel import call, native_binary


@dataclass(frozen=True)
class MarketField:
    key: str
    name: str
    unit: str


MARKET_FIELDS = (
    MarketField("load", "统调负荷峰值", "万千瓦"),
    MarketField("aclass", "省内A类电源最大出力", "万千瓦"),
    MarketField("local", "地方电源最大出力", "万千瓦"),
    MarketField("west", "西电东送电力", "万千瓦"),
    MarketField("bclass", "B类竞价空间最大值", "万千瓦"),
    MarketField("maintenance", "检修容量", "万千瓦"),
    MarketField("coal", "煤机开机台数", "台"),
    MarketField("gas", "气机开机台数", "台"),
    MarketField("price", "全省市场均价", "元/兆瓦时"),
)


def _transport(value):
    # JSON cannot transport website NaN/Infinity; Rust treats them as missing.
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _transport(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_transport(item) for item in value]
    return value


def _execute(**payload):
    binary = native_binary()
    if binary is None:
        raise RuntimeError("未安装 Rust 市场数据后端，请运行 setup_macos.command。")
    try:
        result = call(binary, "market_manual", "", payload=payload)
    except subprocess.CalledProcessError as exc:
        if "unknown operation" in exc.stderr:
            raise RuntimeError("Rust 市场数据后端版本较旧，请运行 setup_macos.command 重新构建。") from exc
        raise ValueError(exc.stderr.strip() or "Rust 市场数据处理失败。") from exc
    if "error" in result:
        raise ValueError(result["error"])
    return result


def normalize_manual_market(data, report_date):
    if data is None:
        return {}
    return _execute(action="normalize", data=data, report_date=report_date.isoformat())["data"]


def preview_manual_market(data, report_date):
    return _execute(action="normalize", data=data, report_date=report_date.isoformat())


def merge_manual_market(cards, period, data, report_date):
    current = report_date if period == "day_ahead" else report_date - timedelta(days=2)
    baseline = report_date - timedelta(days=1) if period == "day_ahead" else current
    return _execute(
        action="merge", cards=_transport(cards), period=period, data=data,
        report_date=report_date.isoformat(), current_date=current.isoformat(),
        comparison_date=baseline.isoformat(),
    )
