"""Read province market prices from the clearing workbook's brief analysis."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from unicodedata import normalize

from openpyxl import load_workbook


NUMBER = r"[-+]?\d+(?:\.\d+)?"
UP = "上涨|上升|增加|增长|升高|上行"
DOWN = "下降|下跌|减少|降低|下行|回落"
PRE_PRICE = rf"全省日前(?:出清)?均价\s*[:：]?\s*({NUMBER})\s*"
UNIT = r"(?:元\s*/\s*(?:兆瓦时|MWh)|厘\s*/\s*(?:千瓦时|kWh))"


def _brief_sheet(book, path: Path, *, day_ahead: bool, expected_date: str | None):
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
    return sheet


def read_brief_prices(path: Path, *, day_ahead: bool, expected_date: str | None = None,
                      a41_only: bool = False) -> dict:
    """Find the labelled paragraph in column A, including shifted A41/A42."""
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = _brief_sheet(book, path, day_ahead=day_ahead, expected_date=expected_date)
        prices = {}
        patterns = {
            "PRE": PRE_PRICE + UNIT,
            "RT": rf"(?:全省|全天)?实时(?:出清)?均价\s*({NUMBER})\s*{UNIT}",
        }
        for row in sheet.iter_rows(min_row=41 if a41_only else 1,
                                   max_row=41 if a41_only else 80, min_col=1, max_col=1):
            cell = row[0]
            if not isinstance(cell.value, str) or (not a41_only and "简析" not in cell.value):
                continue
            for kind, pattern in patterns.items():
                if day_ahead and kind != "PRE":
                    continue
                text = _normalize_brief(cell.value)
                match = re.search(pattern, text, re.I)
                if not match:
                    continue
                difference = _narrative_difference(text[match.end():], UNIT)
                prices[kind] = {
                    "price": float(match[1]), "difference": difference,
                    "workbook": str(path), "sheet": sheet.title, "cell": cell.coordinate,
                }
        if not prices:
            location = "A41" if a41_only else "A1:A80"
            raise ValueError(f"{path.name} {sheet.title} {location} 简析中缺少全省出清均价")
        return prices
    finally:
        book.close()


def apply_brief_prices(payload: dict, *, day_ahead_workbook: Path | None,
                       daily_clearing_workbook: Path | None,
                       previous_day_workbook: Path | None = None, log=print) -> list[dict]:
    """Use workbook prices for D PRE and D-2 RT, and compare D PRE with D-1 A41."""
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
        baseline = None
        if ahead and previous_day_workbook is not None:
            try:
                baseline = read_brief_prices(
                    previous_day_workbook, day_ahead=True, expected_date=board["compareDate"],
                    a41_only=True,
                )["PRE"]
                baseline["date"] = board["compareDate"]
            except (OSError, ValueError) as exc:
                log(f"前一日工作簿日前均价未取数，使用当日简析环比：{exc}")
        for row in board["rows"]:
            if row["dataType"] != ("PRE" if ahead else "RT"):
                continue
            source = prices.get(row["dataType"])
            if source is None:
                continue
            if baseline is not None:
                source = dict(source, difference=round(source["price"] - baseline["price"], 4),
                              compareSource=baseline)
            previous = dict(row)
            row["selectedDailyAvgPrice"] = source["price"]
            row["selectedPointCount"] = None  # Narrative is not a 96-point curve.
            # Do not mix an Excel selected price with a website comparison price.
            row["difference"] = source["difference"]
            row["compareDailyAvgPrice"] = (
                baseline["price"] if baseline is not None else
                round(source["price"] - source["difference"], 4) if source["difference"] is not None else None
            )
            row["comparePointCount"] = None
            row["priceSource"] = dict(source)
            sources.append(dict(source, date=row["selectedDate"], dataType=row["dataType"], website=previous))
            log(f"工作簿市场均价：{row['selectedDate']} {row['typeName']}={source['price']}，"
                f"环比={source['difference']}，来源={Path(path).name} {source['sheet']}!{source['cell']}")
        board["source"] = "工作簿简析（priceSource）及网站价格看板"
        board["note"] = "priceSource.compareSource 标识前一日工作簿均价，其他工作簿环比取当日简析，缺项留空。"
    return sources


def _normalize_brief(text: str) -> str:
    return normalize("NFKC", text).replace("−", "-")


def _narrative_difference(text: str, unit: str) -> float | None:
    # Only the immediately following clause belongs to the matched metric.
    prefix = r"[,：:\s(]*环比\s*[:：]?\s*"
    change = re.match(rf"{prefix}(?:小幅|略有|略)?\s*({UP}|{DOWN})\s*({NUMBER})\s*{unit}", text, re.I)
    if change:
        return abs(float(change[2])) * (-1 if change[1] in DOWN.split("|") else 1)
    flat = re.match(rf"{prefix}(?:基本持平|持平|无变化)(?=$|[\s,。.!;、)])", text)
    return 0.0 if flat else None


def read_day_ahead_market_deltas(path: Path, *, expected_date: str) -> dict:
    """Read only D-2 快报(日前)!A41, never a shifted paragraph or another day."""
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = _brief_sheet(book, path, day_ahead=True, expected_date=expected_date)
        text = sheet["A41"].value
        if not isinstance(text, str):
            raise ValueError(f"{path.name} {sheet.title}!A41 缺少日前市场文字")
        text = _normalize_brief(text)
        startup = re.search(r"市场开机台数\s*:?\s*\d+\s*台", text)
        price = re.search(PRE_PRICE + UNIT, text, re.I)
        return {
            "startupDifference": _narrative_difference(text[startup.end():], "台") if startup else None,
            "price": float(price[1]) if price else None,
            "priceDifference": _narrative_difference(text[price.end():], UNIT) if price else None,
            "workbook": str(path), "sheet": sheet.title, "cell": "A41", "date": expected_date,
        }
    finally:
        book.close()


def apply_day_ahead_market_deltas(payload: dict, *, workbook: Path | None, log=print) -> dict:
    """Use D-2 A41 for the day-ahead price and both comparisons."""
    market = payload["marketAnalysisData"]
    board = market["priceBoard"]
    source = {"startupDifference": None, "price": None, "priceDifference": None,
              "date": board["selectedDate"], "cell": "A41"}
    try:
        if workbook is None:
            raise ValueError("未找到日清分当天出清情况工作簿")
        source = read_day_ahead_market_deltas(Path(workbook), expected_date=board["selectedDate"])
    except (OSError, ValueError) as exc:
        source["error"] = str(exc)
        log(f"日前市场 A41 未取数，标记待补充：{exc}")
    startup = market.setdefault("operationPublicData", {}).setdefault("startupCount", {})
    startup["difference"] = source["startupDifference"]
    startup["differenceSource"] = dict(source)
    for row in board["rows"]:
        if row["dataType"] == "PRE":
            price = source["price"]
            difference = source["priceDifference"]
            row["selectedDailyAvgPrice"] = price
            row["selectedPointCount"] = None
            row["difference"] = difference
            row["priceSource"] = dict(source)
            row["differenceSource"] = dict(source)
            row["compareDailyAvgPrice"] = (
                round(price - difference, 4) if price is not None and difference is not None else None
            )
            row["comparePointCount"] = None
    if "note" in board:
        board["note"] += " 日清分日前均价及环比取快报（日前）A41。"
    market["dayAheadDeltaSource"] = source
    return source
