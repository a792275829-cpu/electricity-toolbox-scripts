"""Shared OCR name matching and circle candidates for diagram annotation."""
from __future__ import annotations
import math
import re
import unicodedata
from dataclasses import dataclass

FACILITY_SUFFIXES = (
    "海风场陆上站",
    "陆上集控站",
    "陆上集控",
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


def clean_text(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"500\s*kV.*$", "", value, flags=re.IGNORECASE)
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value)


def name_variants(text: object, *, ocr: bool = False) -> set[str]:
    value = clean_text(text)
    variants = {value}
    # Workbook normalization can leave “光伏电” while a wrapped map label
    # ends its first line at “光伏”. Retain the facility type in the alias.
    solar = re.fullmatch(r"(.{2,})光伏(?:电站|电)?", value)
    if solar:
        variants.add(solar.group(1) + "光伏")
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
        buses = re.fullmatch(r"([\u4e00-\u9fff]{2,8})[ABY]{1,2}", value)
        if buses:
            variants.add(buses.group(1))
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


def refine_circle(gray, x, y, radius, cv2):
    """Fit angularly distributed radial edges; reject line intersections and drift."""
    import numpy as np

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    angles = np.linspace(0, 2*np.pi, 96, endpoint=False)
    evidence = {1: ([], []), -1: ([], [])}
    for i, angle in enumerate(angles):
        dx, dy = np.cos(angle), np.sin(angle)
        rr = np.arange(max(2, radius-5), radius+6, .5)
        xx, yy = np.rint(x+rr*dx).astype(int), np.rint(y+rr*dy).astype(int)
        valid = (xx >= 0) & (xx < gray.shape[1]) & (yy >= 0) & (yy < gray.shape[0])
        xx, yy, rr = xx[valid], yy[valid], rr[valid]
        if not len(xx):
            continue
        ax, ay = gx[yy, xx], gy[yy, xx]
        norm = np.hypot(ax, ay)
        radial = ax*dx+ay*dy
        # Inner and outer rings can have opposite contrast. Fit each polarity
        # separately so that their edges never bias one mixed least-squares fit.
        for polarity, (points, sectors) in evidence.items():
            aligned = (norm > 80) & (polarity*radial > .85*norm)
            if not aligned.any():
                continue
            score = np.where(aligned, polarity*radial/(1+abs(rr-radius)), -1)
            best = score.argmax()
            points.append((xx[best], yy[best])); sectors.append(i)
    fits = []
    best_support = 0.0
    for points, sectors in evidence.values():
        best_support = max(best_support, len(points)/96)
        if len(points) < 48:
            continue
        points = np.asarray(points, dtype=float)
        keep = np.ones(len(points), dtype=bool)
        for _ in range(4):
            selected = points[keep]
            if len(selected) < 40:
                break
            a = np.column_stack((2*selected[:, 0], 2*selected[:, 1], np.ones(len(selected))))
            cx, cy, c = np.linalg.lstsq(a, (selected**2).sum(axis=1), rcond=None)[0]
            fitted_radius = math.sqrt(max(0, c+cx*cx+cy*cy))
            residual = abs(np.hypot(points[:, 0]-cx, points[:, 1]-cy)-fitted_radius)
            keep = residual <= 2
        support = float(keep.sum()/96)
        quadrants = {sectors[i]//24 for i in np.flatnonzero(keep)}
        if (support >= .55 and len(quadrants) == 4 and math.hypot(cx-x, cy-y) <= 5
                and abs(fitted_radius-radius) <= 5):
            fits.append((float(cx), float(cy), fitted_radius, True, support))
    return max(fits, key=lambda f: f[4]) if fits else (x, y, radius, False, best_support)


def dark_ring_candidates(crop, cv2):
    """Find the enclosed inner edge of black station rings despite outgoing lines."""
    import numpy as np

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] < 110) & (hsv[:, :, 1] < 100)).astype(np.uint8)*255
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    found = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if len(contour) < 40 or not 400 <= area <= 12000:
            continue
        perimeter = cv2.arcLength(contour, True)
        if not perimeter or 4*math.pi*area/(perimeter*perimeter) < .75:
            continue
        (x, y), (a, b), _ = cv2.fitEllipse(contour)
        if min(a, b) < 24 or max(a, b) > 125 or min(a, b)/max(a, b) < .88:
            continue
        radius = (a+b)/4
        points = contour[:, 0, :].astype(float)
        residual = np.abs(np.hypot(points[:, 0]-x, points[:, 1]-y)-radius)
        support = float(np.mean(residual <= 1.8))
        if support >= .9:
            found.append((float(x), float(y), float(radius), support))
    return found


