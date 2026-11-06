"""Generate a self-contained, offline review page from the local review data."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.layer_review_server import DATA, IMAGE, WEB


def build(data_path: Path, image_path: Path, output_path: Path) -> int:
    data = json.loads(data_path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('nodes'), list) or not data['nodes']:
        raise ValueError('校对数据格式无效')
    key = hashlib.sha256(json.dumps([data.get('source'), [n['id'] for n in data['nodes']]], ensure_ascii=False).encode()).hexdigest()[:16]
    seed = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    css = (WEB / 'styles.css').read_text(encoding='utf-8')
    app = (WEB / 'app.js').read_text(encoding='utf-8')
    offline = (WEB / 'offline.js').read_text(encoding='utf-8')
    image = base64.b64encode(image_path.read_bytes()).decode('ascii')
    html = html.replace('<link rel="stylesheet" href="/styles.css">', '<style>'+css+'</style>')
    html = html.replace('src="/image.png"', 'src="data:image/png;base64,'+image+'"')
    html = html.replace('<button id="fit"', '<button id="export" type="button">导出结果</button><label style="margin:0;white-space:nowrap">导入结果<input id="import" type="file" accept=".json,application/json" style="width:160px"></label><button id="fit"')
    html = html.replace('保存的记录是人工校对底稿，', '保存后记录存于本浏览器；请定期导出 JSON 备份，更换浏览器或电脑后可导入。记录不会自动同步至本机服务版。保存的记录是人工校对底稿，')
    scripts = f'<script>window.reviewSeed={seed};window.reviewDatasetKey={json.dumps(key)};</script><script>{offline}</script><script>{app}</script>'
    html = html.replace('<script src="/store.js"></script><script src="/app.js"></script>', scripts)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=output_path.parent, delete=False) as f:
        f.write(html)
        temp = Path(f.name)
    os.replace(temp, output_path)
    return len(data['nodes'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--output', type=Path, default=Path.home() / 'Downloads' / '500和220kV节点校对平台.html')
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'输出已存在，避免覆盖：{args.output}；请指定新的 --output')
    count = build(args.data, IMAGE, args.output)
    print(f'已生成 {count} 条节点：{args.output}')


if __name__ == '__main__':
    main()
