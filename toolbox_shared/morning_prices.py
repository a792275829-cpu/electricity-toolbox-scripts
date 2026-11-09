"""Read province market prices from the clearing workbook's brief analysis."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook


NUMBER = r"[-+]?\d+(?:\.\d+)?"
UNIT = r"(?:元\s*/\s*(?:兆瓦时|MWh)|厘\s*/\s*(?:千瓦时|kWh))"


def read_brief_prices(path: Path, *, day_ahead: bool, expected_date: str | None = None) -> dict:
    """Find the labelled paragraph in column A, including shifted A41/A42."""
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if expected_date is not None:
            value = book["基础信息"]["A2"].value if "基础信息" in book.sheetnames else None
            actual = value.strftime("%Y-%m-%d") if isinstance(value, (date, datetime)) else str(value or "").strip()[:10].replace("/", "-")
            if actual != expected_date:
                raise ValueError(f"{path.name} 基础信息!A2 运行日为 {actual or '空'}，应为 {expected_date}")
        sheet = next((s for s in book if
                      re.sub(r"\s+", "", s.title).replace("（", "(").replace("）", ")")
                      == ("快报(日前)" if day_ahead else "快报")), None)
        if sheet is None:
            raise ValueError(f"{path.name} 未找到{'快报（日前）' if day_ahead else '快报'}工作表")
        prices = {}
        patterns = {
            "PRE": rf"全省日前出清均价\s*({NUMBER})\s*{UNIT}",
            "RT": rf"(?:全省|全天)?实时(?:出清)?均价\s*({NUMBER})\s*{UNIT}",
        }
        for row in sheet.iter_rows(min_row=1, max_row=80, min_col=1, max_col=1):
            cell = row[0]
            if not isinstance(cell.value, str) or "简析" not in cell.value:
                continue
            for kind, pattern in patterns.items():
                if day_ahead and kind != "PRE":
                    continue
                match = re.search(pattern, cell.value, re.I)
                if not match:
                    continue
                tail = cell.value[match.end():]
                change = re.match(rf"[，,\s]*环比\s*(上涨|上升|增加|下降|下跌|减少)\s*({NUMBER})\s*{UNIT}", tail, re.I)
                difference = None
                if change:
                    difference = abs(float(change[2])) * (-1 if change[1] in {"下降", "下跌", "减少"} else 1)
                elif re.match(r"[，,\s]*环比\s*持平", tail):
                    difference = 0.0
                prices[kind] = {
                    "price": float(match[1]), "difference": difference,
                    "workbook": str(path), "sheet": sheet.title, "cell": cell.coordinate,
                }
        if not prices:
            raise ValueError(f"{path.name} {sheet.title} A1:A80 简析中缺少全省出清均价")
        return prices
    finally:
        book.close()


def apply_brief_prices(payload: dict, *, day_ahead_workbook: Path | None,
                       daily_clearing_workbook: Path | None, log=print) -> list[dict]:
    """Override only D PRE and D-2 RT, keeping workbook deltas intact."""
    sources = []
    for path, board, ahead in (
        (day_ahead_workbook, payload["priceBoard"], True),
        (daily_clearing_workbook, payload["marketAnalysisData"]["priceBoard"], False),
    ):
        if path is None:
            continue
        try:
            prices = read_brief_prices(Path(path), day_ahead=ahead, expected_date=board["selectedDate"])
        except (OSError, ValueError) as exc:
            log(f"工作簿市场均价未取数，保留网站价格：{exc}")
            continue
        for row in board["rows"]:
            if row["dataType"] != ("PRE" if ahead else "RT"):
                continue
            source = prices.get(row["dataType"])
            if source is None:
                continue
            previous = dict(row)
            row["selectedDailyAvgPrice"] = source["price"]
            row["selectedPointCount"] = None  # Narrative is not a 96-point curve.
            # Do not mix an Excel selected price with a website comparison price.
            row["difference"] = source["difference"]
            row["compareDailyAvgPrice"] = (round(source["price"] - source["difference"], 4)
                                            if source["difference"] is not None else None)
            row["comparePointCount"] = None
            row["priceSource"] = dict(source)
            sources.append(dict(source, date=row["selectedDate"], dataType=row["dataType"], website=previous))
            log(f"工作簿市场均价：{row['selectedDate']} {row['typeName']}={source['price']}，"
                f"环比={source['difference']}，来源={Path(path).name} {source['sheet']}!{source['cell']}")
        board["source"] = "工作簿简析（priceSource）及网站价格看板"
        board["note"] = "priceSource 标识的均价及环比取工作簿简析，比较均价由均价减环比还原；简析未给环比时留空。其余保留网站值。"
    return sources
