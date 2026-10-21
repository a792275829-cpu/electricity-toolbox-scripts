"""Validate full installer, delta metadata and exact published patch assets."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'desktop/python'))
from github_update import REPOSITORY, trusted_url
from delta_update import INDEX_NAME, select_route


def valid_asset(asset):
    return (isinstance(asset.get('digest'), str)
            and re.fullmatch(r'sha256:[0-9a-fA-F]{64}', asset['digest'])
            and type(asset.get('size')) is int and asset['size'] > 0
            and trusted_url(asset.get('browser_download_url', ''), REPOSITORY))


def validate(data, index_path=None):
    version = data['tag_name'].removeprefix('v')
    name = f'ElectricityToolbox-{version}-apple-silicon.dmg'
    assets = {a['name']: a for a in data['assets']}
    if len(assets) != len(data['assets']) or not {name, name + '.sha256'} <= assets.keys():
        raise ValueError('缺少唯一的 DMG 及校验文件')
    if not valid_asset(assets[name]):
        raise ValueError('DMG 下载链接、大小或 GitHub SHA-256 摘要无效')
    allowed = {name, name + '.sha256', INDEX_NAME}
    pattern = rf'ElectricityToolbox-[0-9]+\.[0-9]+\.[0-9]+-to-{re.escape(version)}-apple-silicon\.patch\.tar\.xz'
    for filename, asset in assets.items():
        if re.fullmatch(pattern, filename):
            if not valid_asset(asset):
                raise ValueError('补丁附件元数据无效')
        elif filename not in allowed:
            raise ValueError('不允许独立 App、旧更新索引或未知发布附件')
    if INDEX_NAME in assets and not valid_asset(assets[INDEX_NAME]):
        raise ValueError('增量更新索引元数据无效')
    if index_path is not None:
        raw = Path(index_path).read_bytes()
        asset = assets.get(INDEX_NAME, {})
        if (len(raw) != asset.get('size') or asset.get('digest') != 'sha256:' + hashlib.sha256(raw).hexdigest()):
            raise ValueError('更新索引与 GitHub 附件摘要不一致')
        index = json.loads(raw)
        select_route(index, '0.0.0', version, assets[name]['size'], REPOSITORY)
        listed = set()
        for edge in index['patches']:
            if edge['target'] == version:
                if edge['name'] not in assets or any(
                        edge[k] != assets[edge['name']].get(k)
                        for k in ('size', 'digest', 'browser_download_url')):
                    raise ValueError('增量索引与本版本补丁附件不一致')
                listed.add(edge['name'])
        if {n for n in assets if n.endswith('.patch.tar.xz')} != listed:
            raise ValueError('发布包含索引外的补丁')
    elif any(n.endswith('.patch.tar.xz') for n in assets):
        raise ValueError('发布补丁必须同时校验更新索引')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('release', type=Path)
    parser.add_argument('--index', type=Path, required=True)
    args = parser.parse_args()
    validate(json.loads(args.release.read_text()), args.index)
    print('DMG and incremental update assets validated')