def ring_dark_coverage(crop, x, y, radius, cv2):
    """Angular black-ring evidence, independent of colored sectors inside a symbol."""
    import numpy as np

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    angles = np.linspace(0, 2*np.pi, 96, endpoint=False)[:, None]
    radii = np.linspace(radius, radius*1.35, 8)[None, :]
    xx = np.rint(x+np.cos(angles)*radii).astype(int)
    yy = np.rint(y+np.sin(angles)*radii).astype(int)
    valid = (xx >= 0) & (xx < crop.shape[1]) & (yy >= 0) & (yy < crop.shape[0])
    xx = np.clip(xx, 0, crop.shape[1]-1); yy = np.clip(yy, 0, crop.shape[0]-1)
    dark = valid & (hsv[yy, xx, 2] < 110) & (hsv[yy, xx, 1] < 100)
    return float(np.mean(dark.any(axis=1)))


def detect_circle_candidates(image, box: dict[str, float], cv2, *, refine=True, voltage=None) -> list[dict[str, float]]:
    import numpy as np

    height, width = image.shape[:2]
    padding = 190
    left = max(0, int(float(box["x"]) - padding))
    top = max(0, int(float(box["y"]) - padding))
    right = min(width, int(float(box["x"]) + float(box["width"]) + padding))
    bottom = min(height, int(float(box["y"]) + float(box["height"]) + padding))
    crop = image[top:bottom, left:right]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    original_gray = gray
    gray = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=14,
        param1=110,
        param2=24,
        minRadius=18 if refine else 22,
        maxRadius=60 if refine else 45,
    )
    measured = []
    if refine and voltage == 220:
        measured.extend((x, y, r, True, support, 'dark-ring-contour')
                        for x, y, r, support in dark_ring_candidates(crop, cv2))
    for x, y, radius in (() if circles is None else circles[0]):
        if refine:
            x, y, radius, refined, support = refine_circle(original_gray, float(x), float(y), float(radius), cv2)
        else:
            x, y, radius, refined, support = float(x), float(y), float(radius), False, 0.0
        measured.append((x, y, radius, refined, support, 'hough'))
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    results = []
    for local_x, local_y, radius, refined, support, source in measured:
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
        if refine:
            radius_fit = max(radius_fit, 1-min(abs(radius-45)/16, 1))
            score = (.30*math.exp(-distance/80) + .30*min(saturation/150, 1)
                     + .25*support + .15*radius_fit)
        dark_coverage = ring_dark_coverage(crop, local_x, local_y, radius, cv2) if refine else 0.0
        if refine and voltage == 220:
            # Diagram-specific candidate ranking evidence, never a voltage label.
            # Cross-voltage names still require semantic review in the caller.
            score = .5*math.exp(-distance/80) + .3*dark_coverage + .2*support
        results.append(
            {
                "x": round(x, 2),
                "y": round(y, 2),
                "radius": round(radius, 2),
                "label_distance": round(distance, 2),
                "ring_saturation": round(saturation, 2),
                "score": round(score, 3),
                "refined": refined,
                "edge_support": round(support, 3),
                "dark_ring_coverage": round(dark_coverage, 3),
                "source": source,
            }
        )
    results.sort(key=lambda item: (-int(item['refined']) if refine else 0, -item["score"], item["label_distance"]))
    unique = []
    for circle in results:
        if not any(math.hypot(circle['x']-other['x'], circle['y']-other['y']) < 12 for other in unique):
            unique.append(circle)
    return unique[:8]
