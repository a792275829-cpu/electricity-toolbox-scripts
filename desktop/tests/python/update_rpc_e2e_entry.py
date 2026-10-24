"""Isolated signed-app update fixture for the real Rust Backend.request path.

Only release HTTP responses and the installation destination are substituted.
Delta reconstruction, image creation, verification, handoff and launch are real.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱'), str(ROOT / 'desktop/python'),
               str(ROOT / 'desktop/tests/python')]
from delta_update import create_patch, inventory
from github_update import GitHubUpdater, REPOSITORY
from test_auto_install import RealAutomaticInstallTests
import auto_install
import service_bridge

folder = Path(sys.argv[1]).resolve()
target = folder / 'Applications/电力工具箱.app'
expected = folder / 'release/电力工具箱.app'


def verify_result():
    result = json.loads((folder / 'state/update-result.json').read_text())
    assert result['success'], result
    assert inventory(target) == inventory(expected)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(target)],
                   check=True, capture_output=True)
    assert (expected.parent / '0.1.14.launched').read_text() == '0.1.14'
    assert (folder / 'data/config.json').read_text() == 'business sentinel'
    assert not list(target.parent.glob('.toolbox-install-*'))
    report = dict(base='0.1.13', target='0.1.14', installed=True, relaunched=True,
                  exact_inventory=True, codesign=True, data_preserved=True,
                  transport='real Rust Backend.request / Python JSON-lines service',
                  network='isolated release responses, real signed application fixture')
    (folder / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if '--verify' in sys.argv:
    verify_result()
    sys.exit()

folder.mkdir(parents=True, exist_ok=True)
fixture = RealAutomaticInstallTests()
fixture.bundle(target, '0.1.13')
fixture.bundle(expected, '0.1.14')
(folder / 'data').mkdir()
(folder / 'data/config.json').write_text('business sentinel')
os.environ['TOOLBOX_DATA_ROOT'] = str(folder / 'data')

# Confine installation to this test and ignore unrelated running real apps.
installer = folder / 'installer.py'
installer.write_text((ROOT / 'packaging/tauri/install_user_app.py').read_text() +
    '\nensure_stopped = lambda: None\n_original_install = install\n'
    'def install(source, **kwargs):\n'
    f'    return _original_install(source, Path({str(target)!r}), **kwargs)\n')
patch_file = folder / 'ElectricityToolbox-0.1.13-to-0.1.14-apple-silicon.patch.tar.xz'
edge = create_patch(target, expected, installer, patch_file)
prefix = f'https://github.com/{REPOSITORY}/releases/download/v0.1.14/'
edge['browser_download_url'] = prefix + patch_file.name
index = json.dumps(dict(format=1, version='0.1.14', patches=[edge])).encode()


def asset(name, raw):
    import hashlib
    return dict(name=name, size=len(raw), digest='sha256:' + hashlib.sha256(raw).hexdigest(),
                browser_download_url=prefix + name)


full = asset('ElectricityToolbox-0.1.14-apple-silicon.dmg', b'F' * 1_000_000)
index_asset = asset('update-manifest.json', index)
release = dict(tag_name='v0.1.14', draft=False, prerelease=False,
               html_url=f'https://github.com/{REPOSITORY}/releases/tag/v0.1.14',
               body='RPC update end-to-end regression', assets=[full, index_asset, edge])
responses = {f'https://api.github.com/repos/{REPOSITORY}/releases/latest': json.dumps(release).encode(),
             index_asset['browser_download_url']: index, edge['browser_download_url']: patch_file.read_bytes()}


class Response(io.BytesIO):
    status = 200


def opener(request, timeout):
    # A full-package fallback must fail the test, rather than hide a delta failure.
    with (folder / 'requests.jsonl').open('a') as stream:
        stream.write(json.dumps(request.full_url) + '\n')
    return Response(responses[request.full_url])


# The manager under test is Rust; only the service child needs to exit before
# the independent installer proceeds. Never wait for the test runner itself.
OriginalAutoInstall = auto_install.AutoInstall
auto_install.AutoInstall = lambda app: OriginalAutoInstall(app, parent_pids=[os.getpid()])

# Optional historical builder used for the before/after regression run.
historical = os.environ.get('TOOLBOX_E2E_IMAGE_BUILDER')
if historical:
    spec = importlib.util.spec_from_file_location('installer_image', historical)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules['installer_image'] = module

OriginalService = service_bridge.DesktopService


class FixtureService(OriginalService):
    def __init__(self, workspace, emit):
        super().__init__(workspace, emit)
        self.updater = GitHubUpdater('0.1.13', opener=opener, app_bundle=target)


service_bridge.DesktopService = FixtureService
service_bridge.serve(folder)
