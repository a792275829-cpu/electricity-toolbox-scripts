"""Version-bound file deltas; reconstruct only in an isolated application copy."""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import plistlib
import shutil
import stat
import subprocess
import tarfile
import unicodedata
from pathlib import Path, PurePosixPath

from github_update import Cancelled, UpdateError, version_parts, trusted_url

FORMAT = 1
INDEX_NAME = 'update-manifest.json'
MAX_MANIFEST = 32 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_APP_SIZE = 12 * 1024**3


def delta_compatible(base, target):
    """Protocol 1 supports increasing patch releases within one minor baseline.

    Major/minor changes deliberately require a verified full DMG. Keep this
    gate in both producer and consumer so hand-crafted indexes cannot bypass it.
    """
    before, after = version_parts(base), version_parts(target)
    return before[:2] == after[:2] and before < after


def cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('已取消更新')


def digest(path, cancel=None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            cancelled(cancel)
            value.update(chunk)
    return value.hexdigest()


def safe_name(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\0' in name
            or name.startswith('/') or any(p in {'', '.', '..'} for p in name.split('/'))):
        raise UpdateError('补丁包含不安全的路径')
    return PurePosixPath(name)


def validate_inventory(entries):
    if not isinstance(entries, dict) or not entries or len(entries) > MAX_ENTRIES:
        raise UpdateError('补丁文件清单无效')
    seen, size = set(), 0
    for name, item in entries.items():
        safe_name(name)
        key = unicodedata.normalize('NFC', name).casefold()
        if key in seen or not isinstance(item, dict):
            raise UpdateError('补丁包含重复路径')
        seen.add(key)
        kind = item.get('type')
        if kind == 'link':
            target = item.get('target')
            if not isinstance(target, str) or not target or target.startswith('/') or '\\' in target or '\0' in target:
                raise UpdateError('补丁符号链接无效')
            # Resolve lexically, without following untrusted links on disk.
            parts = list(PurePosixPath(name).parent.parts)
            for part in target.split('/'):
                if part == '..':
                    if not parts:
                        raise UpdateError('补丁符号链接越界')
                    parts.pop()
                elif part not in {'', '.'}:
                    parts.append(part)
        elif kind in {'file', 'dir'}:
            if type(item.get('mode')) is not int or item['mode'] & ~0o777:
                raise UpdateError('补丁文件权限无效')
            if kind == 'file':
                if (type(item.get('size')) is not int or item['size'] < 0
                        or not isinstance(item.get('sha256'), str)
                        or len(item['sha256']) != 64
                        or any(c not in '0123456789abcdef' for c in item['sha256'])):
                    raise UpdateError('补丁文件摘要无效')
                size += item['size']
        else:
            raise UpdateError('补丁文件类型无效')
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and entries.get(str(parent), {}).get('type') != 'dir':
                raise UpdateError('补丁父目录无效')
    if size > MAX_APP_SIZE:
        raise UpdateError('补丁还原体积超出限制')


def inventory(root, cancel=None):
    entries = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            cancelled(cancel)
            p = Path(folder) / name
            relative = p.relative_to(root).as_posix()
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode):
                if not p.resolve().is_relative_to(Path(root).resolve()):
                    raise UpdateError('应用符号链接越界')
                item = dict(type='link', target=os.readlink(p))
            elif stat.S_ISDIR(mode):
                item = dict(type='dir', mode=stat.S_IMODE(mode))
            elif stat.S_ISREG(mode):
                item = dict(type='file', mode=stat.S_IMODE(mode), size=p.stat().st_size, sha256=digest(p, cancel))
            else:
                raise UpdateError('应用包含不支持的特殊文件')
            entries[relative] = item
    validate_inventory(entries)
    return entries


