from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.analyze_500kv_section_gaps import (
    MIN_SECTION_INTERVALS,
    match_price_rows,
    parse_topology_edges,
    read_price_workbook,
)
from src.classify_500kv_nodes import topology_inventory
from src.node_city_catalog import NODE_CITY, city_pair_label, price_hierarchy
from src.region_curve_view import VIEW_HTML, view_assets
from src.map_html_view import render_map_html


REGION_CENTERS = {
    "粤北片": (113.55, 24.80),
    "清远片": (113.05, 23.75),
    "广州北部片": (113.35, 23.35),
    "佛山片": (113.05, 23.05),
    "广州南部片": (113.55, 22.95),
    "肇云片": (112.25, 23.25),
    "江门片": (112.75, 22.45),
    "阳江片": (111.75, 21.85),
    "茂湛片": (110.45, 21.35),
    "中珠片": (113.45, 22.25),
    "南粤柔直": (113.55, 21.95),
    "东莞片": (113.85, 22.95),
    "深圳片": (114.25, 22.55),
    "惠州片": (114.35, 23.05),
    "梅州河源片": (115.40, 24.05),
    "粤东片": (116.60, 23.55),
}

NODE_OVERRIDES = {
    "丹霞": (113.72, 25.05),
    "曲江": (113.58, 24.75),
    "库湾": (113.10, 24.95),
    "贤令山": (112.95, 24.25),
    "东坡": (113.25, 24.55),
    "振兴": (113.08, 24.10),
    "从西": (112.95, 23.90),
    "科北": (113.15, 23.65),
    "渡水": (113.35, 23.85),
    "凤安": (113.55, 23.65),
    "花都": (113.20, 23.40),
    "北郊": (113.25, 23.25),
    "木棉": (113.45, 23.25),
    # 以下公开地图可核实点使用实际站址；其余点仍按公开工程地点/片区相对方位近似。
    "罗洞": (112.99281, 23.15686),
    "西江": (112.80, 23.05),
    "凤城": (113.08991, 22.83043),
    "广南": (113.20, 22.95),
    "顺德": (113.25, 22.75),
    "楚庭": (113.26404, 22.99141),
    "狮洋": (113.70, 22.72),
    "玉城": (111.95, 23.35),
    "砚都": (112.25, 23.05),
    "沧江": (112.55, 22.90),
    "侨乡": (112.75, 22.65),
    "江门": (113.05, 22.30),
    "卧龙": (111.85, 21.95),
    "安澜": (111.65, 21.75),
    "蝶岭": (111.35, 21.90),
    "茂名": (110.95, 21.65),
    "鹅凰B": (110.65, 21.40),
    "立安": (110.35, 21.25),
    "港城": (110.25, 21.05),
    "东海岛": (110.55, 20.95),
    "芷寮": (110.85, 21.35),
    "香山": (113.40, 22.30),
    "主峰": (113.55, 22.25),
    "桂山": (113.80, 22.10),
    "金鼎": (113.60, 22.40),
    "加林": (113.25, 22.25),
    "文山": (113.10, 22.20),
    "南粤": (113.90, 21.95),
    "水乡": (113.65, 22.90),
    "崇焕": (113.85, 23.05),
    "莞城": (113.80, 22.95),
    "横沥": (114.05, 23.00),
    "宝安": (113.90, 22.60),
    "盛丰": (114.15, 22.65),
    "鹏城": (114.25, 22.55),
    "深圳": (114.10, 22.50),
    "现代": (114.35, 22.60),
    "东方": (114.45, 22.52),
    "紫荆": (114.55, 22.62),
    "屹百": (114.65, 22.72),
    "博罗": (114.18159, 23.24922),
    "穗东": (114.05, 23.00),
    "惠州": (114.55, 23.05),
    "崇文": (114.75, 23.25),
    "上寨": (115.25, 24.10),
    "敬州": (115.70, 24.25),
    "嘉应": (116.10, 24.00),
    "承龙开关站": (116.30, 23.75),
    "陆丰": (115.65, 22.95),
    "吉康": (115.95, 23.25),
    "征程": (116.15, 23.35),
    "福园": (116.45, 23.35),
    "祯州": (116.75, 23.20),
    "岐山": (116.65, 23.55),
    "榕江": (116.35, 23.70),
    "盘龙": (116.85, 23.65),
    "汕头": (116.95, 23.40),
    "韩江": (117.10, 23.65),
}

