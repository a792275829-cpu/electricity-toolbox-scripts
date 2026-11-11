"""Read node-price exports for the clearing summary, without moving sources."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
import re

NODE_MARKETS = {"dayahead": "日前", "realtime": "实时"}
QUARTER_SLOTS = tuple(f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 15, 30, 45))


def is_node_price_file(path: Path) -> bool:
    return (path.suffix.lower() == ".xlsx" and not path.name.startswith("~$")
            and "节点电价查询" in path.stem)


@dataclass(frozen=True)
class NodePrices:
    market: str
    run_date: date
    nodes: tuple[str, ...]
    quarter_averages: tuple[float, ...]
    province_prices: tuple[float, ...] | None = None


def _sheet_date(title: str) -> date:
    match = re.search(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})", title)
    if not match:
        raise ValueError(f"节点电价工作表缺少日期：{title}")
    return date(*(int(part) for part in match.groups()))


def read_node_prices(path: Path) -> NodePrices:
    """Average selected nodes and read the labeled province row separately."""
    from .native_excel import load_value_workbook

    markets = [key for key, label in NODE_MARKETS.items() if label in path.stem]
    if len(markets) != 1:
        raise ValueError(f"{path.name}：节点电价文件须明确日前或实时类型")
    market = markets[0]
    workbook = load_value_workbook(path, read_only=True, data_only=True)
    nodes = set()
    dates = set()
    prices = []
    province_prices = None
    try:
        for sheet in workbook.worksheets:
            sheet.reset_dimensions()
            rows = sheet.iter_rows(values_only=True)
            headers = [str(value or "").strip() for value in next(rows, ())]
            if "节点名称" not in headers or "数据项" not in headers:
                continue
            node_col, item_col = headers.index("节点名称"), headers.index("数据项")
            columns = []
            for slot in QUARTER_SLOTS:
                if headers.count(slot) != 1:
                    raise ValueError(f"{path.name} / {sheet.title}：十五分钟时段缺失或重复 {slot}")
                columns.append(headers.index(slot))
            sheet_date = _sheet_date(sheet.title)
            sheet_markets = [key for key, label in NODE_MARKETS.items() if label in sheet.title]
            if sheet_markets != [market]:
                raise ValueError(f"{path.name} / {sheet.title}：日前/实时类型不一致")
            dates.add(sheet_date)
            for row in rows:
                name = str(row[node_col] or "").strip() if len(row) > node_col else ""
                is_province = name == "全省" and "全省" in sheet.title
                if not is_province and not any(word in name for word in ("海风", "升压站")):
                    continue
                if is_province and province_prices is not None:
                    raise ValueError(f"{path.name} / {sheet.title}：全省电价来源重复")
                if not is_province and name in nodes:
                    raise ValueError(f"{path.name} / {sheet.title}：节点重复 {name}")
                if len(row) <= item_col or str(row[item_col]).strip() != "电价(元/MWh)":
                    raise ValueError(f"{path.name} / {name}：数据项须为电价(元/MWh)")
                values = []
                for slot, col in zip(QUARTER_SLOTS, columns):
                    try:
                        raw = row[col]
                        if isinstance(raw, bool) or raw is None or (isinstance(raw, str) and not raw.strip()):
                            raise ValueError("空值或布尔值")
                        value = float(raw)
                        if not math.isfinite(value):
                            raise ValueError("非有限数值")
                    except (IndexError, ValueError, TypeError) as exc:
                        raise ValueError(f"{path.name} / {name} / {slot}：电价缺失或无效") from exc
                    values.append(value)
                if is_province:
                    province_prices = tuple(values)
                else:
                    nodes.add(name)
                    prices.append(values)
    finally:
        workbook.close()
    if not nodes:
        raise ValueError(f"{path.name}：未找到名称含海风或升压站的节点")
    if len(dates) != 1:
        raise ValueError(f"{path.name}：节点电价工作表日期不唯一")
    return NodePrices(market, dates.pop(), tuple(sorted(nodes)),
                      tuple(math.fsum(row[i] for row in prices) / len(prices) for i in range(96)),
                      province_prices)


def collect_node_prices(folder: Path, logger) -> dict[str, NodePrices]:
    """The review-folder root holds node exports, outside upload station folders."""
    result = {}
    sources = {}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or not is_node_price_file(path):
            continue
        data = read_node_prices(path)
        if data.market in result:
            raise ValueError(f"节点电价{NODE_MARKETS[data.market]}来源冲突：{sources[data.market]} 与 {path}；请仅保留一份来源")
        result[data.market] = data
        sources[data.market] = path
        logger(f"节点电价：{path.name}，表内日期 {data.run_date}，筛选 {len(data.nodes)} 个海风/升压站节点")
        if data.province_prices is None:
            logger(f"提示：{path.name} 缺少全省电价明细，全省汇总对应96点留空。")
    return result
