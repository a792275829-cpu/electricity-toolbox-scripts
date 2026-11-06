"""Local HTML topology editor service; all workbook/state rules stay in section_topology."""
from __future__ import annotations

import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
import secrets
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
from urllib.parse import unquote, urlparse

from toolbox_shared import section_topology as model
from toolbox_shared.storage import WORKSPACE_ROOT

WEB = WORKSPACE_ROOT / '500kV断面分析/tools/topology_editor'


class BrowserEditor:
    def __init__(self, *, input_path=None, initial_data=None):
        self.input_path = Path(input_path) if input_path else None
        self.initial_data = deepcopy(initial_data)
        self.applied = Queue()
        self.dirty = False
        self.lock = Lock()
        self.token = secrets.token_urlsafe(32)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.editor = self
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.url = f'{self.origin}/#{self.token}'
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=.05), name='topology-html', daemon=True)
        self.thread.start()
        self.closed = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def state(self):
        imported_initial = self.initial_data is not None
        data = self.initial_data if imported_initial else model.load_catalog()
        model.validate_catalog(data)
        if self.input_path:
            imported = model.read_catalog(self.input_path)
            data = model.synchronize(data, imported)
            self.input_path = None
            self.dirty = True
            self.initial_data = None
            return dict(data=data, dirty=True)
        self.initial_data = None
        self.dirty = imported_initial
        return dict(data=data, dirty=imported_initial)

    def action(self, name, body):
        if name == 'status':
            self.dirty = bool(body.get('dirty'))
            return {'ok': True}
        data = body['data']
        model.validate_catalog(data)
        if name == 'sync':
            data = model.synchronize(data, body['imported'])
            model.validate_catalog(data)
            return dict(data=data)
        if name == 'resolve':
            selected = model.resolve_variant(data, body['candidate'], body.get('old'))
            return dict(data=data, selected=selected)
        if name == 'validate':
            return {'ok': True}
        if name in ('save', 'apply'):
            if name == 'apply':
                nodes, edges = model.analysis_selection(data)
            saved = model.save_catalog(data)
            self.dirty = False
            result = dict(data=saved)
            if name == 'apply':
                model.save_catalog(saved, model.applied_path(), check_revision=False)
                self.applied.put(model.applied_path())
                result.update(nodes=len(nodes), edges=len(edges))
            return result
        raise ValueError('未知编辑操作')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log session tokens, filenames or workbook contents.

    @property
    def editor(self):
        return self.server.editor

    def respond(self, status, data, mime='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorize(self, *, writing=False):
        if self.headers.get('Host') != self.editor.origin.removeprefix('http://'):
            raise PermissionError('请求地址无效')
        if not secrets.compare_digest(self.headers.get('X-Editor-Token', ''), self.editor.token):
            raise PermissionError('编辑会话已失效，请从工具箱重新打开')
        origin = self.headers.get('Origin')
        if (writing and origin != self.editor.origin) or (origin and origin != self.editor.origin):
            raise PermissionError('请求来源无效')

    def do_GET(self):
        path = urlparse(self.path).path
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path in assets:
            name, mime = assets[path]
            self.respond(200, (WEB / name).read_bytes(), mime)
            return
        try:
            self.authorize()
            if path != '/api/state':
                self.respond(404, {'error': '页面不存在'})
                return
            with self.editor.lock:
                self.respond(200, self.editor.state())
        except PermissionError as exc:
            self.respond(403, {'error': str(exc)})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.respond(400, {'error': str(exc)})

    def do_POST(self):
        try:
            self.authorize(writing=True)
            length = int(self.headers.get('Content-Length', '0'))
            path = urlparse(self.path).path
            limit = 100 * 1024 * 1024 if path == '/api/read-prices' else 30 * 1024 * 1024
            if not 0 < length <= limit:
                raise ValueError('文件或请求大小无效')
            raw = self.rfile.read(length)
            if path == '/api/read-prices':
                filename = Path(unquote(self.headers.get('X-File-Name', '节点电价.xlsx'))).name
                if Path(filename).suffix.lower() != '.xlsx':
                    raise ValueError('请选择.xlsx节点电价表')
                with NamedTemporaryFile(suffix='.xlsx') as temporary:
                    temporary.write(raw)
                    temporary.flush()
                    imported = model.read_catalog(Path(temporary.name))
                    imported['source'] = filename
                self.respond(200, imported)
                return
            body = json.loads(raw)
            with self.editor.lock:
                result = self.editor.action(path.removeprefix('/api/'), body)
            self.respond(200, result)
        except PermissionError as exc:
            self.respond(403, {'error': str(exc)})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.respond(400, {'error': str(exc)})
        except Exception:
            self.respond(400, {'error': '无法读取文件，请确认是有效的节点电价.xlsx或拓扑JSON'})