# 坐标来源等级会直接显示在节点悬浮提示中，避免把示意坐标误当成测绘坐标。
VERIFIED_NODE_SOURCES = {
    "罗洞": "OpenStreetMap公开站址",
    "凤城": "OpenStreetMap公开工程站址",
    "楚庭": "广州市发改委公开站址（番禺石壁二村）",
    "博罗": "OpenStreetMap公开站址",
}

REGION_COLORS = {
    "粤北、清远": "#38bdf8", "广佛、江门、珠海": "#8b5cf6", "东莞、深圳、惠州": "#14b8a6",
    "粤东": "#f59e0b", "粤西": "#f97316",
    "粤北片": "#6b8e23", "清远片": "#2e8b57", "广州北部片": "#008b8b",
    "佛山片": "#3b82c4", "广州南部片": "#5b6fd8", "肇云片": "#8467c4",
    "江门片": "#a05fb4", "阳江片": "#c35a88", "茂湛片": "#c7654c",
    "中珠片": "#d08b32", "南粤柔直": "#9a7b2f", "东莞片": "#4195a6",
    "深圳片": "#2878b8", "惠州片": "#467aa1", "梅州河源片": "#7b6aa8",
    "粤东片": "#a45b72", "未分片": "#7b8794",
}

TOPOLOGY_GROUP_REGIONS = {
    "粤北、清远": "粤北、清远",
    "广佛、江门、珠海": "广佛、江门、珠海",
    "东莞、深圳、惠州": "东莞、深圳、惠州",
    "粤东": "粤东",
    "粤西": "粤西",
    "江门、粤西": "粤西",
}

# 由公开广东省行政边界简化而来，保留主要海岸线和省界转折，取代原先的示意多边形。
GUANGDONG_OUTLINE = [
    (113.0872, 22.1261), (113.2668, 21.8716), (113.5658, 22.0727),
    (113.6699, 22.4356), (113.5687, 22.4119), (113.7405, 22.5343),
    (113.6977, 22.7374), (113.8918, 22.4426), (114.3983, 22.6028),
    (114.5069, 22.4386), (114.6278, 22.5030), (114.5145, 22.6607),
    (114.7096, 22.7877), (114.7474, 22.5816), (114.8836, 22.5402),
    (115.1986, 22.8219), (115.5742, 22.6505), (115.6324, 22.8600),
    (115.8187, 22.7314), (116.5056, 22.9309), (116.5664, 23.1343),
    (116.8147, 23.2076), (116.8992, 23.5189), (117.1929, 23.5617),
    (116.9620, 23.8614), (116.9987, 24.1790), (116.7583, 24.5466),
    (116.8012, 24.6783), (116.5301, 24.6049), (116.3948, 24.8779),
    (116.2513, 24.7934), (115.8973, 24.9371), (115.7571, 24.7499),
    (115.8441, 24.5627), (115.6861, 24.5470), (115.4129, 24.7929),
    (114.4288, 24.4863), (114.1690, 24.6897), (114.7356, 25.1220),
    (114.7429, 25.2745), (114.5404, 25.4170), (114.0399, 25.2507),
    (113.9979, 25.4442), (113.5860, 25.3074), (113.3033, 25.5158),
    (113.1520, 25.4922), (112.8518, 25.3334), (113.0345, 25.2014),
    (113.0119, 24.9461), (112.8735, 24.8967), (112.6598, 25.1328),
    (112.1873, 25.1831), (112.1711, 24.8627), (111.9275, 24.6294),
    (112.0618, 24.3682), (111.8779, 24.2289), (111.9403, 23.9877),
    (111.8106, 23.8070), (111.6549, 23.8333), (111.6653, 23.7000),
    (111.3997, 23.4694), (111.3587, 22.8892), (110.7602, 22.5808),
    (110.6835, 22.4734), (110.7897, 22.2867), (110.6789, 22.1727),
    (110.3485, 22.1959), (110.3876, 21.8904), (109.9862, 21.8791),
    (109.8983, 21.6496), (109.7664, 21.6680), (109.7856, 21.4569),
    (109.9047, 21.4301), (109.7575, 21.3468), (109.6549, 20.9035),
    (109.9135, 20.2194), (110.3275, 20.2516), (110.5458, 20.4275),
    (110.3937, 20.8167), (110.1777, 20.9071), (110.4224, 21.1907),
    (111.0613, 21.4492), (111.8628, 21.5570), (112.1365, 21.7938),
    (112.2631, 21.6935), (112.6394, 21.7560), (112.8004, 21.9248),
    (112.9445, 21.8421), (113.0872, 22.1261),
]

