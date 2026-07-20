from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


DEFAULT_TOPOLOGY = ROOT / "500kV节点拓扑.md"
DEFAULT_IMAGE = ROOT / "接线图.png"
DEFAULT_SPOTMAP_DATA = Path(
    os.environ.get("SPOTMAP_OCR_DATA", ROOT / "local_data" / "ocr-records.json")
)
DEFAULT_OUTPUT = ROOT / "data" / "500kv_node_position_candidates.json"
DEFAULT_PREVIEW = ROOT / "data" / "500kv_node_position_candidates.png"
DEFAULT_CONTACT_SHEET = ROOT / "data" / "500kv_node_position_candidates_contact_sheet.png"

FACILITY_SUFFIXES = (
    "海风场陆上站",
    "陆上集控站",
    "换流站",
    "开关站",
    "升压站",
    "核电厂",
    "电厂",
    "陆上站",
    "站",
    "变",
    "厂",
)

ALIASES = {
    "鹊垌": {"鹊洞"},
    "太平岭": {"太平"},
}


@dataclass(frozen=True)
class TextCandidate:
    record: dict[str, object]
    matched_text: str
    source: str
    score: float


def parse_topology_edges_light(path: Path) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    in_section = False
    group = ""
    edges: list[tuple[str, str, str]] = []
    duplicates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not in_section:
            in_section = bool(re.match(r"^##\s+2\.", line))
            continue
        if re.match(r"^##\s+3\.", line):
            break
        if line.startswith("### "):
            group = line[4:].strip()
            continue
        if not line or not group or "-" not in line:
            continue
        parts = re.split(r"[，,、]", line)
        for part in parts:
            if "-" not in part:
                continue
            left, right = (value.strip() for value in part.split("-", 1))
            key = tuple(sorted((left, right)))
            if key in seen:
                duplicates.append((left, right, group))
            else:
                seen.add(key)
                edges.append((group, left, right))
    if not edges:
        raise ValueError(f"拓扑文件未解析到第二部分连接: {path}")
    return edges, duplicates


def clean_text(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"500\s*kV.*$", "", value, flags=re.IGNORECASE)
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value)


def name_variants(text: object, *, ocr: bool = False) -> set[str]:
    value = clean_text(text)
    variants = {value}
    for suffix in FACILITY_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            variants.add(value[: -len(suffix)])
    # “核电厂”既可能在图上写作“核电”，也可能只写站名。
    if value.endswith("核电厂"):
        variants.add(value[:-1])
        variants.add(value[: -len("核电厂")])
    if value.endswith("电厂"):
        variants.add(value[:-1])
        variants.add(value[: -len("电厂")])
    if ocr and len(value) >= 3 and value[-1:] in {"A", "B", "Y"}:
        variants.add(value[:-1])
    if ocr:
        # OCR 偶尔会把母线字母后的线路字符黏到站名上。
        match = re.match(r"^([\u4e00-\u9fff]{2,8})[ABY](?:F|D|G|H|N|M|P|Z|\d).*$", value)
        if match:
            variants.add(match.group(1))
    expanded = set(variants)
    for canonical, aliases in ALIASES.items():
        if canonical in variants:
            expanded.update(aliases)
        if variants & aliases:
            expanded.add(canonical)
    return {item.casefold() for item in expanded if item}


def levenshtein(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, char_left in enumerate(left, 1):
        current = [row]
        for column, char_right in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (char_left != char_right),
                )
            )
        previous = current
    return previous[-1]


def _record_texts(record: dict[str, object]) -> list[tuple[str, str]]:
    texts = [(str(record.get("text", "")), "primary")]
    ppocr = record.get("ppocrv6")
    if isinstance(ppocr, dict) and ppocr.get("text"):
        texts.append((str(ppocr["text"]), "ppocrv6"))
    for alternative in record.get("alternatives", []) or []:
        if isinstance(alternative, dict) and alternative.get("text"):
            texts.append((str(alternative["text"]), "alternative"))
    return texts


def candidate_score(node_name: str, text: str, source: str, category: str) -> float:
    node_variants = name_variants(node_name)
    text_variants = name_variants(text, ocr=True)
    if node_variants & text_variants:
        base = {"primary": 1.0, "ppocrv6": 0.96, "alternative": 0.92}[source]
        return base if category == "node" else base - 0.12

    best = 0.0
    for node_variant in node_variants:
        for text_variant in text_variants:
            if len(node_variant) < 2 or len(text_variant) < 2:
                continue
            if node_variant in text_variant or text_variant in node_variant:
                coverage = min(len(node_variant), len(text_variant)) / max(len(node_variant), len(text_variant))
                best = max(best, 0.62 + coverage * 0.18)
            distance = levenshtein(node_variant, text_variant)
            if distance == 1 and max(len(node_variant), len(text_variant)) >= 2:
                best = max(best, 0.68)
    if category != "node":
        best -= 0.08
    return max(0.0, best)