def app_version(app):
    info = plistlib.loads((Path(app) / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'com.electricity.toolbox':
        raise UpdateError('补丁应用标识不匹配')
    version = info['CFBundleShortVersionString']
    version_parts(version)
    return version


def verify_app(app, cancel=None):
    cancelled(cancel)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                   check=True, capture_output=True)
    cancelled(cancel)


def create_patch(old, new, installer, output):
    old, new, output = Path(old), Path(new), Path(output)
    base, target = app_version(old), app_version(new)
    if not delta_compatible(base, target):
        raise UpdateError('补丁必须为同主次版本内递增更新；跨版本请使用完整安装包')
    verify_app(old)
    verify_app(new)
    before, after = inventory(old), inventory(new)
    changed = [name for name, entry in after.items() if before.get(name) != entry]
    manifest = dict(format=FORMAT, base=base, target=target, before=before, after=after,
                    installer_sha256=digest(installer))
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    if len(raw) > MAX_MANIFEST:
        raise UpdateError('补丁清单过大')
    import io
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(output, 'w:xz', preset=6, dereference=False) as archive:
            member = tarfile.TarInfo('manifest.json')
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
            archive.add(installer, arcname='installer.py', recursive=False)
            for name in sorted(changed):
                if after[name]['type'] == 'file':
                    archive.add(new / name, arcname='files/' + name, recursive=False)
        return dict(base=base, target=target, name=output.name,
                    size=output.stat().st_size, digest='sha256:' + digest(output))
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def apply_patch(app, patch, installer, expected_base, expected_target, cancel=None):
    """app must be a disposable copy, never the running installation."""
    app, installer = Path(app), Path(installer)
    cancelled(cancel)
    with tarfile.open(patch, 'r:xz') as archive:
        by_name = {}
        for member in archive:
            cancelled(cancel)
            if len(by_name) >= MAX_ENTRIES + 2:
                raise UpdateError('补丁条目过多')
            safe_name(member.name)
            if member.name in by_name or not member.isfile():
                raise UpdateError('补丁压缩包条目无效')
            by_name[member.name] = member
        meta = by_name.get('manifest.json')
        if meta is None or meta.size > MAX_MANIFEST:
            raise UpdateError('补丁缺少有效清单')
        with archive.extractfile(meta) as source:
            manifest = json.loads(source.read(MAX_MANIFEST + 1))
        if (manifest.get('format') != FORMAT or manifest.get('base') != expected_base
                or manifest.get('target') != expected_target
                or app_version(app) != expected_base
                or not delta_compatible(expected_base, expected_target)):
            raise UpdateError('补丁版本不匹配')
        before, after = manifest['before'], manifest['after']
        validate_inventory(before)
        validate_inventory(after)
        if inventory(app, cancel) != before:
            raise UpdateError('旧版应用文件已变化，改用完整安装包')
        changed = {name for name, value in after.items() if before.get(name) != value}
        expected_members = {'manifest.json', 'installer.py'} | {
            'files/' + name for name in changed if after[name]['type'] == 'file'}
        if set(by_name) != expected_members:
            raise UpdateError('补丁文件集合不匹配')
        for name in changed:
            if after[name]['type'] == 'file' and by_name['files/' + name].size != after[name]['size']:
                raise UpdateError('补丁文件大小不匹配')
        # Remove changed entries deepest first, breaking every old symlink before writes.
        for name in sorted(set(before) - set(after) | changed, key=lambda n: (-n.count('/'), n)):
            cancelled(cancel)
            p = app / name
            if p.is_symlink() or p.is_file():
                p.unlink()
            elif p.is_dir() and (name not in after or after[name]['type'] != 'dir'):
                shutil.rmtree(p)
        for name in sorted(after, key=lambda n: (n.count('/'), n)):
            cancelled(cancel)
            item, p = after[name], app / name
            if item['type'] == 'dir':
                p.mkdir(exist_ok=True)
                p.chmod(0o755)  # Final permissions are restored after all writes.
            elif name in changed and item['type'] == 'file':
                with archive.extractfile(by_name['files/' + name]) as src, p.open('wb') as dst:
                    checksum, total = hashlib.sha256(), 0
                    for chunk in iter(lambda: src.read(1024 * 1024), b''):
                        cancelled(cancel)
                        total += len(chunk)
                        checksum.update(chunk)
                        dst.write(chunk)
                if total != item['size'] or checksum.hexdigest() != item['sha256']:
                    raise UpdateError('补丁文件内容校验失败')
                p.chmod(item['mode'])
        # Create links after writing regular files; never traverse them during extraction.
        for name in changed:
            if after[name]['type'] == 'link':
                (app / name).symlink_to(after[name]['target'])
        for name, item in after.items():
            if item['type'] == 'dir':
                (app / name).chmod(item['mode'])
        member = by_name['installer.py']
        if member.size > 1024 * 1024:
            raise UpdateError('安装器大小无效')
        with archive.extractfile(member) as src:
            raw = src.read(1024 * 1024 + 1)
        if hashlib.sha256(raw).hexdigest() != manifest['installer_sha256']:
            raise UpdateError('安装器摘要不匹配')
        installer.write_bytes(raw)
    if app_version(app) != expected_target or inventory(app, cancel) != after:
        raise UpdateError('补丁还原校验失败')
    verify_app(app, cancel)


def select_route(index, current, target, full_size, repository):
    """Smallest increasing-version route, always cheaper than the full installer."""
    if index.get('format') != FORMAT or index.get('version') != target:
        raise UpdateError('增量更新索引版本无效')
    edges = index.get('patches')
    if not isinstance(edges, list) or len(edges) > 2048:
        raise UpdateError('增量更新索引无效')
    graph = {}
    for edge in edges:
        try:
            base, end, size = edge['base'], edge['target'], edge['size']
            name = f'ElectricityToolbox-{base}-to-{end}-apple-silicon.patch.tar.xz'
            if (type(size) is not int or size <= 0 or edge['name'] != name
                    or not trusted_url(edge['browser_download_url'], repository)
                    or not edge['browser_download_url'].endswith('/' + name)
                    or not edge['digest'].startswith('sha256:')
                    or len(edge['digest']) != 71
                    or any(c not in '0123456789abcdef' for c in edge['digest'][7:])
                    or not version_parts(base) < version_parts(end) <= version_parts(target)):
                raise UpdateError('增量更新路径无效')
        except (KeyError, TypeError, AttributeError) as exc:
            raise UpdateError('增量更新路径无效') from exc
        if delta_compatible(base, end):
            graph.setdefault(base, []).append(edge)
    queue, costs, serial = [(0, 0, current, [])], {current: 0}, 0
    while queue:
        cost, _, node, route = heapq.heappop(queue)
        if cost != costs.get(node):
            continue
        if node == target:
            return route
        if len(route) >= 16:
            continue
        for edge in graph.get(node, []):
            new_cost, dest = cost + edge['size'], edge['target']
            if new_cost < min(full_size, costs.get(dest, full_size)):
                costs[dest] = new_cost
                serial += 1
                heapq.heappush(queue, (new_cost, serial, dest, route + [edge]))
    return []
