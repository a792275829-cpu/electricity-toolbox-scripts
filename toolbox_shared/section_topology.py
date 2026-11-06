"""Editable station catalog, conservative workbook synchronization and storage.

Only local state contains price curves. IDs survive daily price changes; ambiguous
same-station variants require an explicit association before analysis.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from uuid import uuid4

from openpyxl import load_workbook
from toolbox_shared.section_schema import (
    _column_map, normalize_price_node_name, parse_topology_edges, price_node_voltage,
)
from toolbox_shared.storage import WORKSPACE_ROOT, data_path

MODULE = WORKSPACE_ROOT / '500kV断面分析'


def catalog_path() -> Path:
    return data_path(MODULE / 'local_data/拓扑编辑/catalog.json')


def applied_path() -> Path:
    return catalog_path().with_name('applied.json')


def station_name(raw: str) -> str:
    text = unicodedata.normalize('NFKC', raw).strip()
    # A voltage prefix can describe the station, while the final label is the bus.
    text = re.sub(r'^其他', '', text)
    text = re.sub(r'^\s*\d+\s*[_-]?\s*k\s*v\s*', '', text, flags=re.I)
    text = re.split(r'\(?\s*\d+\s*[_-]?\s*k\s*v', text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r'(?:#.*|\s*M\d+.*|\d+母.*|母线.*)$', '', text, flags=re.I).strip(' ()_-')
    return normalize_price_node_name(text)


def _number(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError('电价存在非数值内容') from exc
    if not result.is_finite():
        raise ValueError('电价存在非有限数值')
    value = float(result)
    if not math.isfinite(value):
        raise ValueError('电价超出有效数值范围')
    return value


def read_catalog(path: Path) -> dict:
    """Read every eligible sheet; deduplicate by station, voltage and full curve."""
    book = load_workbook(path, read_only=True, data_only=True)
    headers = None
    unique = {}
    rows_read = 0
    ignored = 0
    try:
        for sheet in book.worksheets:
            columns = None
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                if columns is None:
                    columns = _column_map(row)
                    if columns:
                        pairs = sorted(zip(columns['time_headers'], columns['time_columns']))
                        current = [t for t, _ in pairs]
                        if len(set(current)) != len(current):
                            raise ValueError(f'{sheet.title} 存在重复时点')
                        if headers is not None and headers != current:
                            raise ValueError('各工作表的分时时点不一致，无法同步')
                        headers = current
                    continue
                if columns['node'] >= len(row) or not row[columns['node']]:
                    continue
                raw = str(row[columns['node']]).strip()
                voltage = price_node_voltage(raw)
                if voltage not in (None, 500, 220):
                    ignored += 1
                    continue
                station = station_name(raw)
                if not station:
                    raise ValueError(f'{sheet.title} 第 {row_number} 行无法识别站名')
                try:
                    curve = [_number(row[i] if i < len(row) else None) for _, i in pairs]
                except ValueError as exc:
                    raise ValueError(f'{sheet.title} 第 {row_number} 行：{exc}') from exc
                key = (station, voltage, tuple(curve))
                # Keep identity fingerprints, never a list of source names.
                fingerprint = hashlib.sha256(unicodedata.normalize('NFKC', raw).encode()).hexdigest()
                item = unique.setdefault(key, dict(station=station, voltage=voltage, source_voltage=voltage,
                                                  curve=curve, keys=[]))
                if fingerprint not in item['keys']:
                    item['keys'].append(fingerprint)
                rows_read += 1
    finally:
        book.close()
    if not unique:
        raise ValueError('未找到带分时时点的500kV、220kV或待确认节点')
    entries = sorted(unique.values(), key=lambda n: (n['station'], n['voltage'] or 0, repr(n['curve'])))
    for n in entries:
        n['keys'].sort()
        n['curve_headers'] = headers
    return dict(headers=headers, entries=entries, rows=rows_read,
                duplicates=rows_read-len(entries), ignored=ignored, source=Path(path).name)


def seed_catalog() -> dict:
    edges, _ = parse_topology_edges(MODULE / '500kV节点拓扑.md')
    positions = json.loads((MODULE / 'data/500kv_node_positions.json').read_text())
    records = {n['name']: n.get('anchor') for n in positions['nodes']}
    nodes = []
    names = sorted({e.a for e in edges} | {e.b for e in edges})
    for name in names:
        nodes.append(dict(id=hashlib.sha256(('500:'+name).encode()).hexdigest()[:32], station=station_name(name), voltage=500,
                          name=name, curve=[], keys=[], anchor=records.get(name),
                          present=True, pending=False, city=''))
    ids = {name: n['id'] for name, n in zip(names, nodes)}
    connections = [dict(a=ids[e.a], b=ids[e.b], kind='line', group=e.group) for e in edges]
    return dict(version=1, revision=0, headers=[], nodes=nodes, edges=connections,
                image=positions['image'], source='内置500kV拓扑', summary={})


def load_catalog(path: Path | None = None) -> dict:
    path = path or catalog_path()
    if not path.exists():
        return seed_catalog()
    data = json.loads(path.read_text(encoding='utf-8'))
    validate_catalog(data)
    return data


def synchronize(data: dict, imported: dict) -> dict:
    result = deepcopy(data)
    old_groups = defaultdict(list)
    new_groups = defaultdict(list)
    for n in result['nodes']:
        old_groups[n['station'], n.get('source_voltage', n['voltage'])].append(n)
        n['present'] = False
    for n in imported['entries']:
        new_groups[n['station'], n['voltage']].append(n)
    added = updated = pending = 0
    for key, incoming in new_groups.items():
        old = old_groups[key]
        matches = {}
        used = set()
        # Match only mutually unique identities, then unchanged full curves.
        for predicate in (
            lambda a, b: bool(set(a['keys']) & set(b['keys'])),
            lambda a, b: a.get('curve_headers', data['headers']) == imported['headers'] and a['curve'] == b['curve'],
        ):
            candidates = {i: [n for n in old if n['id'] not in used and predicate(n, item)]
                          for i, item in enumerate(incoming) if i not in matches}
            counts = Counter(n['id'] for values in candidates.values() for n in values)
            for i, values in candidates.items():
                if len(values) == 1 and counts[values[0]['id']] == 1:
                    matches[i] = values[0]
                    used.add(values[0]['id'])
        if len(old) == len(incoming) == 1 and not matches:
            matches[0] = old[0]
        for i, item in enumerate(incoming):
            if i in matches:
                target = matches[i]
                target.update(deepcopy(item), present=True)
                if target.get('voltage_override') is not None:
                    target['voltage'] = target['voltage_override']
                updated += 1
            else:
                # Any unexplained old variant makes a new candidate ambiguous.
                ambiguous = any(n['id'] not in used for n in old)
                target = dict(id=uuid4().hex, name=item['station'], anchor=None,
                              city='', present=True, pending=ambiguous, **deepcopy(item))
                # Share a known physical station location, never its electrical edges.
                locations = {json.dumps(n['map_anchor'], sort_keys=True) for n in old if n.get('map_anchor') is not None}
                if result.get('base_map') and len(locations) == 1:
                    target['map_anchor'] = json.loads(next(iter(locations)))
                result['nodes'].append(target)
                added += 1
            pending += int(target['pending'])
    result.update(headers=imported['headers'], source=imported['source'])
    result['summary'] = dict(added=added, updated=updated,
                             missing=sum(not n['present'] for n in result['nodes']),
                             pending=pending, duplicates=imported['duplicates'], ignored=imported['ignored'])
    return result


def resolve_variant(data: dict, candidate_id: str, old_id: str | None) -> str:
    candidate = next(n for n in data['nodes'] if n['id'] == candidate_id)
    if not candidate['present'] or not candidate['pending']:
        raise ValueError('请选择待确认的新价格节点')
    if old_id is None:
        candidate['pending'] = False
        return candidate_id
    old = next(n for n in data['nodes'] if n['id'] == old_id)
    if old['present'] or (old['station'], old.get('source_voltage', old['voltage'])) != (candidate['station'], candidate.get('source_voltage', candidate['voltage'])):
        raise ValueError('只能对应同站同电压的缺失节点')
    old.update(curve=candidate['curve'], curve_headers=candidate.get('curve_headers', data['headers']),
               keys=candidate['keys'], present=True, pending=False)
    data['nodes'].remove(candidate)
    for e in data['edges']:
        e['a'] = old_id if e['a'] == candidate_id else e['a']
        e['b'] = old_id if e['b'] == candidate_id else e['b']
    validate_catalog(data)
    return old_id


def generate_schematic(data: dict, *, reset_layout: bool = False) -> dict:
    """Project annotated stations into the existing analysis coordinate system."""
    validate_catalog(data)
    if not data.get('base_map'):
        raise ValueError('请先导入底图并标注站点')
    result = deepcopy(data)
    base = result['base_map']
    marked = [n for n in result['nodes'] if n.get('map_anchor') is not None and n['present']]
    confirmed = [e for e in result['edges'] if e.get('reviewed') and
                 all(any(n['id'] == e[k] for n in marked) for k in ('a', 'b'))]
    if not confirmed:
        raise ValueError('请先标注线路两端，并至少核对确认一条连接')
    groups = defaultdict(list)
    for n in marked:
        groups[n['station'], n['voltage']].append(n)
    for variants in groups.values():
        for index, n in enumerate(sorted(variants, key=lambda n: n['id'])):
            if n.get('diagram_manual') and n['anchor'] is not None and not reset_layout:
                continue
            offset = (index - (len(variants)-1)/2) * max(result['image']['width'], result['image']['height']) * .04
            n['anchor'] = dict(
                x=max(0, min(result['image']['width'], n['map_anchor']['x']/base['width']*result['image']['width']+offset)),
                y=n['map_anchor']['y']/base['height']*result['image']['height'])
            n['diagram_manual'] = False
    result['schematic_generated'] = True
    validate_catalog(result)
    return result


def validate_catalog(data: dict) -> None:
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('nodes'), list) or not isinstance(data.get('edges'), list):
        raise ValueError('拓扑文件格式无效')
    if not isinstance(data.get('source'), str):
        raise ValueError('节点库来源无效')
    if not isinstance(data.get('revision'), int) or data['revision'] < 0:
        raise ValueError('拓扑版本号无效')
    headers = data.get('headers')
    if not isinstance(headers, list) or any(not isinstance(t, str) for t in headers) or len(set(headers)) != len(headers):
        raise ValueError('分时时点无效')
    image = data.get('image')
    if not isinstance(image, dict) or any(not isinstance(image.get(k), (int, float)) or
            not math.isfinite(image[k]) or image[k] <= 0 for k in ('width', 'height')):
        raise ValueError('画布尺寸无效')
    base = data.get('base_map')
    if base is not None:
        if (not isinstance(base, dict) or not isinstance(base.get('id'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', base['id'])
                or not isinstance(base.get('name'), str)
                or any(isinstance(base.get(k), bool) or not isinstance(base.get(k), (int, float))
                       or not math.isfinite(base[k]) or base[k] <= 0 for k in ('width', 'height'))):
            raise ValueError('底图信息无效')
    if 'schematic_generated' in data and not isinstance(data['schematic_generated'], bool):
        raise ValueError('简图状态无效')
    ids = set()
    for n in data['nodes']:
        if not isinstance(n, dict):
            raise ValueError('节点格式无效')
        if not isinstance(n.get('city', ''), str) or len(n.get('city', '')) > 100:
            raise ValueError('节点地市无效')
        if not isinstance(n.get('id'), str) or not n['id'] or n['id'] in ids:
            raise ValueError('节点ID重复或无效')
        ids.add(n['id'])
        if not isinstance(n.get('name'), str) or not n['name'].strip() or len(n['name']) > 200:
            raise ValueError('节点名称无效')
        if n.get('voltage') not in (None, 220, 500):
            raise ValueError('电压分类无效')
        if not isinstance(n.get('station'), str) or not n['station'] or not isinstance(n.get('keys'), list):
            raise ValueError('节点身份信息无效')
        if not all(isinstance(n.get(k), bool) for k in ('present', 'pending')):
            raise ValueError('节点状态无效')
        curve = n.get('curve')
        if not isinstance(curve, list) or (curve and len(curve) != len(n.get('curve_headers', data['headers']))):
            raise ValueError('电价曲线长度与时点不一致')
        if any(v is not None and (isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)) for v in curve):
            raise ValueError('电价曲线存在无效数值')
        point = n.get('map_anchor')
        if point is not None and (not base or not isinstance(point, dict) or set(point) != {'x', 'y'}
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                       or not 0 <= v <= base['width' if k == 'x' else 'height'] for k, v in point.items())):
            raise ValueError('底图标注坐标无效')
        if 'diagram_manual' in n and not isinstance(n['diagram_manual'], bool):
            raise ValueError('简图布局状态无效')
        anchor = n.get('anchor')
        if anchor is not None and (set(anchor) != {'x', 'y'} or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            or not 0 <= v <= data['image']['width' if k == 'x' else 'height'] for k, v in anchor.items())):
            raise ValueError('节点坐标超出画布')
    by_id = {n['id']: n for n in data['nodes']}
    seen = set()
    for e in data['edges']:
        if not isinstance(e, dict) or any(not isinstance(e.get(k), str) for k in ('a', 'b', 'kind')):
            raise ValueError('线路格式无效')
        if 'reviewed' in e and not isinstance(e['reviewed'], bool):
            raise ValueError('线路核对状态无效')
        key = tuple(sorted((e['a'], e['b'])))
        if e['a'] not in ids or e['b'] not in ids or e['a'] == e['b'] or key in seen:
            raise ValueError('存在重复连接、自连接或不存在的端点')
        if e.get('kind') not in ('line', 'transformer'):
            raise ValueError('连接类型无效')
        if e['kind'] == 'line' and by_id[e['a']]['voltage'] != by_id[e['b']]['voltage']:
            raise ValueError('跨电压连接须标记为站内变压器')
        seen.add(key)


def save_catalog(data: dict, path: Path | None = None, *, check_revision: bool = True) -> dict:
    path = path or catalog_path()
    validate_catalog(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(path.read_text()) if path.exists() else {'revision': 0}
        if check_revision and previous['revision'] != data['revision']:
            raise ValueError('拓扑已在其他窗口更新，请导出当前编辑后重新打开')
        result = deepcopy(data)
        result['revision'] = previous['revision'] + 1
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(result, handle, ensure_ascii=False, allow_nan=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    return result


def analysis_selection(data: dict) -> tuple[list[dict], list[dict]]:
    validate_catalog(data)
    if data.get('base_map') and not data.get('schematic_generated'):
        raise ValueError('请先核对连接并生成简图，再应用到分析')
    if any(n['present'] and n['pending'] and n['voltage'] == 500 for n in data['nodes']):
        raise ValueError('500kV同站多价格仍有待确认节点，请先在拓扑编辑器中处理')
    eligible = {n['id']: n for n in data['nodes'] if n['present'] and n['voltage'] == 500
                and n['anchor'] is not None and (not data.get('base_map') or n.get('map_anchor') is not None)}
    edges = [e for e in data['edges'] if e['kind'] == 'line' and e['a'] in eligible and e['b'] in eligible
             and (not data.get('base_map') or e.get('reviewed'))]
    if not edges:
        raise ValueError('至少需要一条两端已定位、有效的500kV站间线路')
    connected = {e[k] for e in edges for k in ('a', 'b')}
    return [n for n in eligible.values() if n['id'] in connected], edges


def materialize_analysis(data: dict, directory: Path) -> tuple[Path, Path, Path]:
    """Adapt the editor snapshot to the existing Markdown/XLSX/map pipeline."""
    from openpyxl import Workbook
    nodes, edges = analysis_selection(data)
    if not data['headers']:
        raise ValueError('请先导入节点电价表')
    directory.mkdir(parents=True, exist_ok=True)
    counts = Counter(n['name'] for n in nodes)
    names = {n['id']: n['name'] + (f"〔{n['id'][:8]}〕" if counts[n['name']] > 1 else '') for n in nodes}
    if any(re.search(r'[-，,、：\r\n]', value) or value.lstrip().startswith('#') for value in names.values()):
        raise ValueError('用于分析的节点名称不能包含连字符、逗号、顿号、冒号或换行')
    topology = directory / '编辑拓扑.md'
    topology.write_text('# 编辑拓扑\n\n## 1. 节点分区\n\n### 人工编辑\n' +
                        '、'.join(names.values()) + '\n\n## 2. 已确认的接线拓扑\n\n### 人工编辑\n' +
                        '\n'.join(names[e['a']]+'-'+names[e['b']] for e in edges) + '\n', encoding='utf-8')
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '500kV节点电价'
    sheet.append(['节点名称', '数据项', *data['headers']])
    for n in nodes:
        if len(n['curve']) != len(data['headers']):
            raise ValueError('所选节点缺少本次电价曲线，请重新导入电价表')
        sheet.append([names[n['id']], '节点电价', *n['curve']])
        sheet.cell(sheet.max_row, 1).data_type = 's'
    prices = directory / '编辑拓扑电价.xlsx'
    workbook.save(prices)
    workbook.close()
    positions = directory / 'data/500kv_node_positions.json'
    positions.parent.mkdir(exist_ok=True)
    positions.write_text(json.dumps(dict(image=data['image'], nodes=[
        dict(name=names[n['id']], anchor=n['anchor'], status='verified', city=n.get('city', '')) for n in nodes
    ]), ensure_ascii=False), encoding='utf-8')
    return topology, prices, positions
