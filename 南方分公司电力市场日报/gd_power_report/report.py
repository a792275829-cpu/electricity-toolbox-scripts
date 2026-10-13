from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date
from typing import Any



WEEKDAYS = "一二三四五六日"
HIDDEN_SUMMARY_CARD_LABELS = {"节点电价区间"}
REALTIME_SUMMARY_CARD_ORDER = [
    "实时出清电量",
    "实时加权均价",
    "电能电价",
    "含容量综合电价",
    "实时较日前偏差电量",
    "实时偏差收益",
    "实际上网偏差电量",
    "实际上网偏差收益",
]
REALTIME_VALUE_ONLY_CARD_LABELS = {
    "实时出清电量",
    "实时较日前偏差电量",
    "实时偏差收益",
    "实际上网偏差电量",
    "实际上网偏差收益",
}
DAY_SUMMARY_DISPLAY_LABELS = {
    "含中长期电能电价": "日前电能电价",
    "含中长期电能量电价": "日前电能电价",
    "含容量综合电价": "日前综合电价",
    "较中长期偏差": "日前较中长期偏差",
}
REALTIME_SUMMARY_DISPLAY_LABELS = {
    "电能电价": "全天电能电价",
    "含容量综合电价": "全天综合电价",
    "实时较日前偏差电量": "实时偏差电量",
    "实际较实时偏差电量": "实际上网偏差电量",
    "上网偏差收益": "实际上网偏差收益",
}
APPENDIX_HEADER_LABELS = {
    "合计收益": "整体盈亏",
    "单位变动成本": "变动成本",
    "日前偏差\n（较中长期）": "日前偏差",
    "现货偏差边际贡献\n（万元）": "现货贡献",
    "现货边际贡献\n（万元）": "现货贡献",
    "差价\n收益": "差价收益",
    "边际\n贡献": "边际贡献",
    "容量\n电费": "容量电费",
    "固定\n成本": "固定成本",
    "综合\n电价": "综合电价",
    "实时偏差\n（较日前）": "实时偏差",
    "实际上网偏差\n（较实时）": "上网偏差",
    "电能量均价\n（不含容量/机制）": "电能均价",
}
DAY_FIELD_NOTES = [
    "变动成本：单位变动成本。",
    "日前偏差：较中长期偏差。",
    "现货贡献：现货偏差边际贡献。",
]
REALTIME_FIELD_NOTES = [
    "实时偏差：较日前偏差。",
    "上网偏差：较实时偏差。",
    "电能均价：不含容量/机制的电能量均价。",
]
FOLLOWUP_POINTS = [
    "持续跟踪实时负荷峰谷与日前预测偏差。",
    "持续跟踪分公司实时出清电量与日前计划偏差。",
    "持续跟踪实时价格及收益变化。",
]
def _date_cn(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def _format_number(value: Any, decimals: int | None = None) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals is not None:
        if decimals == 0:
            rounded = math.floor(abs(number) + 0.5)
            if number < 0:
                rounded = -rounded
            return f"{rounded:,}"
        return f"{number:,.{decimals}f}"
    if math.isclose(number, round(number), abs_tol=1e-9):
        return f"{int(round(number)):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _prepare_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = deepcopy(cards)
    for card in prepared:
        decimals = card.get("decimals")
        card["value_text"] = (
            "—"
            if card.get("pending")
            else _format_number(card.get("value"), decimals=decimals)
        )
        comparison_note = str(card.get("comparison_note") or "").strip()
        delta = card.get("delta")
        if comparison_note:
            card["delta_text"] = comparison_note
            card["delta_class"] = "flat"
        elif delta is not None:
            card["delta_text"] = (
                f"{'▲' if delta > 0 else '▼' if delta < 0 else ''} "
                f"{_format_number(abs(delta), decimals=card.get('delta_decimals', decimals))}"
            ).strip()
            card["delta_class"] = "up" if delta > 0 else "down" if delta < 0 else "flat"
    return prepared


def _label_key(card: dict[str, Any]) -> str:
    return "".join(str(card.get("label", "")).split())


def _is_price_card(card: dict[str, Any]) -> bool:
    unit = "".join(str(card.get("unit") or "").split())
    return "元/" in unit and ("瓦时" in unit or "MWh" in unit)


def _move_label_to_end(cards: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    target = "".join(label.split())
    return [card for card in cards if _label_key(card) != target] + [
        card for card in cards if _label_key(card) == target
    ]


def _prepare_market_cards(
    cards: list[dict[str, Any]],
    *,
    realtime: bool = False,
) -> list[dict[str, Any]]:
    visible = deepcopy(cards)
    for card in visible:
        if _label_key(card) in {"市场开机", "在线机组数"}:
            breakdown = card.get("thermal_breakdown") or {}
            rows = _prepare_cards([
                {"label": label, "value": breakdown.get(fuel), "delta": breakdown.get(f"{fuel}_delta"),
                 "decimals": 0, "delta_decimals": 0, "delta_prefix": "较日前" if realtime else "环比"}
                for fuel, label in (("coal", "煤机："), ("gas", "燃机："))
            ])
            for row in rows:
                row.setdefault("delta_text", "—")
                row.setdefault("delta_class", "flat")
            card["thermal_breakdown_rows"] = rows
        if not _is_price_card(card):
            card["decimals"] = 0
            card["delta_decimals"] = 0
    prepared = _prepare_cards(visible)
    if realtime:
        for card in prepared:
            if (
                card.get("delta_text")
                and not card.get("comparison_note")
                and card.get("delta_prefix") is None
            ):
                card["delta_prefix"] = "较日前"
        display_order = [
            "统调实际负荷峰值",
            "省内A类电源",
            "地方电源出力",
            "西电东送电力",
            "B类竞价空间",
            "检修容量",
            "在线机组数",
            "全省实时均价",
        ]
    else:
        display_order = [
            "统调负荷峰值",
            "省内A类电源",
            "地方电源出力",
            "西电东送电力",
            "B类竞价空间",
            "检修容量",
            "市场开机",
            "全省日前均价",
        ]
    ordered = [
        card
        for label in display_order
        for card in prepared
        if _label_key(card) == "".join(label.split())
    ]
    display_labels = (
        {
            "省内A类电源": "省内A类电源最大出力",
            "地方电源出力": "地方电源最大出力",
            "西电东送电力": "西电东送实际值",
            "B类竞价空间": "B类竞价空间最大值",
            "在线机组数": "火电机组开机台数",
        }
        if realtime
        else {
            "省内A类电源": "省内A类电源最大出力",
            "地方电源出力": "地方电源最大出力",
            "西电东送电力": "西电东送最大出力",
            "B类竞价空间": "B类最大竞价空间",
            "市场开机": "火电机组开机台数",
        }
    )
    for card in ordered:
        card["label"] = display_labels.get(str(card.get("label") or ""), card.get("label"))
    return ordered


def _prepare_summary_cards(cards: list[dict[str, Any]], *, realtime: bool = False) -> list[dict[str, Any]]:
    visible = [deepcopy(card) for card in cards if _label_key(card) not in HIDDEN_SUMMARY_CARD_LABELS]
    for card in visible:
        decimals = 2 if _is_price_card(card) else 0
        card["decimals"] = decimals
        card["delta_decimals"] = decimals
    if realtime:
        explicit_order_keys = {"".join(label.split()) for label in REALTIME_SUMMARY_CARD_ORDER}
        value_only_keys = {
            "".join(label.split()) for label in REALTIME_VALUE_ONLY_CARD_LABELS
        }
        if explicit_order_keys.issubset({_label_key(card) for card in visible}):
            for card in visible:
                label = _label_key(card)
                if label in value_only_keys:
                    card.pop("delta", None)
                if label in {"实时加权均价", "电能电价", "含容量综合电价"} and card.get("delta") is not None:
                    card["delta_prefix"] = "较日前"
                else:
                    card.pop("delta_prefix", None)
            prepared = _prepare_cards(visible)
            ordered = [
                card
                for label in REALTIME_SUMMARY_CARD_ORDER
                for card in prepared
                if _label_key(card) == "".join(label.split())
            ]
            for card in ordered:
                card["label"] = REALTIME_SUMMARY_DISPLAY_LABELS.get(
                    str(card.get("label") or ""), card.get("label")
                )
            return ordered

        difference = next(
            (card for card in visible if _label_key(card) == "较日前偏差"), None
        )
        energy = next(
            (card for card in visible if _label_key(card) == "实时出清电量"), None
        )
        if difference and energy and not difference.get("pending") and difference.get("value") is not None:
            energy["delta"] = difference["value"]
        visible = [card for card in visible if _label_key(card) != "较日前偏差"]
        for card in visible:
            if _label_key(card) in {"实时出清电量", "加权均价", "电能电价", "电能电费"} and card.get("delta") is not None:
                card["delta_prefix"] = "较日前"
        prepared = _move_label_to_end(_prepare_cards(visible), "平均负荷率")
        for card in prepared:
            card["label"] = REALTIME_SUMMARY_DISPLAY_LABELS.get(
                str(card.get("label") or ""), card.get("label")
            )
        return prepared
    prepared = _move_label_to_end(_prepare_cards(visible), "平均负荷率")
    for card in prepared:
        card["label"] = DAY_SUMMARY_DISPLAY_LABELS.get(
            str(card.get("label") or ""), card.get("label")
        )
    return prepared


def _find_card(cards: list[dict[str, Any]], *labels: str) -> dict[str, Any] | None:
    targets = {"".join(label.split()) for label in labels}
    return next((card for card in cards if _label_key(card) in targets), None)


def _place_table_cells(
    rows: list[dict[str, Any]],
    column_count: int,
) -> list[list[tuple[int, int, dict[str, Any]]]]:
    blocked_until = [0] * column_count
    placed_rows: list[list[tuple[int, int, dict[str, Any]]]] = []
    for row_index, row in enumerate(rows):
        column = 0
        placed: list[tuple[int, int, dict[str, Any]]] = []
        for source_cell in row.get("cells", []):
            while column < column_count and blocked_until[column] > row_index:
                column += 1
            cell = deepcopy(source_cell)
            colspan = max(1, int(cell.get("colspan") or 1))
            rowspan = max(1, int(cell.get("rowspan") or 1))
            end = column + colspan
            if end > column_count:
                raise ValueError("表格合并单元格超出列范围")
            placed.append((column, end, cell))
            if rowspan > 1:
                for occupied in range(column, end):
                    blocked_until[occupied] = row_index + rowspan
            column = end
        placed_rows.append(placed)
    return placed_rows


def _slice_table_rows(
    rows: list[dict[str, Any]],
    column_count: int,
    column_groups: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    placed_rows = _place_table_cells(rows, column_count)
    result: list[dict[str, Any]] = []
    for source_row, placed in zip(rows, placed_rows, strict=True):
        cells = [
            cell
            for start, end, cell in placed
            if any(
                start >= group_start and end <= group_end
                for group_start, group_end in column_groups
            )
        ]
        result.append({"height": source_row.get("height"), "cells": cells})
    return result


def _row_text(row: dict[str, Any]) -> str:
    values = [str(cell.get("value") or "").strip() for cell in row.get("cells", [])]
    return " ".join(value for value in values if value)


DAY_RECAP_PLANTS = {
    "汕头": ("shantou", "muted"),
    "海门": ("haimen", "plain"),
    "东莞": ("dongguan", "muted"),
    "清远": ("qingyuan", "plain"),
}


def _annotate_day_recap_groups(
    rows: list[dict[str, Any]],
    header_rows: int,
) -> list[dict[str, Any]]:
    prepared = deepcopy(rows)
    active_key = ""
    active_tone = ""
    remaining = 0
    subtable_header_rows = 0
    for index, row in enumerate(prepared):
        if index < header_rows:
            continue
        values = {
            str(cell.get("value") or "").strip()
            for cell in row.get("cells", [])
        }
        if "新能源" in values:
            subtable_header_rows = 2
        if subtable_header_rows > 0:
            row["subtable_header"] = True
            row["subtable_header_start"] = subtable_header_rows == 2
            subtable_header_rows -= 1
        plant_cell = next(
            (cell for cell in row.get("cells", [])
             if str(cell.get("value") or "").strip() in DAY_RECAP_PLANTS),
            None,
        )
        if plant_cell:
            plant = str(plant_cell["value"]).strip()
            active_key, active_tone = DAY_RECAP_PLANTS[plant]
            remaining = max(1, int(plant_cell.get("rowspan") or 1))
            row["group_start"] = True
        if remaining > 0:
            row["group_key"] = active_key
            row["group_tone"] = active_tone
            row.setdefault("group_start", False)
            remaining -= 1
        else:
            active_key = ""
            active_tone = ""
    return prepared


def _prioritize_source_table_style(style: str) -> str:
    rules: list[str] = []
    has_background = False
    for source_rule in str(style or "").split(";"):
        rule = source_rule.strip()
        if not rule:
            continue
        property_name = rule.split(":", 1)[0].strip()
        if property_name == "background-color":
            has_background = True
        if property_name == "background-color" or property_name.startswith("border-"):
            rule = f"{rule}!important"
        rules.append(rule)
    if not has_background:
        rules.append("background-color:#FFFFFF!important")
    return ";".join(rules)


def _classify_appendix_rows(
    rows: list[dict[str, Any]],
    *,
    preserve_source_labels: bool = False,
) -> list[dict[str, Any]]:
    prepared = deepcopy(rows)
    active_rowspans: dict[int, int] = {}
    for row in prepared:
        occupied = {column for column, remaining in active_rowspans.items() if remaining > 0}
        source_column = 0
        for cell in row.get("cells", []):
            while source_column in occupied:
                source_column += 1
            colspan = max(1, int(cell.get("colspan") or 1))
            rowspan = max(1, int(cell.get("rowspan") or 1))
            cell["source_column"] = source_column
            if rowspan > 1:
                for column in range(source_column, source_column + colspan):
                    active_rowspans[column] = max(active_rowspans.get(column, 0), rowspan)
            source_column += colspan
            cell_value = cell.get("value")
            if isinstance(cell_value, str) and not preserve_source_labels:
                cell_value = re.sub(
                    r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])[\r\n\t ]+(?=[\u3400-\u4dbf\u4e00-\u9fff])",
                    "",
                    cell_value,
                )
            cell["value"] = (
                cell_value
                if preserve_source_labels
                else APPENDIX_HEADER_LABELS.get(cell_value, cell_value)
            )
            cell_text = str(cell.get("value") or "").strip()
            compact_chinese_text = re.sub(r"\s+", "", cell_text)
            cell["compact_tall_cell"] = (
                colspan == 1
                and rowspan == 1
                and bool(
                    re.fullmatch(
                        r"[\u3400-\u4dbf\u4e00-\u9fff]{4,6}",
                        compact_chinese_text,
                    )
                    or re.fullmatch(r"#\d+#\d+机组", cell_text)
                )
            )
            cell["wrap_parts"] = (
                [compact_chinese_text[:2], compact_chinese_text[2:]]
                if re.fullmatch(
                    r"[\u3400-\u4dbf\u4e00-\u9fff]{4,5}",
                    compact_chinese_text,
                )
                else []
            )
            if compact_chinese_text in {
                "日前出清", "当日合计", "汕头海风", "鮀莲光伏", "𬶍莲光伏", "归湖光伏", "实时偏差"
            }:
                cell["wrap_parts"] = [compact_chinese_text]
            elif compact_chinese_text == "分公司火电":
                cell["wrap_parts"] = ["分公司", "火电"]
            normalized = str(cell.get("value") or "").strip().replace(",", "")
            cell["kind"] = (
                "number"
                if re.fullmatch(r"-?\d+(?:\.\d+)?", normalized)
                else "text"
            )
        active_rowspans = {
            column: remaining - 1
            for column, remaining in active_rowspans.items()
            if remaining - 1 > 0
        }
    return prepared


def _hourly_header_row_count(rows: list[dict[str, Any]]) -> int:
    for index, row in enumerate(rows):
        if any(
            re.fullmatch(r"(?:[01]?\d|2[0-3]):00", str(cell.get("value") or "").strip())
            for cell in row.get("cells", [])
        ):
            return index
    return min(4, len(rows))


def _move_realtime_profit_column_last(block: dict[str, Any]) -> dict[str, Any]:
    """Reorder displayed columns while retaining source-column identity and cells."""
    prepared = deepcopy(block)
    if prepared.get("block_kind") != "realtime-recap":
        return prepared
    headers = [
        cell
        for row in prepared.get("rows", [])[:prepared.get("header_rows", 0)]
        for cell in row.get("cells", [])
        if re.sub(r"\s+", "", str(cell.get("value") or "")) == "整体盈亏"
    ]
    if not headers:
        return prepared
    columns = {cell["source_column"] for cell in headers}
    if len(columns) != 1 or any(int(cell.get("colspan") or 1) != 1 for cell in headers):
        raise ValueError("实时总览的整体盈亏表头未对应唯一单列，无法调整列顺序")
    profit_column = columns.pop()
    widths = prepared.get("column_widths", [])
    old_order = prepared.get("display_column_order", list(range(len(widths))))
    if sorted(old_order) != list(range(len(widths))) or profit_column not in old_order:
        raise ValueError("实时总览列宽与整体盈亏列位置不一致")
    new_order = [column for column in old_order if column != profit_column] + [profit_column]
    if new_order == old_order:
        return prepared
    positions = {column: index for index, column in enumerate(new_order)}

    def left_column(cell: dict[str, Any]) -> int:
        start = cell["source_column"]
        source_columns = range(start, start + max(1, int(cell.get("colspan") or 1)))
        if any(column not in positions for column in source_columns):
            raise ValueError("实时总览单元格超出已知列范围")
        mapped = sorted(positions[column] for column in source_columns)
        if mapped != list(range(mapped[0], mapped[-1] + 1)):
            raise ValueError("实时总览合并单元格跨越待移动列，无法安全调整")
        return mapped[0]

    for row in prepared.get("rows", []):
        row["cells"] = sorted(row.get("cells", []), key=left_column)
    prepared["column_widths"] = [widths[old_order.index(column)] for column in new_order]
    prepared["display_column_order"] = new_order
    return prepared


def _table_block(
    title: str,
    unit: str,
    rows: list[dict[str, Any]],
    header_rows: int,
    column_widths: list[float],
    source_range: str,
    block_kind: str,
    *,
    source_faithful: bool = False,
) -> dict[str, Any]:
    widths = list(column_widths)
    prepared_rows = _classify_appendix_rows(
        rows,
        preserve_source_labels=source_faithful,
    )
    if source_faithful:
        for row in prepared_rows:
            for cell in row.get("cells", []):
                cell["style"] = _prioritize_source_table_style(cell.get("style", ""))
    block = {
        "title": title,
        "unit": unit,
        "rows": prepared_rows,
        "header_rows": min(header_rows, len(rows)),
        "column_widths": widths,
        "min_width": sum(widths),
        "range": source_range,
        "block_kind": block_kind,
    }
    return block


def _prepare_appendix_for_display(
    table: dict[str, Any],
    market_kind: str,
    *,
    source_faithful_day_recap: bool = False,
) -> dict[str, Any]:
    prepared = deepcopy(table)
    visible_rows: list[dict[str, Any]] = []
    notes: list[str] = []
    for row in prepared.get("rows", []):
        combined = _row_text(row)
        if combined.startswith("备注："):
            note = combined.removeprefix("备注：").strip()
            if note:
                notes.append(note)
            continue
        if re.match(r"^\d+、", combined):
            notes.append(combined)
            continue
        visible_rows.append(row)

    widths = list(prepared.get("column_widths", []))
    source_range = prepared.get("range", "")
    title = ""
    unit = ""
    data_rows = visible_rows
    if visible_rows and "复盘" in _row_text(visible_rows[0]):
        title = _row_text(visible_rows[0])
        data_rows = visible_rows[1:]
        if data_rows and _row_text(data_rows[0]).startswith("单位："):
            unit = _row_text(data_rows[0])
            data_rows = data_rows[1:]

    if market_kind == "day_ahead":
        hourly_index = next(
            (
                index
                for index, row in enumerate(data_rows)
                if "火电分时明细" in _row_text(row)
                and "新能源分时明细" in _row_text(row)
            ),
            None,
        )
        if hourly_index is not None and len(widths) >= 17:
            title_row = data_rows[hourly_index]
            hourly_titles = [
                str(cell.get("value") or "").strip()
                for cell in title_row.get("cells", [])
                if str(cell.get("value") or "").strip()
            ]
            hourly_rows = data_rows[hourly_index + 1 :]
            recap_rows = _annotate_day_recap_groups(
                data_rows[:hourly_index],
                header_rows=2,
            )
            fire_title = next(
                (value for value in hourly_titles if "火电分时明细" in value),
                "火电分时明细",
            )
            combined_hourly_title = fire_title.replace(
                "火电分时明细",
                "火电及新能源日前分时明细",
            )
            blocks = [
                _table_block(
                    title or "分公司日前出清复盘",
                    unit,
                    recap_rows,
                    2,
                    widths,
                    source_range,
                    "day-recap",
                    source_faithful=source_faithful_day_recap,
                ),
                _table_block(
                    combined_hourly_title,
                    unit,
                    hourly_rows,
                    _hourly_header_row_count(hourly_rows),
                    widths,
                    source_range,
                    "day-hourly",
                ),
            ]
        else:
            blocks = [
                _table_block(
                    title or "日前出清明细",
                    unit,
                    data_rows,
                    1,
                    widths,
                    source_range,
                    "day-recap",
                    source_faithful=source_faithful_day_recap,
                )
            ]
        if source_faithful_day_recap:
            blocks[0]["notes"] = list(notes)
        return {
            "blocks": blocks,
            "field_notes": deepcopy(DAY_FIELD_NOTES),
            "notes": notes,
        }

    blocks = [
        _table_block(
            title or "实时出清明细",
            unit,
            data_rows,
            2 if title else 1,
            widths,
            source_range,
            "realtime-recap",
        )
    ]
    return {
        "blocks": blocks,
        "field_notes": deepcopy(REALTIME_FIELD_NOTES),
        "notes": notes,
    }


def _card_clause(
    title: str,
    card: dict[str, Any] | None,
    *,
    price: bool = False,
) -> str | None:
    if not card or card.get("pending") or card.get("value") is None:
        return None
    unit = card.get("unit") or ""
    clause = f"{title}为{_format_number(card['value'])}{unit}"
    delta = card.get("delta")
    if delta is None:
        return clause
    delta_number = float(delta)
    prefix = card.get("delta_prefix") or "较日前"
    if math.isclose(delta_number, 0.0, abs_tol=1e-9):
        return f"{clause}，{prefix}持平"
    if price:
        verb = "上涨" if delta_number > 0 else "下降"
    else:
        verb = "增加" if delta_number > 0 else "减少"
    return f"{clause}，{prefix}{verb}{_format_number(abs(delta_number))}{unit}"


def _join_narrative(parts: list[str | None], prefix: str = "") -> str:
    visible = [part for part in parts if part]
    return f"{prefix}{'；'.join(visible)}。" if visible else "数据待更新。"


def _delta_direction(card: dict[str, Any] | None) -> int | None:
    if not card or card.get("pending") or card.get("delta") is None:
        return None
    delta = float(card["delta"])
    if math.isclose(delta, 0.0, abs_tol=1e-9):
        return 0
    return 1 if delta > 0 else -1


def _build_realtime_narratives(
    market_cards: list[dict[str, Any]],
    summary_cards: list[dict[str, Any]],
    raw_summary_cards: list[dict[str, Any]],
    date_label: str | None,
) -> dict[str, str]:
    company_cards = summary_cards + raw_summary_cards
    market_parts = [
        _card_clause(
            "广东统调实际负荷峰值",
            _find_card(market_cards, "统调实际负荷峰值", "统调负荷峰值"),
        ),
        _card_clause(
            "谷值",
            _find_card(market_cards, "统调实际负荷谷值", "统调负荷谷值"),
        ),
        _card_clause(
            "在线机组",
            _find_card(market_cards, "在线机组数", "市场开机"),
        ),
        _card_clause("检修容量", _find_card(market_cards, "检修容量")),
        _card_clause(
            "全省实时均价",
            _find_card(market_cards, "全省实时均价"),
            price=True,
        ),
    ]
    company_parts: list[str | None] = []
    node_range = _card_clause(
        "节点电价区间",
        _find_card(company_cards, "节点电价区间"),
        price=True,
    )
    if node_range:
        company_parts.append(f"{node_range}，价格波动区间较宽")

    insights: list[str] = []
    profit = _find_card(company_cards, "整体盈亏")
    if profit and not profit.get("pending") and isinstance(profit.get("value"), (int, float)):
        profit_value = float(profit["value"])
        if math.isclose(profit_value, 0.0, abs_tol=1e-9):
            insights.append("分公司整体盈亏基本持平")
        elif profit_value > 0:
            insights.append("分公司整体保持盈利")
        else:
            insights.append("分公司整体出现亏损")

    energy_direction = _delta_direction(_find_card(company_cards, "实时出清电量"))
    if energy_direction is not None:
        insights.append(
            "出清电量较日前回升"
            if energy_direction > 0
            else "出清电量较日前回落"
            if energy_direction < 0
            else "出清电量较日前基本持平"
        )
    if insights:
        company_parts.append(
            f"{'，'.join(insights)}，需关注高价时段出清表现及计划执行偏差"
        )
    return {
        "market": _join_narrative(
            market_parts,
            f"{date_label}，" if date_label else "",
        ),
        "company": _join_narrative(company_parts),
    }


def _build_realtime_review_points(
    market_cards: list[dict[str, Any]],
    summary_cards: list[dict[str, Any]],
) -> list[str]:
    points: list[str] = []
    market_price = _find_card(market_cards, "全省实时均价")
    market_direction = _delta_direction(market_price)
    if market_direction is not None:
        prefix = market_price.get("delta_prefix") or "较日前"
        movement = "上升" if market_direction > 0 else "下降" if market_direction < 0 else "持平"
        level = "回升" if market_direction > 0 else "回落" if market_direction < 0 else "保持稳定"
        points.append(f"全省实时均价{prefix}{movement}，市场价格水平{level}。")

    energy = _find_card(summary_cards, "实时出清电量")
    energy_direction = _delta_direction(energy)
    if energy_direction is not None:
        movement = "上升" if energy_direction > 0 else "下降" if energy_direction < 0 else "基本持平"
        points.append(
            f"分公司实时出清电量较日前{movement}，应持续跟踪负荷及计划偏差。"
        )

    company_price = _find_card(summary_cards, "加权均价")
    company_direction = _delta_direction(company_price)
    if company_direction is not None:
        movement = "上升" if company_direction > 0 else "下降" if company_direction < 0 else "基本持平"
        points.append(
            f"分公司加权均价较日前{movement}，需结合节点价差关注收益变化。"
        )
    return points


def _prepare_day_ahead_operations(
    operations: dict[str, Any], analysis_date: date
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for raw_row in operations.get("rows", []):
        statuses = [
            str(value or "").strip() for value in raw_row.get("statuses", [])
        ]
        current = statuses[0] if statuses else ""
        if current == "与前一日运行方式一致。":
            current = statuses[1] if len(statuses) > 1 else ""
        rows.append(
            {
                "unit": str(raw_row.get("unit") or "—"),
                "statuses": [current or "数据待更新"],
            }
        )
    return {
        "dates": [f"D+1日（日前，{analysis_date.month}月{analysis_date.day}日）"],
        "rows": rows,
    }


def prepare_report_data(
    report_data: dict[str, Any],
    *,
    operations_mode: str = "single",
    source_faithful_day_recap: bool = False,
) -> tuple[dict[str, Any], date]:
    if operations_mode not in {"single", "comparison"}:
        raise ValueError(f"未知运行方式展示模式：{operations_mode}")
    data = deepcopy(report_data)
    analysis_date = date.fromisoformat(data["report_date"])
    raw_operations = deepcopy(data.get("operations", {}))
    data["market"]["day_ahead"] = _prepare_market_cards(data["market"]["day_ahead"])
    data["market"]["realtime"] = _prepare_market_cards(
        data["market"]["realtime"], realtime=True
    )
    data["day_ahead"]["cards"] = _prepare_summary_cards(data["day_ahead"]["cards"])
    raw_realtime_cards = data["realtime"]["cards"]
    data["realtime"]["cards"] = _prepare_summary_cards(
        raw_realtime_cards, realtime=True
    )
    data["realtime"]["narratives"] = _build_realtime_narratives(
        data["market"]["realtime"],
        data["realtime"]["cards"],
        raw_realtime_cards,
        data["market"].get("realtime_date_label"),
    )
    data["realtime"]["review_points"] = _build_realtime_review_points(
        data["market"]["realtime"], data["realtime"]["cards"]
    )
    data["followup_points"] = deepcopy(FOLLOWUP_POINTS)
    data["appendices"]["day_ahead"] = _prepare_appendix_for_display(
        data["appendices"]["day_ahead"],
        "day_ahead",
        source_faithful_day_recap=source_faithful_day_recap,
    )
    data["appendices"]["realtime"] = _prepare_appendix_for_display(
        data["appendices"]["realtime"], "realtime"
    )
    data["operations"] = (
        _prepare_day_ahead_operations(raw_operations, analysis_date)
        if operations_mode == "single"
        else raw_operations
    )
    return data, analysis_date


