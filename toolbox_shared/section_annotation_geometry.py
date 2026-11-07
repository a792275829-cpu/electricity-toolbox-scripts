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


def split_lobe_candidates(crop, cv2):
    """Find a symmetric pair of enclosed colored lobes, not two fitted circles.

    This diagram's split symbols have two equal, convex, half-ellipse interiors.
    Require matching color, size, orientation and opposite centroid offsets.
    The returned anchor is their symmetry midpoint; no circle error is claimed.
    """
    import numpy as np

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    dark = ((hsv[:, :, 2] < 110) & (hsv[:, :, 1] < 100)).astype(np.uint8)*255
    contours, hierarchy = cv2.findContours(dark, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    lobes = []
    for i, contour in enumerate(contours):
        if hierarchy[0][i][3] < 0 or len(contour) < 30:
            continue
        area = cv2.contourArea(contour)
        if not 200 <= area <= 1500:
            continue
        _, axes, angle = cv2.fitEllipse(contour)
        minor, major = sorted(axes)
        if not (12 <= minor <= 32 and 25 <= major <= 65 and .4 <= minor/major <= .65):
            continue
        solidity = area/cv2.contourArea(cv2.convexHull(contour))
        if solidity < .94:
            continue
        mask = np.zeros(dark.shape, dtype=np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1)
        interior = mask.astype(bool)
        colored = interior & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 110)
        if colored.sum()/interior.sum() < .4:
            continue
        moments = cv2.moments(contour)
        x, y = moments['m10']/moments['m00'], moments['m01']/moments['m00']
        bx, by, w, h = cv2.boundingRect(contour)
        offset = np.array([x-bx-(w-1)/2, y-by-(h-1)/2])
        if np.linalg.norm(offset) < 1.5:
            continue
        lobes.append(dict(x=x, y=y, area=area, major=major, angle=angle,
                          solidity=solidity, offset=offset, hue=float(np.median(hsv[:, :, 0][colored])),
                          box=(bx, by, bx+w, by+h)))
    found = []
    for i, a in enumerate(lobes):
        for b in lobes[i+1:]:
            size_agreement = min(a['area'], b['area'])/max(a['area'], b['area'])
            separation = math.hypot(a['x']-b['x'], a['y']-b['y'])
            scale = (a['major']+b['major'])/2
            angle = abs(a['angle']-b['angle']) % 180
            hue = abs(a['hue']-b['hue'])
            opposition = float(np.dot(a['offset'], b['offset']) /
                               (np.linalg.norm(a['offset'])*np.linalg.norm(b['offset'])))
            if (size_agreement < .9 or not 1.0 <= separation/scale <= 1.5
                    or min(angle, 180-angle) > 10 or min(hue, 180-hue) > 5 or opposition > -.8):
                continue
            span = max(max(a['box'][2], b['box'][2])-min(a['box'][0], b['box'][0]),
                       max(a['box'][3], b['box'][3])-min(a['box'][1], b['box'][1]))
            found.append(dict(x=(a['x']+b['x'])/2, y=(a['y']+b['y'])/2,
                              radius=span/2, support=min(a['solidity'], b['solidity'], size_agreement)))
    return found


