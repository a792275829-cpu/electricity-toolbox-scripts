"""Exercise automatic handoff using an isolated signed app and real DMG."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'desktop/python'), str(ROOT / 'packaging/tauri'), str(ROOT), str(ROOT / '电力工具箱')]
from auto_install import AutoInstall, app_version, run
from github_update import GitHubUpdater, Release
from service_bridge import DesktopService
from prepare_payload import runtime_source


class AutoInstallGuards(unittest.TestCase):
    def test_helper_failure_keeps_old_app_and_reopens_it_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'old.app'
            old.mkdir()
            (old / 'sentinel').write_text('old app')
            result = root / 'result.json'
            request = dict(version='0.2.0', old_app=str(old), mount=str(root / 'image'), parents=[], result=str(result))
            (root / 'request.json').write_text(json.dumps(request))
            (root / 'commit.json').write_text('{}')
            (root / 'installer.py').write_text('def install(source):\n    raise OSError("copy failed")\n')
            with patch('auto_install.verify'), patch('auto_install.subprocess.run') as command, patch('auto_install.os.execv') as detach:
                run(root)
            self.assertFalse(json.loads(result.read_text())['success'])
            self.assertIn('copy failed', json.loads(result.read_text())['error'])
            self.assertEqual((old / 'sentinel').read_text(), 'old app')
            self.assertEqual(command.call_args_list[0].args[0], ['/usr/bin/open', '-n', str(old)])
            detach.assert_called_once()

    def test_prepare_retry_reuses_the_accepted_helper(self):
        service = DesktopService(ROOT, lambda _: None)
        service.maintenance = True
        service.updater.downloaded = ('v0.2.0', Path('/verified.dmg'), 'checksum')
        params = dict(version='v0.2.0', path='/verified.dmg')
        with patch.object(service.updater, 'prepare_install', return_value=dict(ready=True)) as prepare:
            task = service.call('updates.prepare_install', params)
            deadline = time.monotonic() + 3
            while service.registry.has_running_tasks() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(service.call('updates.prepare_install', params), task)
            prepare.assert_called_once()
            with self.assertRaisesRegex(ValueError, '不一致'):
                service.call('updates.prepare_install', dict(version='v9.0.0', path='/other.dmg'))

    def test_only_the_downloaded_release_and_path_can_be_installed(self):
        updater = GitHubUpdater('0.1.0', app_bundle=Path('/unused.app'))
        release = Release('v0.2.0', '', '', None)
        with self.assertRaisesRegex(ValueError, '未经过'):
            updater.prepare_install(release, Path('/unverified.dmg'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'update.dmg'
            path.write_bytes(b'verified')
            updater.remember_download(release, path)
            path.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError, '已变化'):
                updater.prepare_install(release, path)
        self.assertTrue(runtime_source(Path('desktop/python/auto_install.py')))

    def test_backend_reservation_blocks_business_and_abort_releases_it(self):
        service = DesktopService(ROOT, lambda _: None)
        with patch.object(service.updater, 'prepare_update'), patch.object(service.updater, 'abort_update'):
            self.assertTrue(service.call('updates.reserve', {}))
            with self.assertRaisesRegex(ValueError, '正在更新'):
                service.call('start', {})
            with self.assertRaisesRegex(ValueError, '先下载'):
                service.call('updates.prepare_install', {} )
            self.assertTrue(service.call('updates.abort', {}))
            self.assertFalse(service.maintenance)
            with self.assertRaisesRegex(ValueError, '未经过'):
                service.updater.prepare_install(Release('v0.2.0', '', '', None), Path('/other.dmg'))


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('codesign'), 'macOS required')
class RealAutomaticInstallTests(unittest.TestCase):
    def bundle(self, app, version):
        contents = app / 'Contents'
        (contents / 'MacOS').mkdir(parents=True)
        (contents / 'Resources').mkdir()
        (contents / 'Info.plist').write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier='com.electricity.toolbox', CFBundleShortVersionString=version,
            CFBundleExecutable='electricity-toolbox', CFBundlePackageType='APPL')))
        executable = contents / 'MacOS/electricity-toolbox'
        shutil.copyfile('/usr/bin/true', executable)
        executable.chmod(0o755)
        python = contents / 'Resources/payload/runtime/python/bin/python3'
        python.parent.mkdir(parents=True)
        # The isolated fixture borrows this test interpreter, never an installed app.
        python.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o755)
        (contents / 'Resources/version.txt').write_text(version)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)

    def test_dmg_handoff_waits_for_old_process_then_installs_and_relaunches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'Applications/电力工具箱.app'
            self.bundle(target, '0.1.0')
            image = root / 'image'
            self.bundle(image / '电力工具箱.app', '0.2.0')
            # Only ignore unrelated real apps in this temporary installer fixture.
            installer = (ROOT / 'packaging/tauri/install_user_app.py').read_text()
            (image / '安装到用户应用程序.py').write_text(installer + '\nensure_stopped = lambda: None\n')
            dmg = root / 'update.dmg'
            subprocess.run(['/usr/bin/hdiutil', 'create', '-ov', '-volname', 'ToolboxAutoUpdateTest',
                '-srcfolder', str(image), '-format', 'UDZO', str(dmg)], check=True, capture_output=True)
            with dmg.open('rb') as stream:
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            data = root / 'data'
            data.mkdir()
            (data / 'config.json').write_text('business sentinel')
            old_process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            manager = AutoInstall(target, parent_pids=[old_process.pid])
            try:
                with patch.dict(os.environ, {'HOME': str(root), 'TOOLBOX_DATA_ROOT': str(data)}):
                    self.assertEqual(manager.prepare(dmg, checksum, '0.2.0'), dict(ready=True))
                self.assertEqual(app_version(target), '0.1.0')
                manager.commit()
                time.sleep(.2)
                self.assertEqual(app_version(target), '0.1.0', 'must wait for the old process')
                old_process.terminate()
                old_process.wait(timeout=5)
                manager.process.wait(timeout=30)
                result = json.loads((root / 'state/update-result.json').read_text())
                self.assertTrue(result['success'], result)
                self.assertEqual(app_version(target), '0.2.0')
                self.assertEqual((data / 'config.json').read_text(), 'business sentinel')
                self.assertEqual(list(target.parent.glob('.toolbox-install-*')), [])
                self.assertEqual(manager.process.returncode, 0, (manager.directory / 'install.log').read_text())
            finally:
                if old_process.poll() is None:
                    old_process.kill()
                    old_process.wait(timeout=5)
                manager.abort()
