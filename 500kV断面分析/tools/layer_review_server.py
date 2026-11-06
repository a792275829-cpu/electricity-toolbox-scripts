from __future__ import annotations

import argparse
import json
import math
import mimetypes
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from toolbox_shared.storage import data_path
from tools.run_500kv_anchor_calibrator import atomic_write

WEB = Path(__file__).resolve().parent / 'layer_review'
DATA = data_path(ROOT / 'local_data' / '分层拓扑校对' / 'review.json')
IMAGE = ROOT / '接线图.png'
LOCK = Lock()


def import_source(source: Path, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f'校对数据已存在，不会覆盖：{destination}')
    rows = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or not rows:
        raise ValueError('来源必须是非空 mapping2.json 列表')
    nodes = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get('disp'), str):
            raise ValueError(f'来源第 {i} 项格式无效')
        nodes.append({'id': str(i), 'name': row['disp'], 'base': row.get('base', ''),
                      'tiles': row.get('tiles', []), 'strong': row.get('strong', []),
                      'weak': row.get('weak', []), 'voltage': '待核验', 'mapping': '',
                      'mapping_status': '待核验', 'anchor': None, 'position_status': '待定位',
                      'note': ''})
    result = {'version': 1, 'source': source.name, 'nodes': nodes, 'connections': []}
    atomic_write(destination, result)
    return result


def validate_node(patch: dict) -> dict:
    allowed = {'name', 'voltage', 'mapping', 'mapping_status', 'anchor', 'position_status', 'note'}
    if not isinstance(patch, dict) or set(patch) != allowed:
        raise ValueError('节点字段不完整或不受支持')
    for key in ('name', 'mapping', 'note'):
        if not isinstance(patch[key], str) or len(patch[key]) > 10000:
            raise ValueError(f'{key} 无效')
    if not patch['name'].strip():
        raise ValueError('名称不能为空')
    if patch['voltage'] not in ('待核验', '500kV', '220kV', '其他'):
        raise ValueError('电压等级无效')
    if patch['mapping_status'] not in ('待核验', '已确认', '无匹配', '排除'):
        raise ValueError('映射状态无效')
    if patch['mapping_status'] == '已确认' and (not patch['mapping'].strip() or patch['voltage'] not in ('500kV', '220kV')):
        raise ValueError('确认映射前须填写电价原始名称并确定电压等级')
    if patch['position_status'] not in ('待定位', '候选', '已确认'):
        raise ValueError('坐标状态无效')
    anchor = patch['anchor']
    if anchor is not None:
        if not isinstance(anchor, dict) or set(anchor) != {'x', 'y'}:
            raise ValueError('坐标格式无效')
        if any(isinstance(anchor[k], bool) or not isinstance(anchor[k], (int, float)) or not math.isfinite(anchor[k]) for k in ('x', 'y')) or not (0 <= anchor['x'] <= 7111 and 0 <= anchor['y'] <= 5025):
            raise ValueError('坐标超出底图')
    if patch['position_status'] != '待定位' and anchor is None:
        raise ValueError('候选或已确认坐标不能为空')
    return patch


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/data':
            with LOCK:
                self.respond(200, json.loads(DATA.read_text(encoding='utf-8')))
            return
        files = {'/': WEB / 'index.html', '/app.js': WEB / 'app.js', '/styles.css': WEB / 'styles.css', '/image.png': IMAGE}
        target = files.get(path)
        if not target:
            self.send_error(404)
            return
        self.respond(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or 'application/octet-stream')

    def do_PATCH(self):
        if not self.path.startswith('/api/nodes/'):
            self.send_error(404)
            return
        try:
            # Restrict state-changing requests to the local page, avoiding cross-site writes.
            origin = self.headers.get('Origin', '')
            if origin != f'http://127.0.0.1:{self.server.server_port}':
                raise ValueError('请求来源无效')
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 65536:
                raise ValueError('请求长度无效')
            patch = validate_node(json.loads(self.rfile.read(length)))
            node_id = self.path.removeprefix('/api/nodes/')
            with LOCK:
                data = json.loads(DATA.read_text(encoding='utf-8'))
                node = next((n for n in data['nodes'] if n['id'] == node_id), None)
                if node is None:
                    raise ValueError('节点不存在')
                node.update(patch)
                node['updated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
                atomic_write(DATA, data)
            self.respond(200, node)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.respond(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description='500/220kV 节点人工校对')
    parser.add_argument('--import-source', type=Path, help='首次导入 mapping2.json；不覆盖现有校对数据')
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    if args.import_source:
        data = import_source(args.import_source, DATA)
        print(f"已导入 {len(data['nodes'])} 条候选：{DATA}", flush=True)
    if not DATA.exists():
        parser.error('请首次使用 --import-source 指定 mapping2.json')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'校对平台：{url}\n校对数据：{DATA}', flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
