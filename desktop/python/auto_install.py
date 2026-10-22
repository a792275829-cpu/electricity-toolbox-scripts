"""Prepare an authenticated DMG, then hand installation to an independent process."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

APP_NAME = "电力工具箱.app"


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def app_version(app):
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'com.electricity.toolbox':
        raise ValueError('更新应用标识不匹配')
    return info['CFBundleShortVersionString']


def verify(app, version):
    if app_version(app) != version:
        raise ValueError('更新应用版本不匹配')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                   check=True, capture_output=True)


class AutoInstall:
    def __init__(self, old_app, *, parent_pids=None):
        self.old_app = Path(old_app).resolve()
        self.parent_pids = [os.getpid(), os.getppid()] if parent_pids is None else list(parent_pids)
        self.directory = None
        self.process = None

    def prepare(self, dmg, checksum, version):
        if self.directory is not None:
            raise ValueError('已有更新正在准备安装')
        with Path(dmg).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                raise ValueError('安装镜像已变化，请重新下载')
        self.directory = Path(tempfile.mkdtemp(prefix='ElectricityToolboxInstall-'))
        mount = self.directory / 'image'
        mount.mkdir()
        try:
            subprocess.run(['/usr/bin/hdiutil', 'verify', str(dmg)], check=True, capture_output=True)
            subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
                            '-mountpoint', str(mount), str(dmg)], check=True, capture_output=True)
            app = mount / APP_NAME
            verify(app, version)
            python = app / 'Contents/Resources/payload/runtime/python/bin/python3'
            installer = mount / '安装到用户应用程序.py'
            if not python.is_file() or not installer.is_file():
                raise ValueError('镜像缺少内置运行环境或安装器')
            # The helper and request survive the old application's removal.
            helper = self.directory / 'auto_install.py'
            shutil.copy2(__file__, helper)
            shutil.copy2(installer, self.directory / 'installer.py')
            data = os.environ.get('TOOLBOX_DATA_ROOT')
            result = Path(data).parent / 'state/update-result.json' if data else self.directory / 'result.json'
            result.parent.mkdir(parents=True, exist_ok=True)
            request = dict(version=version, old_app=str(self.old_app), mount=str(mount),
                           parents=self.parent_pids, result=str(result))
            write_json(self.directory / 'request.json', request)
            env = os.environ.copy()
            for key in ('PYTHONHOME', 'PYTHONPATH'):
                env.pop(key, None)
            with (self.directory / 'install.log').open('ab') as log:
                self.process = subprocess.Popen([str(python), '-I', '-B', str(helper), '--run', str(self.directory)],
                    cwd=self.directory, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True, close_fds=True)
            deadline = time.monotonic() + 30
            while not (self.directory / 'ready.json').exists():
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f'自动安装进程未就绪，日志：{self.directory / "install.log"}')
                time.sleep(.05)
            return dict(ready=True)
        except BaseException:
            self.abort()
            raise

    def commit(self):
        if (self.directory is None or self.process is None or self.process.poll() is not None
                or not (self.directory / 'ready.json').is_file()):
            raise ValueError('自动安装进程未就绪，请重试更新')
        write_json(self.directory / 'commit.json', {})
        return True

    def abort(self):
        if self.directory is None:
            return
        write_json(self.directory / 'cancel.json', {})
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Before commit the helper only waits; it has not touched the installation.
                if not (self.directory / 'commit.json').exists():
                    self.process.terminate()
                    self.process.wait(timeout=5)
                else:
                    return
        subprocess.run(['/usr/bin/hdiutil', 'detach', str(self.directory / 'image')], capture_output=True)
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory = None
        self.process = None


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def run(directory):
    request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
    mount = Path(request['mount'])
    try:
        spec = importlib.util.spec_from_file_location('update_installer', directory / 'installer.py')
        installer = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = installer
        spec.loader.exec_module(installer)
        source = mount / APP_NAME
        verify(source, request['version'])
        write_json(directory / 'ready.json', {})
        deadline = time.monotonic() + 300
        while True:
            if (directory / 'cancel.json').exists():
                return
            committed = (directory / 'commit.json').exists()
            if committed and not any(alive(pid) for pid in request['parents']):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('等待工具箱正常退出超时，原应用未改动')
            time.sleep(.1)
        # Reuse the image's verified installer, including its staging and rollback.
        target = installer.install(source)
        verify(target, request['version'])
        subprocess.run(['/usr/bin/open', '-n', str(target)], check=True)
        write_json(Path(request['result']), dict(version=request['version'], success=True))
    except Exception as exc:
        print(f'自动更新失败：{exc}', flush=True)
        write_json(Path(request['result']), dict(version=request['version'], success=False, error=str(exc),
                                               log=str(directory / 'install.log')))
        old = Path(request['old_app'])
        if old.is_dir() and not any(alive(pid) for pid in request['parents']):
            subprocess.run(['/usr/bin/open', '-n', str(old)], check=False)
        subprocess.run(['/usr/bin/osascript', '-e',
            'display notification ' + json.dumps(str(exc), ensure_ascii=False) + ' with title "电力工具箱更新失败"'],
            capture_output=True)
    # exec releases the interpreter and libraries loaded from the mounted image
    # before hdiutil unmounts it. Keep the request/log for failure diagnostics.
    os.execv('/usr/bin/hdiutil', ['hdiutil', 'detach', str(mount)])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--run':
        run(Path(sys.argv[2]))