def match_text_candidates(node_name: str, records: list[dict[str, object]]) -> list[TextCandidate]:
    best_by_record: dict[str, TextCandidate] = {}
    for record in records:
        if record.get("status") in {"ignored", "omission"}:
            continue
        category = str(record.get("category", ""))
        for text, source in _record_texts(record):
            score = candidate_score(node_name, text, source, category)
            if score < 0.67:
                continue
            record_id = str(record.get("id", ""))
            candidate = TextCandidate(record, text, source, round(score, 3))
            if record_id not in best_by_record or candidate.score > best_by_record[record_id].score:
                best_by_record[record_id] = candidate
    return sorted(
        best_by_record.values(),
        key=lambda item: (
            -item.score,
            -float(item.record.get("confidence", 0) or 0),
            str(item.record.get("id", "")),
        ),
    )


def rectangle_distance(x: float, y: float, box: dict[str, float]) -> float:
    dx = max(float(box["x"]) - x, 0.0, x - (float(box["x"]) + float(box["width"])))
    dy = max(float(box["y"]) - y, 0.0, y - (float(box["y"]) + float(box["height"])))
    return math.hypot(dx, dy)


def detect_circle_candidates(image, box: dict[str, float], cv2) -> list[dict[str, float]]:
    import numpy as np

    height, width = image.shape[:2]
    padding = 190
    left = max(0, int(float(box["x"]) - padding))
    top = max(0, int(float(box["y"]) - padding))
    right = min(width, int(float(box["x"]) + float(box["width"]) + padding))
    bottom = min(height, int(float(box["y"]) + float(box["height"]) + padding))
    crop = image[top:bottom, left:right]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=14,
        param1=110,
        param2=24,
        minRadius=22,
        maxRadius=45,
    )
    if circles is None:
        return []
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    results = []
    for local_x, local_y, radius in circles[0]:
        x, y, radius = float(local_x + left), float(local_y + top), float(radius)
        if (
            float(box["x"]) - 4 <= x <= float(box["x"]) + float(box["width"]) + 4
            and float(box["y"]) - 4 <= y <= float(box["y"]) + float(box["height"]) + 4
        ):
            continue
        distance = rectangle_distance(x, y, box)
        if distance > 185:
            continue
        cx, cy = int(round(local_x)), int(round(local_y))
        outer = max(3, int(round(radius * 1.12)))
        y0, y1 = max(0, cy - outer), min(hsv.shape[0], cy + outer + 1)
        x0, x1 = max(0, cx - outer), min(hsv.shape[1], cx + outer + 1)
        patch = hsv[y0:y1, x0:x1, 1]
        yy, xx = np.ogrid[y0 - cy : y1 - cy, x0 - cx : x1 - cx]
        distance_from_center = np.sqrt(xx * xx + yy * yy)
        annulus = (distance_from_center >= radius * 0.62) & (distance_from_center <= radius * 1.12)
        saturation = float(patch[annulus].mean()) if patch.size and annulus.any() else 0.0
        radius_fit = 1.0 - min(abs(radius - 30.0) / 16.0, 1.0)
        score = 0.35 * math.exp(-distance / 80.0) + 0.35 * min(saturation / 150.0, 1.0) + 0.30 * radius_fit
        results.append(
            {
                "x": round(x, 2),
                "y": round(y, 2),
                "radius": round(radius, 2),
                "label_distance": round(distance, 2),
                "ring_saturation": round(saturation, 2),
                "score": round(score, 3),
            }
        )
    results.sort(key=lambda item: (-item["score"], item["label_distance"]))
    return results[:8]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_candidates(topology_path: Path, spotmap_data_path: Path, image_path: Path) -> dict[str, object]:
    try:
        import cv2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少 OpenCV。请在当前 Python 环境安装 opencv-python 后重新运行。"
        ) from exc

    dataset = json.loads(spotmap_data_path.read_text(encoding="utf-8"))
    records = dataset.get("records", [])
    edges, duplicates = parse_topology_edges_light(topology_path)
    node_names = sorted({left for _, left, _ in edges} | {right for _, _, right in edges})
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"无法读取接线图: {image_path}")
    image_height, image_width = image.shape[:2]

    nodes = []
    for node_name in node_names:
        text_candidates = match_text_candidates(node_name, records)
        serialized = []
        for candidate in text_candidates[:6]:
            box = candidate.record.get("box")
            circles = detect_circle_candidates(image, box, cv2) if isinstance(box, dict) else []
            serialized.append(
                {
                    "record_id": candidate.record.get("id"),
                    "record_text": candidate.record.get("text"),
                    "matched_text": candidate.matched_text,
                    "match_source": candidate.source,
                    "match_score": candidate.score,
                    "ocr_confidence": candidate.record.get("confidence"),
                    "category": candidate.record.get("category"),
                    "status": candidate.record.get("status"),
                    "box": box,
                    "circle_candidates": circles,
                }
            )
        unique_high = len([item for item in serialized if item["match_score"] >= 0.9]) == 1
        circles = serialized[0]["circle_candidates"] if serialized else []
        top_circle = circles[0] if circles else None
        circle_margin = top_circle["score"] - circles[1]["score"] if len(circles) > 1 else (top_circle["score"] if top_circle else 0)
        auto_ready = bool(
            unique_high
            and top_circle
            and top_circle["score"] >= 0.60
            and top_circle["radius"] >= 26.0
            and circle_margin >= 0.04
        )
        nodes.append(
            {
                "name": node_name,
                "text_candidates": serialized,
                "suggested_anchor": {"x": top_circle["x"], "y": top_circle["y"]} if auto_ready else None,
                "mapping_status": "candidate" if auto_ready else "review",
                "circle_score_margin": round(circle_margin, 3),
                "review_reason": None if auto_ready else (
                    "未找到可靠文字" if not serialized else "文字或圆点候选需要人工确认"
                ),
            }
        )

    return {
        "version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "topology": {
            "path": str(topology_path),
            "nodes": len(node_names),
            "unique_edges": len(edges),
            "duplicate_edges": [list(item) for item in duplicates],
        },
        "image": {
            "path": str(image_path),
            "width": image_width,
            "height": image_height,
            "sha256": file_sha256(image_path),
        },
        "spotmap": {
            "path": str(spotmap_data_path),
            "generated_at": dataset.get("generatedAt"),
            "image": dataset.get("image"),
        },
        "nodes": nodes,
    }


