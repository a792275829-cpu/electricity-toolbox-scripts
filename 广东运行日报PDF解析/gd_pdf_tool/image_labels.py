from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path


def recognize_image_labels(image_path: Path) -> list[dict]:
    """Use macOS Vision locally; no extra packages or developer tools needed."""
    helper = Path(__file__).with_name("recognize_image_labels.js")
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", str(helper), str(image_path)],
            capture_output=True, text=True, check=True, timeout=90,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"macOS 图片标签识别失败：{detail.strip()}") from exc


def merge_image_labels(table, labels: list[dict], width: float, height: float) -> list[list]:
    """Match image labels to detected cells without replacing native PDF values."""
    values = table.extract()
    for row_index, row in enumerate(table.rows):
        for column_index, cell in enumerate(row.cells):
            if cell is None:
                continue
            original = values[row_index][column_index]
            # Nonempty cells, including every capacity value, stay untouched.
            if original and original.strip():
                continue
            matched = []
            for label in labels:
                if not re.search(r"[\u4e00-\u9fff]", label["text"]):
                    continue
                origin, size = label["box"]["origin"], label["box"]["size"]
                center_x = (origin["x"] + size["width"] / 2) * width
                center_y = (1 - origin["y"] - size["height"] / 2) * height
                if cell[0] <= center_x < cell[2] and cell[1] <= center_y < cell[3]:
                    matched.append((center_y, center_x, label["text"]))
            if matched:
                values[row_index][column_index] = "\n".join(item[2] for item in sorted(matched))
    return values


def extract_image_label_tables(page) -> list[list[list]]:
    """Recover rasterized headings only on pages with declaration-like tables."""
    if not page.images:
        return []
    tables = [
        table for table in page.find_tables()
        if any(
            sum(bool(re.search(r"\d.*[%％]", cell or "")) for cell in row) >= 4
            for row in table.extract()
        )
    ]
    if not tables:
        return []
    with tempfile.TemporaryDirectory(prefix="daily-pdf-labels-") as folder:
        image_path = Path(folder) / "page.png"
        image = page.to_image(resolution=180).original
        try:
            image.save(image_path)
        finally:
            image.close()
        labels = recognize_image_labels(image_path)
    return [merge_image_labels(table, labels, page.width, page.height) for table in tables]
