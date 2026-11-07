"""Non-destructive, tiled station prelabels; candidates never become analysis anchors."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import math
import os
import re
from pathlib import Path
import subprocess
import tempfile
import warnings

from PIL import Image, ImageOps
from toolbox_shared import section_basemap as maps, section_topology as model
from toolbox_shared.section_annotation_geometry import match_text_candidates, detect_circle_candidates, name_variants, levenshtein, rectangle_distance
from toolbox_shared.storage import WORKSPACE_ROOT, data_path


def tiles(width, height, size=1500, overlap=300):
    """Integer origins, full resolution, overlapping right/bottom edge tiles."""
    def starts(length):
        return sorted(set([*range(0, max(1, length-size+1), size-overlap), max(0, length-size)]))
    return [(x, y, min(x+size, width), min(y+size, height)) for y in starts(height) for x in starts(width)]


def deduplicate(records):
    result = []
    for r in sorted(records, key=lambda r: -r['confidence']):
        b = r['box']; x, y = b['x']+b['width']/2, b['y']+b['height']/2
        if any(r['text'] == o['text'] and math.hypot(x-o['box']['x']-o['box']['width']/2, y-o['box']['y']-o['box']['height']/2) < 30 for o in result):
            continue
        result.append(r)
    return result


OCR_RECIPE = 'native1500-retry2x-native-label-proposals-v5'
UPGRADABLE_OCR_RECIPES = {'native1500-baseline', 'native1500-retry2x-v2', 'native1500-retry2x-native-proposals-v3', 'native1500-retry2x-native-token-proposals-v4'}
ALGORITHM_VERSION = 'station-ownership-label-tokens-partial-v9'


def competing_labels(chosen, record, station, labels):
    """Comparable nearby station labels make automatic circle ownership unsafe.

    This is an abstention signal, not a voltage or nearest-label classifier.
    Never switch to a lower-ranked circle after finding competing evidence.
    """
    own_distance = rectangle_distance(chosen['x'], chosen['y'], record['box'])
    tolerance = chosen.get('radius', 30) * .25
    competitors = []
    for name, other in labels:
        if name == station or name_variants(name) & name_variants(station):
            continue
        if other['id'] == record['id']:
            continue
        distance = rectangle_distance(chosen['x'], chosen['y'], other['box'])
        if distance <= own_distance + tolerance:
            competitors.append(dict(station=name, record_id=other['id'], distance=round(distance, 2)))
    return sorted(competitors, key=lambda item: (item['distance'], item['station'], item['record_id']))


def partial_symbol_conflicts(chosen, circles):
    """A strong nearby partial ring contradicts assignment to a distant full ring.

    Partial fits lack a trustworthy center. Preserve them as semantic-review
    evidence, rather than silently replacing the station with a neighbor.
    """
    if not chosen.get('refined') or 'label_distance' not in chosen:
        return []
    return [c for c in circles if c.get('refined') is False
            and c.get('edge_support', 0) >= .75 and c.get('dark_ring_coverage', 0) >= .7
            and c.get('label_distance', float('inf')) + chosen['radius'] < chosen['label_distance']]


def optional_runtime():
    configured = os.environ.get('TOPOLOGY_OCR_PYTHON')
    if configured:
        return Path(configured).expanduser()
    local = WORKSPACE_ROOT/'500kV断面分析/local_data/模型环境/ppocr/bin/python'
    return next((path for path in (data_path(local), local) if path.is_file()), None)


def external_recognize(image, station_names, enhanced, base_records=None):
    """Run the same implementation in an explicitly configured optional environment."""
    runtime = optional_runtime()
    if runtime is None or not runtime.is_file():
        raise ValueError('TOPOLOGY_OCR_PYTHON 指向的解释器不存在')
    with tempfile.TemporaryDirectory(prefix='topology-ppocr-') as folder:
        root = Path(folder)
        image.save(root/'image.png')
        request = dict(image=str(root/'image.png'), station_names=list(station_names), enhanced=enhanced, base_records=base_records)
        (root/'request.json').write_text(json.dumps(request, ensure_ascii=False))
        env = dict(os.environ); env.pop('TOPOLOGY_OCR_PYTHON', None)
        # Source-debug launchers may set these for their bundled interpreter.
        # The optional environment must load its own standard library/dependencies.
        env.pop('PYTHONHOME', None); env.pop('PYTHONPATH', None)
        try:
            subprocess.run([str(runtime), '-m', 'toolbox_shared.section_prelabel',
                            str(root/'request.json'), str(root/'result.json')], cwd=WORKSPACE_ROOT,
                           env=env, check=True, capture_output=True, timeout=1800)
        except subprocess.CalledProcessError as exc:
            raise ValueError(f'独立PP-OCRv6环境执行失败（退出码 {exc.returncode}），未更新草稿；请用该解释器运行小样命令检查首条异常') from None
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('独立PP-OCRv6环境无法启动或运行超时，未更新草稿') from None
        return json.loads((root/'result.json').read_text())


def vocabulary_hits(text, station_names):
    variants = name_variants(text, ocr=True)
    return {name for name in station_names if variants & name_variants(name)}


def retry_difficult(image, records, station_names, read_crop, limit=120):
    """Retain raw OCR; alternatives are evidence, never forced vocabulary corrections."""
    names = sorted(set(station_names))
    variants = {v for name in names for v in name_variants(name)}
    retried = 0
    for record in records:
        text = record['text']
        hits = vocabulary_hits(text, names)
        near = not hits and any(len(v) >= 3 and len(w) >= 3 and abs(len(v)-len(w)) <= 1
                                and levenshtein(v, w) == 1
                                for v in name_variants(text, ocr=True) for w in variants)
        if not (near or hits and record['confidence'] < .92) or retried >= limit:
            continue
        b = record['box']
        bounds = (max(0, int(b['x'])-6), max(0, int(b['y'])-6),
                  min(image.width, math.ceil(b['x']+b['width'])+6),
                  min(image.height, math.ceil(b['y']+b['height'])+6))
        crop = image.crop(bounds)
        crop = crop.resize((crop.width*2, crop.height*2), Image.Resampling.LANCZOS)
        alternatives = []
        for treatment, view in (('color2x', crop), ('gray2x', ImageOps.autocontrast(ImageOps.grayscale(crop)).convert('RGB'))):
            for value, score in read_crop(view):
                if value:
                    alternatives.append(dict(text=value, confidence=float(score), treatment=treatment))
        record['alternatives'] = alternatives
        evidence = [hits] + [vocabulary_hits(a['text'], names) for a in alternatives]
        all_hits = set().union(*evidence)
        record['ocr_conflict'] = len(all_hits) > 1
        record['ocr_retried'] = True
        # A correction must be independently reproduced by both treatments.
        agreed = set.intersection(*(set().union(*(vocabulary_hits(a['text'], names)
                  for a in alternatives if a['treatment'] == t and a['confidence'] >= .9))
                  for t in ('color2x', 'gray2x')))
        record['retry_agreed'] = len(agreed) == 1 and not record['ocr_conflict']
        retried += 1
    return records


def recover_text_proposals(image, records, station_names, proposals, read_crop, limit=120, *, proposal_engine='macos-vision'):
    """Use secondary boxes only after PP-OCR independently reads the exact name twice."""
    names = sorted(set(station_names))
    found = set()
    for r in records:
        if (r.get('ocr_conflict') or r.get('status') in ('ignored', 'omission')
                or r.get('ocr_retried') and not r.get('retry_agreed')
                or r.get('confidence', 0) < .8):
            continue
        found.update(vocabulary_hits(r['text'], names))
        if r.get('retry_agreed'):
            for a in r.get('alternatives', []):
                found.update(vocabulary_hits(a['text'], names))
    missing = set(names)-found
    added = []
    attempts = 0
    for proposal in sorted(proposals, key=lambda p: -p['confidence']):
        hits = vocabulary_hits(proposal['text'], names)
        if proposal['confidence'] < .9 or len(hits) != 1 or not hits & missing:
            continue
        if attempts >= limit:
            break
        attempts += 1
        b = proposal['box']
        bounds = (max(0, int(b['x'])-6), max(0, int(b['y'])-6),
                  min(image.width, math.ceil(b['x']+b['width'])+6),
                  min(image.height, math.ceil(b['y']+b['height'])+6))
        crop = image.crop(bounds)
        if not crop.width or not crop.height:
            continue
        crop = crop.resize((crop.width*2, crop.height*2), Image.Resampling.LANCZOS)
        alternatives = []
        votes = []
        for treatment, view in (('color2x', crop), ('gray2x', ImageOps.autocontrast(ImageOps.grayscale(crop)).convert('RGB'))):
            accepted = {}
            for value, score in read_crop(view):
                alternatives.append(dict(text=value, confidence=float(score), treatment=treatment))
                if score >= .9 and vocabulary_hits(value, names) == hits:
                    accepted[value] = float(score)
            votes.append(accepted)
        agreed = set(votes[0]) & set(votes[1])
        if len(agreed) != 1:
            continue
        text = agreed.pop()
        added.append(dict(text=text, confidence=min(v[text] for v in votes), box=deepcopy(b),
                          category='node', ocr_recovered=True, proposal_engine=proposal_engine,
                          proposal_text=proposal['text'], source_record_id=proposal.get('source_record_id'), alternatives=alternatives,
                          proposal_source=deepcopy(proposal.get('proposal_source')),
                          ocr_retried=True, retry_agreed=True, ocr_conflict=False))
    return records+added


def merged_label_proposals(records, station_names):
    """Propose crops at explicit token boundaries, never a fuzzy name edit.

    A merged box may contain numeric bus graphics or adjacent labels. Estimated
    character widths propose a region only; independent image reads must confirm
    its text before it can participate in name matching or circle association.
    """
    proposals = []
    index = {}
    for name in station_names:
        for variant in name_variants(name):
            index.setdefault(variant, set()).add(name)
    def hits(value):
        return set().union(*(index.get(v, set()) for v in name_variants(value, ocr=True)))
    for record in records:
        raw = record['text']
        if record.get('confidence', 0) < .9 or record.get('status') in ('ignored', 'omission'):
            continue
        spans = {(m.start(), m.end(), m.group(), 'bus')
                 for m in re.finditer(r'[\u4e00-\u9fff]{2,8}[ABY]{1,2}', raw)}
        spans.update((m.start(), m.end(), m.group(), 'numeric')
                     for m in re.finditer(r'[\u4e00-\u9fff]{2,8}(?=\d)', raw))
        # Split a Chinese-only merged box only when both halves independently
        # identify catalog stations and the whole is not an established name.
        if re.fullmatch(r'[\u4e00-\u9fff]{4,16}', raw) and not hits(raw):
            for split in range(2, len(raw)-1):
                if len(hits(raw[:split])) == len(hits(raw[split:])) == 1:
                    spans.update(((0,split,raw[:split],'paired-names'),
                                  (split,len(raw),raw[split:],'paired-names')))
        for begin, finish, token, kind in sorted(spans):
            if len(hits(token)) != 1 or (begin, finish) == (0, len(raw)):
                continue
            b = record['box']
            weights = [1.0 if '\u4e00' <= c <= '\u9fff' else .6 for c in raw]
            total = sum(weights)
            start = sum(weights[:begin])/total
            end = sum(weights[:finish])/total
            # Two treatments of the source pixels must agree; the estimated
            # token box itself is never a station coordinate.
            box = dict(x=b['x']+b['width']*start, y=b['y'],
                       width=b['width']*(end-start), height=b['height'])
            boxes = [box]
            if kind == 'numeric' and b['height'] > b['width']/total*1.25:
                narrow = dict(box, height=b['width']/total*1.25)
                boxes.insert(0, narrow)
            for proposed in boxes:
                proposals.append(dict(text=token, confidence=record['confidence'], box=proposed,
                                      source_record_id=record.get('id'),
                                      proposal_source=dict(id=record.get('id'), text=raw, box=deepcopy(b), kind=kind)))
    return proposals


def recover_merged_labels(image, records, station_names, read_crop):
    proposals = merged_label_proposals(records, station_names)
    return recover_text_proposals(image, records, station_names, proposals, read_crop,
                                  limit=120, proposal_engine='ppocr-label-token')


def supplement_native_boxes(image, records, station_names, read_crop):
    try:
        proposals = recognize(image, 'macos-vision', enhanced=False)
    except ValueError:
        warnings.warn('本机文字框补检不可用，保留PP-OCRv6原结果；需要macOS Command Line Tools', RuntimeWarning)
        return records
    return recover_text_proposals(image, records, station_names, proposals, read_crop)


def recognize(image: Image.Image, engine='macos-vision', *, station_names=(), enhanced=True, base_records=None):
    """OCR original-resolution tiles. macOS Vision is an explicitly named baseline."""
    records = [] if base_records is None else deepcopy(base_records)
    paddle = None
    if engine == 'ppocrv6':
        try:
            from paddleocr import PaddleOCR
            paddle = PaddleOCR(text_detection_model_name='PP-OCRv6_medium_det',
                               text_recognition_model_name='PP-OCRv6_medium_rec',
                               use_doc_orientation_classify=False, use_doc_unwarping=False,
                               use_textline_orientation=False,
                               text_det_limit_type='max', text_det_limit_side_len=1500,
                               cpu_threads=4)
        except ImportError as exc:
            if optional_runtime() is not None:
                return external_recognize(image, station_names, enhanced, base_records)
            raise ValueError('未安装可选 PP-OCRv6；请在独立环境安装 PaddleOCR 与 PaddlePaddle，或选择本机 OCR 基线') from exc
    elif engine != 'macos-vision':
        raise ValueError('未知 OCR 引擎')
    with tempfile.TemporaryDirectory(prefix='topology-ocr-') as tmp:
        root = Path(tmp); binary = root/'vision'
        if paddle is None:
            try:
                subprocess.run(['xcrun', 'swiftc', str(WORKSPACE_ROOT/'500kV断面分析/tools/layer_review_vision.swift'), '-o', str(binary)], check=True, capture_output=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError('本机 OCR 编译失败，需要 macOS Command Line Tools；可改用独立 PP-OCRv6 环境') from exc
        for i, bounds in enumerate(tiles(*image.size) if base_records is None else []):
            tile = image.crop(bounds).convert('RGB'); path = root/'tile.png'; tile.save(path)
            left, top = bounds[:2]
            if paddle is None:
                try:
                    output = subprocess.run([str(binary), str(path)], check=True, capture_output=True, timeout=90)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError(f'本机 OCR 图块 {i+1} 识别失败') from exc
                for item in json.loads(output.stdout):
                    records.append(dict(text=item['text'], confidence=item['confidence'], category='node',
                                        box=dict(x=left+item['x']*tile.width, y=top+(1-item['y']-item['h'])*tile.height,
                                                 width=item['w']*tile.width, height=item['h']*tile.height)))
            else:
                for prediction in paddle.predict(str(path)):
                    content = prediction.json['res']
                    for text, score, box in zip(content['rec_texts'], content['rec_scores'], content['rec_boxes']):
                        x0, y0, x1, y1 = map(float, box)
                        records.append(dict(text=text, confidence=float(score), category='node', box=dict(x=left+x0, y=top+y0, width=x1-x0, height=y1-y0)))
        if enhanced and station_names:
            if paddle is not None:
                from paddleocr import TextRecognition
                recognizer = TextRecognition(model_name='PP-OCRv6_medium_rec', cpu_threads=4)
                def read_crop(view):
                    import numpy as np
                    return [(p.json['res']['rec_text'], p.json['res']['rec_score'])
                            for p in recognizer.predict(np.array(view))]
            else:
                def read_crop(view):
                    path = root/'retry.png'; view.save(path)
                    output = subprocess.run([str(binary), str(path)], check=True, capture_output=True, timeout=90)
                    return [(r['text'], r['confidence']) for r in json.loads(output.stdout)]
            records = retry_difficult(image, deduplicate(records), station_names, read_crop)
            if paddle is not None:
                records = supplement_native_boxes(image, records, station_names, read_crop)
                records = recover_merged_labels(image, records, station_names, read_crop)
    for i, r in enumerate(records):
        r['id'] = str(i)
    return deduplicate(records)


def sample_groups(groups, matches, limit):
    """Round-robin density strata plus unresolved names, no anchor ground truth used."""
    strata = []
    for voltage in sorted({g[1] for g in groups}):
        found, missing = [], []
        for group in (g for g in groups if g[1] == voltage):
            candidates = matches[group]
            if not candidates:
                missing.append(group); continue
            box = candidates[0].record['box']
            x, y = box['x'], box['y']
            density = sum(bool(matches[g]) and math.hypot(matches[g][0].record['box']['x']-x, matches[g][0].record['box']['y']-y) < 400 for g in groups)
            found.append((density, group))
        found.sort(key=lambda pair: (pair[0], str(pair[1])))
        buckets = [[], [], [], sorted(missing, key=str)]
        for i, (_, group) in enumerate(found):
            buckets[min(2, i*3//max(1, len(found)))].append(group)
        strata.extend(buckets)
    result = []
    while len(result) < min(limit, len(groups)):
        for bucket in strata:
            if bucket and len(result) < limit:
                # Spread observations throughout each stratum deterministically.
                result.append(bucket.pop(len(bucket)//2))
    return result


def prelabel(data, *, limit=30, engine='macos-vision', records=None, reviewer=None, sample=None, enhanced=True):
    model.validate_catalog(data)
    if not data.get('base_map'):
        raise ValueError('请先导入底图')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError('小样数量须为1至30站；完成复核后再决定全图方案')
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ValueError('缺少可选圆点定位依赖；请在源码 .venv 安装 opencv-python-headless') from exc
    image = Image.open(maps.asset_path(data['base_map']['id'])).convert('RGB')
    if image.size != (data['base_map']['width'], data['base_map']['height']):
        raise ValueError('底图尺寸不一致')
    groups = {}
    protected = {(n['station'], n['voltage']) for n in data['nodes']
                 if n.get('map_anchor') or n.get('map_candidate')}
    for n in data['nodes']:
        if n['present'] and n['voltage'] in (500, 220) and (n['station'], n['voltage']) not in protected:
            groups.setdefault((n['station'], n['voltage']), []).append(n['id'])
    if sample is not None:
        if not sample or len(sample) > limit or len(set(sample)) != len(sample) or any(g not in groups for g in sample):
            raise ValueError('固定小样须为1至30个不重复、未标注的现有站点及电压')
    records = recognize(image, engine, station_names=[g[0] for g in groups], enhanced=enhanced) if records is None else records
    # Index established name variants once. Uncertain OCR stays unresolved;
    # fuzzy all-pairs matching is both slow and unsafe for short Chinese names.
    by_name = {}
    for r in records:
        values = [r['text']] + [a['text'] for a in r.get('alternatives', [])
                              if r.get('retry_agreed') and a.get('confidence', 0) >= .9]
        for value in values:
            for variant in name_variants(value, ocr=True):
                by_name.setdefault(variant, {})[r['id']] = r
    matches = {}
    for g in groups:
        exact = {}
        for variant in name_variants(g[0]):
            exact.update(by_name.get(variant, {}))
        matches[g] = match_text_candidates(g[0], list(exact.values()))

    # Include protected and unsampled stations as competing evidence too.
    labels = []
    for station in sorted({n['station'] for n in data['nodes'] if n['present']}):
        exact = {}
        for variant in name_variants(station):
            exact.update(by_name.get(variant, {}))
        for candidate in match_text_candidates(station, list(exact.values())):
            r = candidate.record
            if (candidate.score >= .9 and not r.get('ocr_conflict')
                    and (not r.get('ocr_retried') or r.get('retry_agreed'))
                    and float(r.get('confidence', 0)) >= .8):
                labels.append((station, r))
    selected = list(sample) if sample is not None else sample_groups(list(groups), matches, limit)
    pixels = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    label_records = {r['id']: r for _, r in labels}
    ownership_circles = {}
    result = deepcopy(data); report = []
    for group in selected:
        texts = matches[group]
        row = dict(station=group[0], voltage=group[1], text=None, point=None, reason='未匹配站名', vision_status='未配置')
        if texts:
            text = texts[0]; row.update(text=text.matched_text, match_score=text.score, box=text.record['box'], reason='未找到圆点')
            circles = detect_circle_candidates(pixels, text.record['box'], cv2, refine=enhanced, voltage=group[1])
            # A repeated name at different locations or voltage groups is ambiguous.
            ambiguous = sum(t.score >= .9 for t in texts) > 1 or sum(g[0] == group[0] for g in groups) > 1
            conflict = text.record.get('ocr_conflict', False)
            row['ocr_retried'] = text.record.get('ocr_retried', False)
            row['ocr_conflict'] = conflict
            row['alternatives'] = text.record.get('alternatives', [])
            if conflict:
                row['reason'] = '多次OCR站名冲突，需人工核对'
                report.append(row)
                continue
            if enhanced and ((text.record.get('ocr_retried') and not text.record.get('retry_agreed'))
                             or float(text.record.get('confidence', 0)) < .8 and not text.record.get('retry_agreed')):
                row['reason'] = '站名置信不足或复识别未一致，需人工核对'
                report.append(row)
                continue
            if (circles or reviewer is not None and ambiguous) and text.score >= .9 and (not ambiguous or reviewer is not None):
                chosen = circles[0] if circles else None; row['reason'] = '待人工核对'
                if enhanced and reviewer is None:
                    margin = circles[0]['score']-circles[1]['score'] if len(circles) > 1 else circles[0]['score']
                    # Sparse ring evidence or close alternatives require semantic review.
                    # Preserve legacy injected records used by callers without fit metadata.
                    if margin < .04 or ('refined' in chosen and not chosen['refined']):
                        chosen = None
                        row['reason'] = '圆周证据不足或多个圆点接近，需视觉或人工核对'
                if reviewer is not None:
                    chosen = reviewer(image, group, text.record, circles) if circles else None
                    if ambiguous:
                        accepted = [(chosen, text, circles)] if chosen else []
                        for other in texts[1:4]:
                            if other.score < .9 or other.record.get('ocr_conflict'):
                                continue
                            alternatives = detect_circle_candidates(pixels, other.record['box'], cv2, refine=enhanced, voltage=group[1])
                            if not alternatives:
                                continue
                            alternative = reviewer(image, group, other.record, alternatives)
                            if alternative and not any(math.hypot(alternative['x']-c['x'], alternative['y']-c['y']) < 5 for c, _, _ in accepted):
                                accepted.append((alternative, other, alternatives))
                        # Unreviewed extra locations or conflicting accepted symbols stay unresolved.
                        if len(accepted) == 1 and len(texts) <= 4:
                            chosen, text, circles = accepted[0]
                            row.update(text=text.matched_text, match_score=text.score, box=text.record['box'])
                        else:
                            chosen = None
                    row['vision_status'] = '已复核' if chosen else '不一致'
                    if not chosen:
                        row['reason'] = '视觉复核未通过，需人工定位'
                if chosen and enhanced and reviewer is None and group[1] == 220:
                    partials = partial_symbol_conflicts(chosen, circles)
                    if partials:
                        row['partial_symbol_conflicts'] = partials
                        row['reason'] = '邻近有高支持未拟合符号，不能强配远处圆点，需视觉或人工核对'
                        chosen = None
                if chosen and enhanced and reviewer is None:
                    competitors = competing_labels(chosen, text.record, group[0], labels)
                    owners = []
                    for competitor in competitors:
                        key = (competitor['record_id'], group[1])
                        if key not in ownership_circles:
                            ownership_circles[key] = detect_circle_candidates(
                                pixels, label_records[key[0]]['box'], cv2, refine=True, voltage=key[1])
                        options = ownership_circles[key]
                        # The other label must independently prefer this same circle
                        # with the existing confidence margin. Proximity alone is
                        # insufficient in dense substations.
                        if (options and options[0].get('refined')
                                and math.hypot(options[0]['x']-chosen['x'], options[0]['y']-chosen['y']) < 12
                                and options[0]['score']-(options[1]['score'] if len(options)>1 else 0) >= .04):
                            owners.append(competitor)
                    if owners:
                        row['competing_labels'] = owners
                        row['reason'] = '圆点附近有其他站名归属竞争，需视觉或人工核对'
                        chosen = None
                if chosen:
                    row['ocr_record_id'] = text.record['id']
                    row['ocr_retried'] = text.record.get('ocr_retried', False)
                    row['ocr_conflict'] = text.record.get('ocr_conflict', False)
                    row['alternatives'] = text.record.get('alternatives', [])
                    row['point'] = {k: chosen[k] for k in ('x', 'y')}
                    row['circle_score'] = chosen['score']
                    row['circle_refined'] = chosen.get('refined', False)
                    row['circle_support'] = chosen.get('edge_support')
                    row['circle_margin'] = round(chosen['score']-max((c['score'] for c in circles if c is not chosen), default=0), 3)
            elif ambiguous:
                row['reason'] = '同名多位置或跨电压，需人工定位'
            elif texts[0].score < .9:
                row['reason'] = '站名匹配不足，需人工定位'
        if row['point']:
            candidate = dict(point=row['point'], text=row['text'], ocr_engine=engine,
                             vision_status=row['vision_status'], match_score=row['match_score'],
                             circle_score=row['circle_score'], circle_margin=row['circle_margin'])
            for n in result['nodes']:
                if n['id'] in groups[group]:
                    n['map_candidate'] = deepcopy(candidate)
        report.append(row)
    model.validate_catalog(result)
    return dict(data=result, report=dict(engine=engine, algorithm_version=ALGORITHM_VERSION if enhanced else 'baseline', recipe=OCR_RECIPE if enhanced else 'native1500-baseline', tiles=len(tiles(*image.size)), ocr_boxes=len(records),
                                        ocr_retried=sum(bool(r.get('ocr_retried')) for r in records),
                                        ocr_conflicts=sum(bool(r.get('ocr_conflict')) for r in records),
                                        ocr_recovered=sum(bool(r.get('ocr_recovered')) for r in records),
                                        sample_selection='fixed' if sample is not None else 'ocr-stratified-exploration', sampled=len(report),
                                        matched=sum(r['text'] is not None for r in report), candidates=sum(r['point'] is not None for r in report),
                                        vision_reviewed=sum(r['vision_status']=='已复核' for r in report), rows=report))


if __name__ == '__main__':
    import sys
    request = json.loads(Path(sys.argv[1]).read_text())
    with Image.open(request['image']) as source:
        records = recognize(source.convert('RGB'), 'ppocrv6', station_names=request['station_names'], enhanced=request['enhanced'], base_records=request.get('base_records'))
    Path(sys.argv[2]).write_text(json.dumps(records, ensure_ascii=False))
