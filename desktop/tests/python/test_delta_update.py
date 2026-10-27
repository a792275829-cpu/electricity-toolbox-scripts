from __future__ import annotations
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'desktop/python'), str(ROOT / 'packaging/tauri')]
from delta_update import (apply_patch, app_version, create_patch, digest, inventory,
                          select_route, validate_inventory, INDEX_NAME)
from github_update import GitHubUpdater, Release, UpdateError, Cancelled, REPOSITORY
from validate_installer import validate
from prepare_payload import runtime_source


def asset(name, raw, version='1.0.3'):
    return dict(name=name, size=len(raw), digest='sha256:' + hashlib.sha256(raw).hexdigest(),
                browser_download_url=f'https://github.com/{REPOSITORY}/releases/download/v{version}/{name}')


def edge(base, target, size):
    name = f'ElectricityToolbox-{base}-to-{target}-apple-silicon.patch.tar.xz'
    return dict(asset(name, b'', target), base=base, target=target, size=size)


class Response(io.BytesIO):
    status = 200


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.installer = self.root / 'installer.py'
        self.installer.write_text('print("installer fixture")\n')
        self.mock_verify = patch('delta_update.verify_app')
        self.mock_verify.start()
        self.addCleanup(self.mock_verify.stop)
        self.addCleanup(self.temp.cleanup)

    def app(self, version):
        app = self.root / version / '电力工具箱.app'
        (app / 'Contents/Resources').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier='com.electricity.toolbox', CFBundleShortVersionString=version)))
        (app / 'Contents/Resources/source.py').write_text(f'VERSION = "{version}"\n')
        (app / 'Contents/Resources/stable.bin').write_bytes(b'stable runtime' * 100)
        return app

    def patch_for(self, old, new):
        output = self.root / f'{app_version(old)}-{app_version(new)}.tar.xz'
        result = create_patch(old, new, self.installer, output)
        return output, result

    def clone(self, app, name='restored.app'):
        target = self.root / name
        shutil.copytree(app, target, symlinks=True)
        return target

    def test_three_previous_versions_restore_to_current_fixture(self):
        # Synthetic applications exercise the compatibility protocol without
        # downloading historical production installers or modifying user data.
        new = self.app('0.1.21')
        for base in ('0.1.18', '0.1.19', '0.1.20'):
            with self.subTest(base=base):
                old = self.app(base)
                archive, _ = self.patch_for(old, new)
                restored = self.clone(old, base + '.app')
                apply_patch(restored, archive, self.root/'restored-installer.py', base, '0.1.21')
                self.assertEqual(inventory(restored), inventory(new))
                self.assertEqual((self.root/'restored-installer.py').read_bytes(), self.installer.read_bytes())

    def test_major_minor_and_unknown_protocol_require_full_dmg(self):
        old, new = self.app('0.1.21'), self.app('0.2.0')
        with self.assertRaises(UpdateError):
            self.patch_for(old, new)
        for target in ('0.2.0', '1.0.0'):
            index = dict(format=1, version=target, patches=[edge('0.1.21', target, 10)])
            self.assertEqual(select_route(index, '0.1.21', target, 100, REPOSITORY), [])
        with self.assertRaises(UpdateError):
            select_route(dict(format=2, version='0.1.22', patches=[]), '0.1.21', '0.1.22', 100, REPOSITORY)

    def test_roundtrip_files_links_deletions_modes_and_type_changes(self):
        old, new = self.app('1.0.1'), self.app('1.0.2')
        (old / 'Contents/remove').write_text('remove')
        (old / 'Contents/switch').write_text('was a file')
        (new / 'Contents/switch').mkdir()
        (new / 'Contents/switch/child').write_text('new child')
        (new / 'Contents/empty').mkdir()
        (new / 'Contents/Resources/link').symlink_to('source.py')
        (new / 'Contents/Resources/source.py').chmod(0o755)
        archive, _ = self.patch_for(old, new)
        restored = self.clone(old)
        apply_patch(restored, archive, self.root / 'output-installer.py', '1.0.1', '1.0.2')
        self.assertEqual(inventory(restored), inventory(new))
        self.assertEqual((self.root / 'output-installer.py').read_bytes(), self.installer.read_bytes())
        self.assertEqual(app_version(old), '1.0.1')
        self.assertTrue((old / 'Contents/remove').exists())

    def test_unchanged_directory_to_file_and_old_symlink_to_directory(self):
        old, new = self.app('1.0.1'), self.app('1.0.2')
        (old / 'Contents/switch').mkdir()
        (old / 'Contents/switch/nested').write_text('old')
        (new / 'Contents/switch').write_text('new')
        (old / 'Contents/linked').symlink_to('Resources')
        (new / 'Contents/linked').mkdir()
        (new / 'Contents/linked/file').write_text('safe')
        archive, _ = self.patch_for(old, new)
        restored = self.clone(old)
        apply_patch(restored, archive, self.root / 'install-copy.py', '1.0.1', '1.0.2')
        self.assertEqual(inventory(restored), inventory(new))

    def test_wrong_base_modified_app_and_cancellation_are_rejected(self):
        old, new = self.app('1.0.1'), self.app('1.0.2')
        archive, _ = self.patch_for(old, new)
        with self.assertRaises(UpdateError):
            apply_patch(self.clone(old, 'wrong.app'), archive, self.root / 'i.py', '1.0.0', '1.0.2')
        restored = self.clone(old)
        (restored / 'Contents/Resources/source.py').write_text('local edit')
        with self.assertRaisesRegex(UpdateError, '变化'):
            apply_patch(restored, archive, self.root / 'i.py', '1.0.1', '1.0.2')
        cancel = threading.Event();cancel.set()
        with self.assertRaises(Cancelled):
            apply_patch(old, archive, self.root / 'i.py', '1.0.1', '1.0.2', cancel)

    def test_archive_traversal_duplicate_extra_and_payload_tampering_rejected(self):
        old, new = self.app('1.0.1'), self.app('1.0.2')
        original, _ = self.patch_for(old, new)
        for mode in ('traversal', 'duplicate', 'extra', 'tamper', 'link'):
            bad = self.root / (mode + '.xz')
            with tarfile.open(original, 'r:xz') as src, tarfile.open(bad, 'w:xz') as dst:
                for member in src:
                    raw = src.extractfile(member).read()
                    if mode == 'tamper' and member.name.endswith('source.py'):
                        raw = b'x' * len(raw)
                    dst.addfile(member, io.BytesIO(raw))
                    if mode == 'duplicate' and member.name == 'manifest.json':
                        dst.addfile(member, io.BytesIO(raw))
                if mode in ('traversal', 'extra', 'link'):
                    m = tarfile.TarInfo('../outside' if mode == 'traversal' else 'extra')
                    if mode == 'link':m.type=tarfile.SYMTYPE;m.linkname='/tmp/outside'
                    dst.addfile(m, io.BytesIO(b''))
            with self.subTest(mode=mode), self.assertRaises(UpdateError):
                apply_patch(self.clone(old, mode + '.app'), bad, self.root / 'i.py', '1.0.1', '1.0.2')
        self.assertFalse((self.root / 'outside').exists())

    def test_inventory_rejects_external_links_case_collisions_and_link_parents(self):
        app = self.app('1.0.1')
        (app / 'Contents/escape').symlink_to('../../outside')
        with self.assertRaises(UpdateError):inventory(app)
        for entries in ({'../escape': dict(type='dir', mode=0o755)},
                        {'A': dict(type='dir', mode=0o755), 'a': dict(type='dir', mode=0o755)},
                        {'a':dict(type='link',target='b'),'a/x':dict(type='dir',mode=0o755)}):
            with self.assertRaises(UpdateError):validate_inventory(entries)

    def test_route_chooses_smallest_direct_or_chain_and_full_when_cheaper(self):
        index = dict(format=1, version='1.0.3', patches=[
            edge('1.0.1','1.0.2',10), edge('1.0.2','1.0.3',20),edge('1.0.1','1.0.3',50)])
        route = select_route(index,'1.0.1','1.0.3',100,REPOSITORY)
        self.assertEqual([e['target'] for e in route],['1.0.2','1.0.3'])
        index['patches'][-1]['size']=25
        self.assertEqual(len(select_route(index,'1.0.1','1.0.3',100,REPOSITORY)),1)
        self.assertEqual(select_route(index,'1.0.1','1.0.3',20,REPOSITORY),[])
        self.assertEqual(select_route(index,'0.9.0','1.0.3',100,REPOSITORY),[])
        index['patches'][0]['target']='1.0.0'
        with self.assertRaises(UpdateError):select_route(index,'1.0.1','1.0.3',100,REPOSITORY)

    def download_fixture(self, corrupt=False, modify=False, cancel=None):
        old, middle, new = self.app('1.0.1'), self.app('1.0.2'), self.app('1.0.3')
        patches = [self.patch_for(old,middle),self.patch_for(middle,new)]
        index = dict(format=1,version='1.0.3',patches=[])
        responses = {}
        for path, row in patches:
            row['name']=f"ElectricityToolbox-{row['base']}-to-{row['target']}-apple-silicon.patch.tar.xz"
            row['browser_download_url']=f"https://github.com/{REPOSITORY}/releases/download/v{row['target']}/{row['name']}"
            index['patches'].append(row)
            responses[row['browser_download_url']]=path.read_bytes() if not corrupt else b'corrupted'
        raw = json.dumps(index).encode();index_asset=asset(INDEX_NAME,raw)
        full=asset('full.dmg',b'F'*100_000)
        responses[index_asset['browser_download_url']]=raw
        responses[full['browser_download_url']]=b'F'*100_000
        requests=[]
        def opener(request,timeout):
            requests.append(request.full_url)
            return Response(responses[request.full_url])
        updater=GitHubUpdater('1.0.1',opener=opener,app_bundle=old)
        if modify:(old/'Contents/Resources/source.py').write_text('local edit')
        candidate=Release('v1.0.3','notes','page',full,index_asset)
        def build(app,out,installer):
            self.assertEqual(inventory(app),inventory(new))
            p=out/'reconstructed.dmg';p.write_bytes(b'local image');return p
        with patch('installer_image.build',side_effect=build):
            result=updater.download(candidate,cancel or threading.Event(),lambda _:None)
        self.addCleanup(shutil.rmtree,result.parent,True)
        return result,updater,requests,full

    def test_download_chain_reconstructs_and_never_fetches_full(self):
        result,updater,requests,full=self.download_fixture()
        self.assertEqual(result.read_bytes(),b'local image')
        self.assertNotIn(full['browser_download_url'],requests)
        self.assertIsNone(updater.fallback_reason)

    def test_bad_patch_or_local_changes_fall_back_to_verified_full(self):
        for corrupt,modify in ((True,False),(False,True)):
            with self.subTest(corrupt=corrupt,modify=modify):
                # Each fixture uses unique directories.
                with tempfile.TemporaryDirectory() as tmp:
                    original=self.root;self.root=Path(tmp);self.installer=self.root/'installer.py';self.installer.write_text('pass')
                    try:
                        result,updater,requests,full=self.download_fixture(corrupt,modify)
                        self.assertEqual(result.read_bytes(),b'F'*100_000)
                        self.assertIn(full['browser_download_url'],requests)
                        self.assertIsNotNone(updater.fallback_reason)
                    finally:self.root=original

    def test_cancel_never_falls_back_to_full(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(Cancelled):self.download_fixture(cancel=cancel)

    def test_runtime_export_contains_updater_and_image_builder(self):
        for name in ('delta_update.py','installer_image.py'):
            self.assertTrue(runtime_source(Path('desktop/python')/name))

    def test_local_release_builder_verifies_patch_and_full_only_resets_routes(self):
        from build_updates import build
        old,new=self.app('1.0.1'),self.app('1.0.3')
        output=self.root/'release';output.mkdir()
        (output/'ElectricityToolbox-1.0.3-apple-silicon.dmg').write_bytes(b'F'*100_000)
        with patch('build_updates.verify_app'):
            folder=build(new,output,base_apps=[old],fetch_history=False)
            index=json.loads((folder/INDEX_NAME).read_text())
            self.assertEqual([(e['base'],e['target']) for e in index['patches']],[('1.0.1','1.0.3')])
            self.assertEqual(len(list(folder.glob('*.patch.tar.xz'))),1)
            folder=build(new,output,full_only=True,fetch_history=False)
            self.assertEqual(json.loads((folder/INDEX_NAME).read_text())['patches'],[])
            self.assertFalse(list(folder.glob('*.patch.tar.xz')))

    def test_release_builder_carries_history_for_older_versions(self):
        from build_updates import build
        from contextlib import nullcontext
        middle,new=self.app('1.0.2'),self.app('1.0.3')
        output=self.root/'release';output.mkdir()
        (output/'ElectricityToolbox-1.0.3-apple-silicon.dmg').write_bytes(b'F'*100_000)
        history=dict(format=1,version='1.0.2',patches=[edge('1.0.1','1.0.2',10)])
        raw=json.dumps(history).encode();index_file=self.root/INDEX_NAME;index_file.write_bytes(raw)
        prior=dict(tag_name='v1.0.2',draft=False,prerelease=False,assets=[
            asset(INDEX_NAME,raw,'1.0.2'),asset('ElectricityToolbox-1.0.2-apple-silicon.dmg',b'old','1.0.2')])
        def fetch(release,row,directory):
            return index_file if row['name']==INDEX_NAME else self.root/'old.dmg'
        with patch('build_updates.verify_app'),patch('build_updates.gh_json',return_value=[prior]), \
             patch('build_updates.fetch_asset',side_effect=fetch), \
             patch('build_updates.mounted_app',return_value=nullcontext(middle)):
            folder=build(new,output)
        index=json.loads((folder/INDEX_NAME).read_text())
        self.assertEqual([e['target'] for e in select_route(index,'1.0.1','1.0.3',100_000,REPOSITORY)],
                         ['1.0.2','1.0.3'])

    def test_draft_temporary_urls_are_valid_only_before_publication(self):
        e=edge('1.0.1','1.0.3',10)
        raw=json.dumps(dict(format=1,version='1.0.3',patches=[e])).encode()
        index_file=self.root/INDEX_NAME;index_file.write_bytes(raw)
        full=asset('ElectricityToolbox-1.0.3-apple-silicon.dmg',b'F'*100)
        patch_asset=dict(e,browser_download_url=f"https://github.com/{REPOSITORY}/releases/download/untagged-123abc/{e['name']}")
        data=dict(tag_name='v1.0.3',draft=True,assets=[full,dict(name=full['name']+'.sha256'),asset(INDEX_NAME,raw),patch_asset])
        validate(data,index_file)
        data['draft']=False
        with self.assertRaises(ValueError):validate(data,index_file)
        data['draft']=True;patch_asset['size']+=1
        with self.assertRaises(ValueError):validate(data,index_file)
        patch_asset['size']-=1;patch_asset['browser_download_url']+='wrong'
        with self.assertRaises(ValueError):validate(data,index_file)

    def test_publication_binds_index_and_patch_to_github_assets(self):
        e=edge('1.0.1','1.0.3',10)
        raw=json.dumps(dict(format=1,version='1.0.3',patches=[e])).encode()
        index_file=self.root/INDEX_NAME;index_file.write_bytes(raw)
        full=asset('ElectricityToolbox-1.0.3-apple-silicon.dmg',b'F'*100)
        data=dict(tag_name='v1.0.3',assets=[full,dict(name=full['name']+'.sha256'),asset(INDEX_NAME,raw),e])
        validate(data,index_file)
        bad=copy.deepcopy(data);bad['assets'][-1]['size']+=1
        with self.assertRaises(ValueError):validate(bad,index_file)
        index_file.write_text('{}')
        with self.assertRaises(ValueError):validate(data,index_file)

    def test_history_cache_requires_exact_remote_size_and_digest(self):
        from build_updates import fetch_asset
        cache = self.root / 'dist'
        cache.mkdir()
        row = asset('historical.dmg', b'valid history', '1.0.2')
        cached = cache / row['name']
        cached.write_bytes(b'valid history')
        target = self.root / 'download'
        target.mkdir()
        release = dict(tag_name='v1.0.2')
        with patch('build_updates.ROOT', self.root), patch('build_updates.subprocess.run') as download:
            result = fetch_asset(release, row, target)
            self.assertEqual(result.read_bytes(), b'valid history')
            download.assert_not_called()
            result.unlink()
            for invalid in (b'wrong history', b'short'):
                cached.write_bytes(invalid)
                def fetch(*args, **kwargs):
                    (target / row['name']).write_bytes(b'valid history')
                download.side_effect = fetch
                result = fetch_asset(release, row, target)
                self.assertEqual(result.read_bytes(), b'valid history')
                result.unlink()
            self.assertEqual(download.call_count, 2)


if __name__=='__main__':unittest.main()
