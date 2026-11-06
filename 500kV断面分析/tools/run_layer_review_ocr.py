"""Recognize the original 30 1500×1250 diagram tiles using macOS Vision."""
from __future__ import annotations
import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tiles', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=3, choices=range(1, 9))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出已存在，不覆盖已有数据')
    paths = [args.tiles / f'r{row:02}c{col:02}.jpg' for row in range(1, 6) for col in range(1, 7)]
    if any(not p.is_file() for p in paths):
        parser.error('需要原始 30 张 r01c01.jpg 至 r05c06.jpg 图块')
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / 'vision'
        subprocess.run(['xcrun', 'swiftc', str(ROOT / 'layer_review_vision.swift'), '-o', str(binary)], check=True)
        def recognize(path):
            result = subprocess.run([str(binary), str(path)], check=True, capture_output=True, text=True)
            return path.stem, json.loads(result.stdout)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            data = dict(pool.map(recognize, paths))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    print(f"完成 {len(data)} 图块，{sum(map(len, data.values()))} 文字框：{args.output}")

if __name__ == '__main__': main()
