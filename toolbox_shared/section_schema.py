"""Shared 500/220kV naming, topology and workbook header contracts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TIME_HEADER_RE = re.compile(r"^\d{2}:\d{2}$")
NODE_VOLTAGE_RE = re.compile(r"(?<!\d)(\d+)\s*[_-]?\s*k\s*v", re.IGNORECASE)
SECTION_2_RE = re.compile(r"^##\s+2\.")
SECTION_NEXT_RE = re.compile(r"^##\s+3\.")


@dataclass(frozen=True)
class TopologyEdge:
    group: str
    a: str
    b: str


def parse_topology_edges(topology_path: Path) -> tuple[list[TopologyEdge], list[tuple[str, str, str]]]:
    in_section = False
    current_group = ""
    edges = []
    seen: set[tuple[str, str]] = set()
    skipped_duplicates = []

    for raw_line in Path(topology_path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not in_section:
            in_section = bool(SECTION_2_RE.match(line))
            continue

        if SECTION_NEXT_RE.match(line):
            break
        if not line:
            continue
        if line.startswith("### "):
            current_group = line[4:].strip()
            continue
        if not current_group:
            continue

        if "：" in line:
            a, neighbors_text = (value.strip() for value in line.split("：", 1))
            parts = [f"{a}-{neighbor.strip()}" for neighbor in re.split(r"[，,、]", neighbors_text) if neighbor.strip()]
        else:
            parts = re.split(r"[，,、]", line)

        for part in parts:
            part = part.strip()
            if not part or "-" not in part:
                continue
            a, b = (value.strip() for value in part.split("-", 1))
            edge_key = tuple(sorted((a, b)))
            if edge_key in seen:
                skipped_duplicates.append((a, b, current_group))
                continue
            seen.add(edge_key)
            edges.append(TopologyEdge(current_group, a, b))

    if not edges:
        raise ValueError(f"拓扑文件未解析到“## 2.”下的边: {topology_path}")
    return edges, skipped_duplicates


def price_node_voltage(name: object) -> int | None:
    """Use the final voltage label (the earlier label may describe the station)."""
    labels = NODE_VOLTAGE_RE.findall("" if name is None else str(name))
    return int(labels[-1]) if labels else None


def normalize_price_node_name(name: object) -> str:
    text = "" if name is None else str(name).strip()
    text = re.sub(r"^其他", "", text)
    text = text.replace("开关站", "")
    text = text.replace("换流站", "")
    text = text.replace("站", "")
    text = text.replace("变", "")
    text = re.sub(r"\(500kV\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"500\s*kV.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"220\s*kV.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"#.*$", "", text)
    text = re.sub(r"\d+母$", "", text)
    return text.strip()


def _column_map(header: tuple[object, ...]) -> dict[str, object] | None:
    cleaned = ["" if value is None else str(value).strip() for value in header]
    try:
        node_index = cleaned.index("节点名称")
    except ValueError:
        return None

    time_columns = [index for index, value in enumerate(cleaned) if TIME_HEADER_RE.match(value)]
    if not time_columns:
        return None

    return {
        "node": node_index,
        "time_columns": time_columns,
        "time_headers": [cleaned[index] for index in time_columns],
    }