LON_MIN, LON_MAX = 109.45, 117.45
LAT_MIN, LAT_MAX = 20.05, 25.65
SVG_W, SVG_H = 1500, 1020
DIAGRAM_PADDING_X = 92
DIAGRAM_PADDING_Y = 50


def build_map_html(
    topology_path: Path,
    original_price_path: Path,
    processed_price_path: Path,
    analysis_path: Path,
    output_path: Path,
    node_positions_path: Path | None = None,
    *,
    analysis_data: dict | None = None,
) -> None:
    topology_path = Path(topology_path)
    original_price_path = Path(original_price_path)
    processed_price_path = Path(processed_price_path)
    analysis_path = Path(analysis_path)
    output_path = Path(output_path)

    if analysis_data is None:
        edges, skipped_duplicates = parse_topology_edges(topology_path)
        time_headers, price_rows = read_price_workbook(processed_price_path)
        edge_summary = _read_sheet_dicts(analysis_path, "断面汇总")
        interval_details = _read_sheet_dicts(analysis_path, "逐时段明细")
        group_summary = _read_sheet_dicts(analysis_path, "片区汇总")
    else:
        edges, skipped_duplicates = analysis_data["edges"], analysis_data["skipped_duplicates"]
        time_headers, price_rows = analysis_data["time_headers"], analysis_data["price_rows"]
        edge_summary = _analysis_records(analysis_data["edge_summary"])
        interval_details = _analysis_records(analysis_data["interval_details"])
        group_summary = _analysis_records(analysis_data["group_summary"])
    topology_nodes = sorted({edge.a for edge in edges} | {edge.b for edge in edges})
    matched_rows = match_price_rows(topology_nodes, price_rows)
    node_regions = _parse_node_regions(topology_path)
    for edge in edges:
        inferred_region = TOPOLOGY_GROUP_REGIONS.get(edge.group)
        if inferred_region:
            node_regions.setdefault(edge.a, inferred_region)
            node_regions.setdefault(edge.b, inferred_region)
    raw_stats = _workbook_stats(original_price_path)
    processed_stats = _workbook_stats(processed_price_path)

    discovered_positions_path = (
        Path(node_positions_path)
        if node_positions_path is not None
        else topology_path.parent / "data" / "500kv_node_positions.json"
    )
    if discovered_positions_path.exists():
        coordinates, coordinate_metadata = _build_diagram_coordinates(
            topology_nodes,
            discovered_positions_path,
        )
    else:
        coordinates = _build_coordinates(topology_nodes, node_regions)
        coordinate_metadata = {
            "coordinate_mode": "geographic-fallback",
            "coordinate_source": "公开站址与片区相对方位",
            "node_positions_path": "",
        }
    severity_by_edge = _build_edge_payloads(
        edges,
        edge_summary,
        interval_details,
        coordinates,
        time_headers,
        matched_rows,
    )
    blocked_nodes = {edge["a"] for edge in severity_by_edge if edge["is_section"]} | {
        edge["b"] for edge in severity_by_edge if edge["is_section"]
    }
    nodes = _build_node_payloads(topology_nodes, node_regions, coordinates, matched_rows, time_headers, blocked_nodes)
    regions = _build_region_payloads(nodes)
    curve_names, connected_names = topology_inventory(topology_path)
    curve_regions = price_hierarchy(curve_names)
    administrative = {name: (region["name"], city["name"])
                      for region in curve_regions for city in region["cities"] for name in city["nodes"]}
    curve_rows = match_price_rows(curve_names, price_rows)
    curve_nodes = [{"name": name, "region": administrative[name][0], "city": administrative[name][1],
                    "connected": name in connected_names,
                    "prices": [curve_rows[name]["prices"].get(t) for t in time_headers] if name in curve_rows else []}
                   for region in curve_regions for city in region["cities"] for name in city["nodes"]]

    data = {
        "nodes": nodes,
        "regions": regions,
        "curve_data": {"regions": curve_regions, "nodes": curve_nodes},
        "edges": severity_by_edge,
        "time_headers": time_headers,
        "group_summary": group_summary,
        "metadata": {
            "original_path": str(original_price_path),
            "processed_path": str(processed_price_path),
            "analysis_path": str(analysis_path),
            "topology_path": str(topology_path),
            "original_rows": raw_stats["rows"],
            "processed_rows": processed_stats["rows"],
            "skipped_duplicate_edges": skipped_duplicates,
            "missing_price_nodes": [node for node in topology_nodes if node not in matched_rows],
            "min_section_intervals": MIN_SECTION_INTERVALS,
            "section_interval_basis": "daily_accumulated",
            "severity_basis": "intervals_and_max_abs_diff",
            "layout_canvas_width": SVG_W,
            "layout_canvas_height": SVG_H,
            **coordinate_metadata,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_html(data), encoding="utf-8")


def _analysis_records(records):
    # Keep the same numeric representation as the standalone XLSX round trip.
    from openpyxl.compat.strings import safe_string
    from openpyxl.worksheet._reader import _cast_number
    def value(item):
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            return _cast_number(safe_string(item))
        return _clean_cell(item)
    return [{key: value(item) for key, item in row.items()} for row in records]


def _read_sheet_dicts(path: Path, sheet_name: str) -> list[dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook[sheet_name]
        rows = sheet.iter_rows(values_only=True)
        try:
            headers = [str(value) for value in next(rows)]
        except StopIteration:
            return []
        return [{header: _clean_cell(value) for header, value in zip(headers, row)}
                for row in rows if not all(value is None for value in row)]
    finally:
        workbook.close()


def _parse_node_regions(topology_path: Path) -> dict[str, str]:
    text = Path(topology_path).read_text(encoding="utf-8")
    lines = text.splitlines()
    in_section = False
    current_region = ""
    mapping = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not in_section:
            in_section = bool(re.match(r"^##\s+1\.", line))
            continue
        if re.match(r"^##\s+2\.", line):
            break
        if not line:
            continue
        if line.startswith("### "):
            current_region = line[4:].strip()
            continue
        if current_region:
            for node in re.split(r"[，,、]", line):
                node = node.strip()
                if node and not node.startswith("原 "):
                    mapping[node] = current_region
    return mapping


def _workbook_stats(path: Path) -> dict[str, int]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = sum(max(sheet.max_row - 1, 0) for sheet in workbook.worksheets)
        return {"rows": rows, "sheets": len(workbook.worksheets)}
    finally:
        workbook.close()


def _build_coordinates(nodes: list[str], node_regions: dict[str, str]) -> dict[str, dict[str, float]]:
    region_counts: dict[str, int] = {}
    coordinates = {}
    for node in nodes:
        if node in NODE_OVERRIDES:
            lon, lat = NODE_OVERRIDES[node]
        else:
            region = node_regions.get(node, "")
            base_lon, base_lat = REGION_CENTERS.get(region, (113.6, 23.2))
            index = region_counts.get(region, 0)
            region_counts[region] = index + 1
            angle = index * 2.3999632297
            radius = 0.10 + 0.035 * index
            lon = base_lon + math.cos(angle) * radius
            lat = base_lat + math.sin(angle) * radius
        x, y = _project(lon, lat)
        coordinates[node] = {"lon": lon, "lat": lat, "x": x, "y": y}
    return coordinates


def _build_diagram_coordinates(
    nodes: list[str],
    positions_path: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    payload = json.loads(Path(positions_path).read_text(encoding="utf-8"))
    image = payload.get("image", {})
    image_width = float(image.get("width", 0))
    image_height = float(image.get("height", 0))
    if image_width <= 0 or image_height <= 0:
        raise ValueError(f"锚点文件缺少有效接线图尺寸: {positions_path}")

    records = {str(record.get("name", "")): record for record in payload.get("nodes", [])}
    missing = [
        node
        for node in nodes
        if node not in records
        or records[node].get("status") != "verified"
        or not records[node].get("anchor")
    ]
    if missing:
        raise ValueError(f"以下拓扑节点缺少已确认接线图锚点: {', '.join(missing)}")

    scale = min(
        (SVG_W - DIAGRAM_PADDING_X * 2) / image_width,
        (SVG_H - DIAGRAM_PADDING_Y * 2) / image_height,
    )
    offset_x = (SVG_W - image_width * scale) / 2
    offset_y = (SVG_H - image_height * scale) / 2
    coordinates: dict[str, dict[str, object]] = {}
    for node in nodes:
        anchor = records[node]["anchor"]
        diagram_x = float(anchor["x"])
        diagram_y = float(anchor["y"])
        coordinates[node] = {
            "x": round(offset_x + diagram_x * scale, 2),
            "y": round(offset_y + diagram_y * scale, 2),
            "diagram_x": round(diagram_x, 2),
            "diagram_y": round(diagram_y, 2),
            "position_label": f"接线图锚点 {diagram_x:.0f}, {diagram_y:.0f}",
        }

    return coordinates, {
        "coordinate_mode": "wiring-diagram",
        "coordinate_source": str(image.get("path", "接线图.png")),
        "node_positions_path": str(positions_path),
        "diagram_width": image_width,
        "diagram_height": image_height,
        "diagram_scale": round(scale, 8),
    }


def _build_node_payloads(
    nodes: list[str],
    node_regions: dict[str, str],
    coordinates: dict[str, dict[str, float]],
    matched_rows: dict[str, dict[str, object]],
    time_headers: list[str],
    blocked_nodes: set[str],
) -> list[dict[str, object]]:
    payload = []
    for node in nodes:
        price_row = matched_rows.get(node)
        coordinate = coordinates[node]
        prices = []
        raw_name = ""
        if price_row:
            raw_name = str(price_row["raw_name"])
            prices = [price_row["prices"].get(time_header) for time_header in time_headers]
        if "diagram_x" in coordinate:
            location_accuracy = "已确认接线图锚点"
            location_source = "人工校准接线图节点位置"
        else:
            location_accuracy = "公开站址" if node in VERIFIED_NODE_SOURCES else "片区校核近似"
            location_source = VERIFIED_NODE_SOURCES.get(node, "公开工程地点与片区相对方位")
            coordinate = {
                **coordinate,
                "position_label": f"{float(coordinate['lon']):.5f}, {float(coordinate['lat']):.5f}",
            }
        payload.append(
            {
                "name": node,
                "region": node_regions.get(node, "未分片"),
                "city": NODE_CITY.get(node, "未分类"),
                "raw_name": raw_name,
                "prices": prices,
                "blocked": node in blocked_nodes,
                "location_accuracy": location_accuracy,
                "location_source": location_source,
                **coordinate,
            }
        )
    return payload


def _build_region_payloads(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for node in nodes:
        grouped.setdefault(str(node["region"]), []).append(node)

    payload = []
    for region, members in sorted(grouped.items()):
        xs = [float(node["x"]) for node in members]
        ys = [float(node["y"]) for node in members]
        payload.append(
            {
                "name": region,
                "color": REGION_COLORS.get(region, REGION_COLORS["未分片"]),
                "count": len(members),
                "x": round(sum(xs) / len(xs), 2),
                "y": round(sum(ys) / len(ys), 2),
                "rx": round(max((max(xs) - min(xs)) / 2 + 30, 34), 2),
                "ry": round(max((max(ys) - min(ys)) / 2 + 24, 28), 2),
            }
        )
    return payload


def _build_edge_payloads(
    edges,
    edge_summary: list[dict[str, object]],
    interval_details: list[dict[str, object]],
    coordinates: dict[str, dict[str, float]],
    time_headers: list[str] | None = None,
    matched_rows: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    summary_by_edge = {
        str(row["edge"]): row
        for row in edge_summary
        if int(float(row.get("intervals", 0))) >= MIN_SECTION_INTERVALS
    }
    detail_counts = {}
    for row in interval_details:
        detail_counts[str(row["edge"])] = detail_counts.get(str(row["edge"]), 0) + 1
    flow_directions_by_edge = _build_flow_directions(
        edges,
        matched_rows or {},
        time_headers or [],
    )

    gap_scores = [float(row["max_abs_diff"]) for row in summary_by_edge.values()]
    interval_scores = [float(row["intervals"]) for row in summary_by_edge.values()]

    def normalize(value: float, values: list[float]) -> float:
        if not values or max(values) == min(values):
            return 1.0
        return (value - min(values)) / (max(values) - min(values))

    payload = []
    for edge in edges:
        edge_name = f"{edge.a}-{edge.b}"
        summary = summary_by_edge.get(edge_name)
        flow = flow_directions_by_edge.get(
            edge_name,
            {"kind": "none", "directions": [], "by_time": {}},
        )
        score = 0.0
        severity = 0.0
        gap_severity = 0.0
        interval_severity = 0.0
        if summary:
            score = float(summary["max_abs_diff"])
            gap_severity = normalize(score, gap_scores)
            interval_severity = normalize(float(summary["intervals"]), interval_scores)
            severity = 0.15 + 0.85 * (gap_severity + interval_severity) / 2

        payload.append(
            {
                "group": edge.group,
                "city_label": city_pair_label(edge.a, edge.b),
                "edge": edge_name,
                "a": edge.a,
                "b": edge.b,
                "x1": coordinates[edge.a]["x"],
                "y1": coordinates[edge.a]["y"],
                "x2": coordinates[edge.b]["x"],
                "y2": coordinates[edge.b]["y"],
                "is_section": bool(summary),
                "intervals": int(float(summary["intervals"])) if summary else 0,
                "windows": summary.get("windows", "") if summary else "",
                "max_abs_diff": float(summary["max_abs_diff"]) if summary else 0.0,
                "max_time": summary.get("max_time", "") if summary else "",
                "max_low": summary.get("max_low", "") if summary else "",
                "max_high": summary.get("max_high", "") if summary else "",
                "main_direction": summary.get("main_direction", "") if summary else "",
                "direction_counts": summary.get("direction_counts", "") if summary else "",
                "detail_count": detail_counts.get(edge_name, 0),
                "gap_severity": gap_severity,
                "interval_severity": interval_severity,
                "severity_score": severity,
                "severity": severity,
                "flow_kind": flow["kind"],
                "flow_directions": flow["directions"],
                "flow_by_time": flow["by_time"],
            }
        )
    return payload


def _build_flow_directions(
    edges,
    matched_rows: dict[str, dict[str, object]],
    time_headers: list[str],
) -> dict[str, dict[str, object]]:
    """Build price-direction summaries for every edge with two usable price series."""
    if not edges or not matched_rows or not time_headers:
        return {}

    time_index = {time: index for index, time in enumerate(time_headers)}
    rows_by_edge: dict[str, list[dict[str, object]]] = {}
    for edge in edges:
        price_row_a = matched_rows.get(edge.a)
        price_row_b = matched_rows.get(edge.b)
        if not price_row_a or not price_row_b:
            continue
        rows = []
        for time in time_headers:
            price_a = price_row_a["prices"].get(time)
            price_b = price_row_b["prices"].get(time)
            if not isinstance(price_a, (int, float)) or not isinstance(price_b, (int, float)):
                continue
            difference = float(price_b) - float(price_a)
            if abs(difference) < 1e-9:
                continue
            low, high = (edge.a, edge.b) if difference > 0 else (edge.b, edge.a)
            rows.append(
                {
                    "edge": f"{edge.a}-{edge.b}",
                    "time": time,
                    "direction": f"{low}->{high}",
                    "abs_diff": abs(difference),
                }
            )
        if rows:
            rows_by_edge[f"{edge.a}-{edge.b}"] = rows

    result: dict[str, dict[str, object]] = {}
    for edge_name, rows in rows_by_edge.items():
        rows.sort(key=lambda row: time_index[str(row["time"])])
        valid_rows: list[dict[str, object]] = []
        run: list[dict[str, object]] = []

        def finish_run() -> None:
            if len(run) >= MIN_SECTION_INTERVALS:
                valid_rows.extend(run)

        for row in rows:
            if not run:
                run = [row]
                continue
            previous = run[-1]
            is_next = time_index[str(row["time"])] == time_index[str(previous["time"])] + 1
            same_direction = str(row["direction"]) == str(previous["direction"])
            if is_next and same_direction:
                run.append(row)
            else:
                finish_run()
                run = [row]
        finish_run()

        direction_rows: dict[str, list[dict[str, object]]] = {}
        for row in valid_rows or rows:
            direction_rows.setdefault(str(row["direction"]), []).append(row)

        if not valid_rows:
            primary_direction = max(
                direction_rows,
                key=lambda direction: (len(direction_rows[direction]), direction),
            )
            direction_rows = {primary_direction: direction_rows[primary_direction]}

        directions = []
        for direction, direction_items in direction_rows.items():
            direction_items.sort(key=lambda row: time_index[str(row["time"])])
            directions.append(
                {
                    "direction": direction,
                    "intervals": len(direction_items),
                    "windows": _flow_windows(
                        [str(row["time"]) for row in direction_items],
                        time_headers,
                    ),
                    "max_abs_diff": max(
                        float(row.get("abs_diff", 0) or 0) for row in direction_items
                    ),
                }
            )
        directions.sort(key=lambda item: (-int(item["intervals"]), str(item["direction"])))
        result[edge_name] = {
            "kind": "bidirectional" if len(directions) > 1 else "single",
            "directions": directions,
            "by_time": {
                str(row["time"]): str(row["direction"])
                for row in rows
            },
        }
    return result


def _flow_windows(times: list[str], time_headers: list[str]) -> str:
    time_index = {time: index for index, time in enumerate(time_headers)}
    indexes = sorted({time_index[time] for time in times})
    windows = []
    start = previous = indexes[0]
    for index in indexes[1:]:
        if index == previous + 1:
            previous = index
            continue
        windows.append((time_headers[start], time_headers[previous]))
        start = previous = index
    windows.append((time_headers[start], time_headers[previous]))
    return "; ".join(start if start == end else f"{start}-{end}" for start, end in windows)


def _clean_cell(value):
    if value is None:
        return ""
    return value


def _project(lon: float, lat: float) -> tuple[float, float]:
    x = 84 + (lon - LON_MIN) / (LON_MAX - LON_MIN) * (SVG_W - 168)
    y = 72 + (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * (SVG_H - 144)
    return round(x, 2), round(y, 2)


def _outline_points() -> str:
    return " ".join(f"{x},{y}" for x, y in (_project(lon, lat) for lon, lat in GUANGDONG_OUTLINE))


def _render_html(data: dict[str, object]) -> str:
    curve_css, curve_js = view_assets()
    return render_map_html(data, outline=_outline_points(), SVG_W=SVG_W, SVG_H=SVG_H,
                           DIAGRAM_PADDING_X=DIAGRAM_PADDING_X, DIAGRAM_PADDING_Y=DIAGRAM_PADDING_Y,
                           curve_css=curve_css, curve_js=curve_js, VIEW_HTML=VIEW_HTML)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成广东500kV节点断面交互HTML地图。")
    parser.add_argument("--topology", required=True, type=Path, help="拓扑 Markdown 文件路径")
    parser.add_argument("--original-prices", required=True, type=Path, help="原始节点电价 Excel 文件路径")
    parser.add_argument("--processed-prices", required=True, type=Path, help="处理后的500kV节点电价 Excel 文件路径")
    parser.add_argument("--analysis", required=True, type=Path, help="断面分析 Excel 文件路径")
    parser.add_argument("--output", required=True, type=Path, help="输出 HTML 文件路径")
    parser.add_argument("--node-positions", type=Path, help="已确认的接线图节点锚点 JSON；默认从拓扑文件旁的 data 目录自动发现")
    args = parser.parse_args()

    build_map_html(
        topology_path=args.topology,
        original_price_path=args.original_prices,
        processed_price_path=args.processed_prices,
        analysis_path=args.analysis,
        output_path=args.output,
        node_positions_path=args.node_positions,
    )
    print(f"HTML地图已生成: {args.output}")


if __name__ == "__main__":
    main()
