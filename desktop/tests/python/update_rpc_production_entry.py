"""Opt-in real production-patch RPC test; prepare, verify, then cancel installation.

Never commit installation or change the user's installed application or data.
Published assets are replayed from local files after matching their SHA-256.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱'), str(ROOT / 'desktop/python')]
from delta_update import app_version, digest, inventory
from github_update import GitHubUpdater, REPOSITORY
import service_bridge

folder = Path(sys.argv[1]).resolve()
folder.mkdir(parents=True, exist_ok=True)
os.environ['TOOLBOX_DATA_ROOT'] = str(folder / 'data')
old_app = Path.home() / 'Applications/电力工具箱.app'
assert app_version(old_app) == '0.1.13', 'production test requires an installed v0.1.13'
original = inventory(old_app)
release_folder = ROOT / 'dist/release-v0.1.14'
release = json.loads((release_folder / 'published.json').read_text())
assert release['tag_name'] == 'v0.1.14' and not release['draft']
assets = {item['name']: item for item in release['assets']}
responses = {f'https://api.github.com/repos/{REPOSITORY}/releases/latest': json.dumps(release).encode()}
download_bytes = 0
for name in ('update-manifest.json', 'ElectricityToolbox-0.1.13-to-0.1.14-apple-silicon.patch.tar.xz'):
    path = release_folder / 'updates' / name
    asset = assets[name]
    assert path.stat().st_size == asset['size']
    assert 'sha256:' + digest(path) == asset['digest']
    responses[asset['browser_download_url']] = path.read_bytes()
    download_bytes += asset['size']
full = assets['ElectricityToolbox-0.1.14-apple-silicon.dmg']
reference_dmg = release_folder / full['name']
assert reference_dmg.stat().st_size == full['size']
assert 'sha256:' + digest(reference_dmg) == full['digest']
reference = folder / 'reference-image'
reference.mkdir()
subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
                '-mountpoint', str(reference), str(reference_dmg)], check=True, capture_output=True)


class Response(io.BytesIO):
    status = 200


def opener(request, timeout):
    with (folder / 'requests.jsonl').open('a') as stream:
        stream.write(json.dumps(request.full_url) + '\n')
    return Response(responses[request.full_url])


OriginalService = service_bridge.DesktopService


class ProductionFixtureService(OriginalService):
    def __init__(self, workspace, emit):
        super().__init__(workspace, emit)
        self.updater = GitHubUpdater('0.1.13', opener=opener, app_bundle=old_app)
        self.prepared_directory = None

    def call(self, method, params):
        if method == 'e2e.verify_image':
            assert self.updater.fallback_reason is None, self.updater.fallback_reason
            manager = self.updater.installer
            self.prepared_directory = manager.directory
            app = manager.directory / 'image/电力工具箱.app'
            actual = inventory(app)
            assert actual == inventory(reference / '电力工具箱.app')
            assert inventory(old_app) == original
            assert not (manager.directory / 'commit.json').exists()
            report = dict(base='0.1.13', target=app_version(app), file_entries=len(actual),
                download_bytes=download_bytes, full_bytes=full['size'], full_fallback=False,
                exact_files_modes_links=True, image_and_signature_verified=True,
                automatic_install_ready=True, installed_app_unchanged=True,
                network='local published assets validated against release SHA-256')
            (folder / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            return report
        if method == 'e2e.verify_cleanup':
            assert self.updater.installer is None
            assert self.prepared_directory is not None and not self.prepared_directory.exists()
            assert inventory(old_app) == original
            assert not self.maintenance
            report = json.loads((folder / 'verification.json').read_text())
            report['cancel_cleanup_verified'] = True
            (folder / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            return True
        return super().call(method, params)


service_bridge.DesktopService = ProductionFixtureService
try:
    service_bridge.serve(folder)
finally:
    subprocess.run(['/usr/bin/hdiutil', 'detach', str(reference)], check=True, capture_output=True)
