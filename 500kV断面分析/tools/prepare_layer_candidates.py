"""Attach traceable position suggestions without changing reviewed positions."""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.layer_review_server import DATA
from tools.run_500kv_anchor_calibrator import atomic_write
from tools.build_500kv_position_candidates import clean_text


def in_tiles(point, tiles):
    for tile in tiles:
        m = re.fullmatch(r'r(\d\d)c(\d\d)', tile)
        if m:
            row, col = map(int, m.groups())
            x, y = (col - 1) * 1122.2, (row - 1) * 943.75
            if x <= point['x'] <= x + 1500 and y <= point['y'] <= y + 1250:
                return True
    return False


def enrich(data, old_positions, old_candidates, pins, vision=None):
    text_records = {}
    for node in old_candidates['nodes']:
        for rec in node.get('text_candidates', []):
            if rec.get('box') and rec.get('record_id'):
                text_records.setdefault(rec['record_id'], rec)
    by_text = defaultdict(list)
    for rec in text_records.values():
        by_text[clean_text(rec.get('record_text'))].append(rec)
    for tile, records in (vision or {}).items():
        match = re.fullmatch(r'r(\d\d)c(\d\d)', tile)
        if not match:
            continue
        row, col = map(int, match.groups())
        left, top = (col - 1) * 1122.2, (row - 1) * 943.75
        for index, rec in enumerate(records):
            text = clean_text(rec.get('text'))
            if len(text) < 2:
                continue
            box = {'x': round(left + rec['x'] * 1500, 2), 'y': round(top + (1-rec['y']-rec['h']) * 1250, 2), 'width': round(rec['w'] * 1500, 2), 'height': round(rec['h'] * 1250, 2)}
            by_text[text].append({'record_id': f'vision:{tile}:{index}', 'record_text': rec['text'], 'box': box, 'vision': True})
    old = defaultdict(list)
    for item in old_positions['nodes']:
        if item.get('status') == 'verified' and item.get('anchor'):
            old[clean_text(item['name'])].append(item)
    pin_names = defaultdict(list)
    for pin in pins:
        if pin.get('x') is not None and pin.get('y') is not None:
            pin_names[clean_text(pin['name'])].append(pin)
    stats = defaultdict(int)
    for node in data['nodes']:
        name = clean_text(node['name'])
        suggestions = []
        for pin in pin_names[name]:
            point = {'x': pin['x'], 'y': pin['y']}
            suggestions.append({'kind': '原校对标记', 'point': point, 'source': f"原平台 {pin.get('id', '')}；标记可能是文字附近的位置", 'warning': '' if in_tiles(point, node.get('tiles', [])) else '位置与名称图块不一致，须重点核验'})
        # Use exact normalized names only; abbreviated station names are not assumed equivalent.
        for item in old[name]:
            point = item['anchor']
            suggestions.append({'kind': '500kV已校准锚点', 'point': point, 'source': f"500kV锚点：{item['name']}；同名、跨电压须核验", 'warning': '' if in_tiles(point, node.get('tiles', [])) else '位置与名称图块不一致，须重点核验'})
        for rec in by_text[name]:
            box = rec['box']
            point = {'x': round(box['x'] + box['width']/2, 2), 'y': round(box['y'] + box['height']/2, 2)}
            if not in_tiles(point, node.get('tiles', [])):
                continue
            if any(s['kind'] == 'OCR文字框' and abs(s['point']['x']-point['x']) < 30 and abs(s['point']['y']-point['y']) < 30 for s in suggestions):
                continue
            source = '本次图块OCR' if rec.get('vision') else '原500kV定位OCR'
            suggestions.append({'kind': 'OCR文字框', 'point': point, 'box': box, 'source': f"{source}：{rec['record_text']}；这是文字位置，不是节点锚点", 'warning': '需对照原图确认名称并选择节点锚点'})
        node['position_candidates'] = suggestions
        for kind in set(s['kind'] for s in suggestions):
            stats[kind] += 1
        if suggestions:
            stats['有候选节点'] += 1
    return dict(stats)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, default=DATA)
    p.add_argument('--pins', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--vision', type=Path, help='图块OCR结果')
    args = p.parse_args()
    if args.output.exists():
        p.error('输出已存在，不覆盖已有数据')
    data = json.loads(args.data.read_text(encoding='utf-8'))
    load = lambda x: json.loads(x.read_text(encoding='utf-8'))
    stats = enrich(data, load(ROOT/'data/500kv_node_positions.json'), load(ROOT/'data/500kv_node_position_candidates.json'), load(args.pins), load(args.vision) if args.vision else None)
    atomic_write(args.output, data)
    print(json.dumps(stats, ensure_ascii=False))

if __name__ == '__main__': main()