def draw_preview(payload: dict[str, object], image_path: Path, preview_path: Path) -> None:
    import cv2

    image = cv2.imread(str(image_path))
    for node in payload["nodes"]:
        anchor = node.get("suggested_anchor")
        if not anchor:
            continue
        center = (int(round(anchor["x"])), int(round(anchor["y"])))
        cv2.circle(image, center, 20, (0, 255, 255), 4, cv2.LINE_AA)
        cv2.putText(
            image,
            str(node["name"]),
            (center[0] + 24, center[1] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 80, 255),
            2,
            cv2.LINE_AA,
        )
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(preview_path), image)


def draw_contact_sheet(payload: dict[str, object], image_path: Path, output_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    source = Image.open(image_path).convert("RGB")
    nodes = [node for node in payload["nodes"] if node.get("suggested_anchor")]
    cell_width, cell_height, columns = 360, 250, 4
    rows = max(1, math.ceil(len(nodes) / columns))
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#0b1420")
    font_path = Path("/System/Library/Fonts/PingFang.ttc")
    font = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default()
    small_font = ImageFont.truetype(str(font_path), 13) if font_path.exists() else ImageFont.load_default()

    for index, node in enumerate(nodes):
        text_candidate = node["text_candidates"][0]
        box = text_candidate["box"]
        anchor = node["suggested_anchor"]
        center_x = (float(box["x"]) + float(box["width"]) / 2 + float(anchor["x"])) / 2
        center_y = (float(box["y"]) + float(box["height"]) / 2 + float(anchor["y"])) / 2
        crop_width, crop_height = 330, 190
        left = max(0, min(source.width - crop_width, int(center_x - crop_width / 2)))
        top = max(0, min(source.height - crop_height, int(center_y - crop_height / 2)))
        crop = source.crop((left, top, left + crop_width, top + crop_height))
        draw = ImageDraw.Draw(crop)
        bx0, by0 = float(box["x"]) - left, float(box["y"]) - top
        bx1, by1 = bx0 + float(box["width"]), by0 + float(box["height"])
        draw.rectangle((bx0, by0, bx1, by1), outline="#ff4d6d", width=3)
        ax, ay = float(anchor["x"]) - left, float(anchor["y"]) - top
        draw.ellipse((ax - 14, ay - 14, ax + 14, ay + 14), outline="#ffe330", width=4)

        column, row = index % columns, index // columns
        x, y = column * cell_width + 15, row * cell_height + 50
        sheet.paste(crop, (x, y))
        sheet_draw = ImageDraw.Draw(sheet)
        sheet_draw.text((column * cell_width + 14, row * cell_height + 8), str(node["name"]), fill="#e9f3ff", font=font)
        sheet_draw.text(
            (column * cell_width + 14, row * cell_height + 29),
            f"OCR:{text_candidate['record_text']}  match:{text_candidate['match_score']:.2f}  margin:{node['circle_score_margin']:.2f}",
            fill="#91a7be",
            font=small_font,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="把500kV拓扑规范名匹配到SpotMap文字及接线图圆点候选。")
    parser.add_argument("--topology", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument("--spotmap-data", type=Path, default=DEFAULT_SPOTMAP_DATA)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview", type=Path, default=DEFAULT_PREVIEW)
    parser.add_argument("--contact-sheet", type=Path, default=DEFAULT_CONTACT_SHEET)
    args = parser.parse_args()

    payload = build_candidates(args.topology, args.spotmap_data, args.image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    draw_preview(payload, args.image, args.preview)
    draw_contact_sheet(payload, args.image, args.contact_sheet)

    candidate_count = sum(node["mapping_status"] == "candidate" for node in payload["nodes"])
    print(f"节点总数: {len(payload['nodes'])}")
    print(f"可自动候选: {candidate_count}")
    print(f"待人工复核: {len(payload['nodes']) - candidate_count}")
    print(f"候选数据: {args.output}")
    print(f"叠加预览: {args.preview}")
    print(f"候选核对表: {args.contact_sheet}")


if __name__ == "__main__":
    main()