def generator_glyph_evidence(crop, x, y, radius, cv2):
    """Require a central, single, hole-free sine stroke inside an intact ring."""
    import numpy as np

    left, top = max(0, math.floor(x-radius)), max(0, math.floor(y-radius))
    view = crop[top:min(crop.shape[0], math.ceil(y+radius)+1),
                left:min(crop.shape[1], math.ceil(x+radius)+1)]
    if not view.size:
        return None
    hsv = cv2.cvtColor(view, cv2.COLOR_BGR2HSV)
    cx, cy = x-left, y-top
    yy, xx = np.ogrid[:view.shape[0], :view.shape[1]]
    mask = ((hsv[:, :, 2] < 110) & (hsv[:, :, 1] < 100)
            & ((xx-cx)**2+(yy-cy)**2 < (.75*radius)**2)).astype(np.uint8)*255
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
    components = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 5]
    if len(components) != 1:
        return None
    i = components[0]
    bx, by, w, h, area = map(int, stats[i])
    if not (1.2 <= w/radius <= 1.6 and .6 <= h/radius <= 1.05
            and w/h >= 1.4 and .22 <= area/(w*h) <= .45
            and math.hypot(centers[i][0]-cx, centers[i][1]-cy) < .15*radius):
        return None
    component = (labels == i).astype(np.uint8)*255
    _, hierarchy = cv2.findContours(component, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None or any(item[3] >= 0 for item in hierarchy[0]):
        return None
    columns = [int(round(bx+q*(w-1))) for q in (0, .25, .75, 1)]
    curve = [float(np.flatnonzero(component[:, col]).mean()) for col in columns]
    if (curve[1]-curve[2] < .35*radius
            or max(abs(curve[0]-cy), abs(curve[3]-cy)) > .3*radius):
        return None
    return dict(kind='sine-generator', stroke_fraction=round(area/(w*h), 3))


def solar_glyph_evidence(crop, x, y, radius, cv2):
    """Four similarly sized panel cells distinguish this icon from wind blades."""
    import numpy as np

    left,top=max(0,math.floor(x-radius-3)),max(0,math.floor(y-radius-3))
    view=crop[top:min(crop.shape[0],math.ceil(y+radius)+4),left:min(crop.shape[1],math.ceil(x+radius)+4)]
    if not view.size:
        return None
    hsv=cv2.cvtColor(view,cv2.COLOR_BGR2HSV)
    mask=((hsv[:,:,2]<110)&(hsv[:,:,1]<100)).astype(np.uint8)*255
    contours,hierarchy=cv2.findContours(mask,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
    cells=[]
    for i,c in enumerate(contours):
        if hierarchy[0][i][3]<0:
            continue
        area=cv2.contourArea(c); perimeter=cv2.arcLength(c,True)
        polygon=cv2.approxPolyDP(c,.04*perimeter,True)
        if not (.08*radius**2 <= area <= .3*radius**2 and 4 <= len(polygon) <= 5
                and cv2.isContourConvex(polygon)):
            continue
        m=cv2.moments(c);cx,cy=m['m10']/m['m00'],m['m01']/m['m00']
        if math.hypot(cx-(x-left),cy-(y-top)) <= .95*radius:
            cells.append(area)
    if len(cells)==4 and min(cells)/max(cells)>=.8:
        return dict(kind='solar-panel',cells=4)
    return None


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
        if refine and voltage == 220 and refined and dark_coverage >= .9:
            glyph = generator_glyph_evidence(crop, local_x, local_y, radius, cv2)
            if glyph:
                results[-1]['generator_glyph'] = glyph
            solar=solar_glyph_evidence(crop,local_x,local_y,radius,cv2)
            if solar:
                results[-1]['solar_glyph']=solar
    if refine and voltage == 220:
        for shape in split_lobe_candidates(crop, cv2):
            x, y = shape['x']+left, shape['y']+top
            distance = rectangle_distance(x, y, box)
            if distance > 185 or distance == 0:
                continue
            results.append(dict(x=round(x, 2), y=round(y, 2), radius=round(shape['radius'], 2),
                                label_distance=round(distance, 2), refined=True,
                                score=round(.5*math.exp(-distance/80)+.5*shape['support'], 3),
                                source='paired-lobe-contours', symbol_kind='split-lobes',
                                symbol_support=round(shape['support'], 3)))
    results.sort(key=lambda item: (-int(item['refined']) if refine else 0, -item["score"], item["label_distance"]))
    unique = []
    for circle in results:
        if not any(math.hypot(circle['x']-other['x'], circle['y']-other['y']) < 12 for other in unique):
            unique.append(circle)
    return unique[:8]
