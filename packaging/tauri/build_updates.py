"""Publish direct deltas for recent releases and retain historical upgrade routes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'desktop/python'))
from delta_update import (FORMAT, INDEX_NAME, apply_patch, app_version, create_patch,
                          digest, select_route, verify_app)
from github_update import REPOSITORY, UpdateError, version_parts


def gh_json(*args):
    return json.loads(subprocess.check_output(['gh', *args], text=True))


def release_asset(release, name):
    matches = [a for a in release['assets'] if a['name'] == name]
    if len(matches) != 1:
        raise UpdateError(f'发布附件缺失或重复：{name}')
    return matches[0]


def fetch_asset(release, asset, directory):
    name = asset['name']
    if Path(name).name != name:
        raise UpdateError('发布附件名称无效')
    subprocess.run(['gh', 'release', 'download', release['tag_name'], '--repo', REPOSITORY,
                    '--pattern', name, '--dir', str(directory)], check=True)
    path = directory / name
    if path.stat().st_size != asset['size'] or 'sha256:' + digest(path) != asset.get('digest'):
        raise UpdateError(f'历史发布附件校验失败：{name}')
    return path


@contextmanager
def mounted_app(dmg):
    with tempfile.TemporaryDirectory(prefix='toolbox-update-mount-') as tmp:
        mount = Path(tmp) / 'image'
        mount.mkdir()
        result = subprocess.check_output(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                                          '-mountpoint', str(mount), '-plist', str(dmg)])
        plistlib.loads(result)  # Require a valid mount result before using the directory.
        try:
            app = mount / '电力工具箱.app'
            if not app.is_dir():
                raise UpdateError('历史镜像缺少应用')
            yield app
        finally:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True)


def build_one(old, new, output, full_size):
    base, target = app_version(old), app_version(new)
    name = f'ElectricityToolbox-{base}-to-{target}-apple-silicon.patch.tar.xz'
    installer = ROOT / 'packaging/tauri/install_user_app.py'
    patch = output / name
    edge = create_patch(old, new, installer, patch)
    if edge['size'] >= full_size:
        patch.unlink()
        return None
    # Publication requires actually applying the patch, then verifying the reconstructed app.
    with tempfile.TemporaryDirectory(prefix='toolbox-delta-check-') as tmp:
        restored = Path(tmp) / '电力工具箱.app'
        subprocess.run(['/usr/bin/ditto', '--norsrc', '--noextattr', '--noqtn', '--noacl',
                        str(old), str(restored)], check=True)
        apply_patch(restored, patch, Path(tmp) / 'installer.py', base, target)
    edge['browser_download_url'] = f'https://github.com/{REPOSITORY}/releases/download/v{target}/{name}'
    return edge


def build(app, output, full_only=False, base_apps=(), fetch_history=True, limit=3):
    app, output = Path(app), Path(output)
    target = app_version(app)
    verify_app(app)
    full = output / f'ElectricityToolbox-{target}-apple-silicon.dmg'
    if not full.is_file():
        raise UpdateError('请先构建本版本完整 DMG')
    output.mkdir(parents=True, exist_ok=True)
    # A dedicated directory avoids uploading stale patches from earlier local runs.
    assets = output / 'updates'
    if assets.exists():
        shutil.rmtree(assets)
    assets.mkdir()
    index = dict(format=FORMAT, version=target, patches=[])
    if not full_only:
        previous = []
        if fetch_history:
            releases = gh_json('api', f'repos/{REPOSITORY}/releases?per_page=100')
            previous = sorted((r for r in releases if not r['draft'] and not r['prerelease']
                               and version_parts(r['tag_name']) < version_parts(target)),
                              key=lambda r: version_parts(r['tag_name']), reverse=True)
        with tempfile.TemporaryDirectory(prefix='toolbox-update-history-') as tmp:
            cache = Path(tmp)
            if (previous and version_parts(previous[0]['tag_name'])[:2] == version_parts(target)[:2]
                    and any(a['name'] == INDEX_NAME for a in previous[0]['assets'])):
                inherited = fetch_asset(previous[0], release_asset(previous[0], INDEX_NAME), cache)
                history = json.loads(inherited.read_text())
                previous_version = previous[0]['tag_name'].removeprefix('v')
                # Validate every inherited edge even if no route is requested.
                select_route(history, '0.0.0', previous_version, full.stat().st_size, REPOSITORY)
                index['patches'].extend(history['patches'])
            for old in base_apps:
                if version_parts(app_version(old))[:2] == version_parts(target)[:2]:
                    edge = build_one(old, app, assets, full.stat().st_size)
                    if edge:
                        index['patches'].append(edge)
            for release in previous[:limit]:
                base = release['tag_name'].removeprefix('v')
                # Major/minor series changes use the full installer by default.
                if version_parts(base)[:2] != version_parts(target)[:2]:
                    continue
                folder = cache / base
                folder.mkdir()
                old_dmg = fetch_asset(release, release_asset(release,
                    f'ElectricityToolbox-{base}-apple-silicon.dmg'), folder)
                with mounted_app(old_dmg) as old:
                    if app_version(old) != base:
                        raise UpdateError('历史镜像版本与标签不一致')
                    edge = build_one(old, app, assets, full.stat().st_size)
                    if edge:
                        index['patches'].append(edge)
    # No missing intermediate edges: older referenced patches stay at their original releases.
    unique = {(e['base'], e['target']): e for e in index['patches']}
    index['patches'] = list(unique.values())
    select_route(index, '0.0.0', target, full.stat().st_size, REPOSITORY)
    raw = json.dumps(index, ensure_ascii=False, indent=2) + '\n'
    if len(raw.encode()) > 2 * 1024 * 1024:
        raise UpdateError('历史补丁索引过大，请发布完整更新以重设基线')
    (assets / INDEX_NAME).write_text(raw, encoding='utf-8')
    return assets


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('app', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--full-only', action='store_true', help='重大更新仅使用全量包，重设补丁基线')
    parser.add_argument('--base-app', action='append', type=Path, default=[])
    parser.add_argument('--no-fetch-history', action='store_true', help='本地验证使用显式基准应用')
    args = parser.parse_args()
    print(build(args.app, args.output, args.full_only, args.base_app, not args.no_fetch_history))
