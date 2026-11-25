from __future__ import annotations
import importlib

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱'), str(ROOT / 'packaging/tauri')]
import prepare_payload
import prepare_workspace
from toolbox.runtime import load_module

SEED = Path('wps自动/wps_excel_to_kdocs_config.defaults.json')
RUNTIME_CONFIG = Path('wps自动/wps_excel_to_kdocs_config.json')


class WpsPackagedConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root / 'payload'
        self.app = self.payload / 'app'
        (self.app / SEED.parent).mkdir(parents=True)
        shutil.copy2(ROOT / SEED, self.app / SEED)
        self.seed = json.loads((ROOT / SEED).read_text())

    def manifest(self, build):
        return {
            'layout': 2, 'build': build,
            'files': {SEED.as_posix(): prepare_workspace.digest(self.app / SEED)},
            'defaults': [p.relative_to(self.payload / 'defaults').as_posix()
                         for p in (self.payload / 'defaults').rglob('*') if p.is_file()],
        }

    def install(self, build):
        (self.payload / 'manifest.json').write_text(json.dumps(self.manifest(build)))
        prepare_workspace.install(
            self.payload, self.root / 'user/app', data_root=self.root / 'user/data',
            state_root=self.root / 'user/state', backup_root=self.root / 'user/backups',
        )

    def test_default_resource_is_exportable_but_local_settings_and_login_are_not(self):
        self.assertTrue(prepare_payload.runtime_source(SEED))
        self.assertFalse(prepare_payload.runtime_source(RUNTIME_CONFIG))
        for name in ('wps-browser-profile/Default/Cookies', 'wps-chrome.pid', 'wps-chrome-state.json'):
            self.assertFalse(prepare_payload.runtime_source(Path('wps自动') / name))
        self.assertEqual(len(self.seed['configs']), 3)
        self.assertEqual([len(item['regions']) for item in self.seed['configs']], [8, 15, 16])
        self.assertEqual(self.seed['recent_urls'], [])
        self.assertTrue(self.seed['overwrite'])
        self.assertEqual(set(self.seed), {'recent_urls', 'browser_mode', 'overwrite', 'cdp_url', 'configs'})
        for config in self.seed['configs']:
            for field in ('local_file', 'source_url', 'kdocs_url'):
                self.assertEqual(config[field], '')
            self.assertEqual(set(config), {
                'name', 'source_type', 'source_url', 'kdocs_url', 'local_file', 'regions',
            })
            for region in config['regions']:
                self.assertEqual(set(region), {
                    'source_sheet', 'source_start', 'source_end',
                    'target_sheet', 'target_start', 'target_end',
                })

    def test_fresh_install_receives_all_regions_and_updates_preserve_user_config(self):
        prepare_payload.prepare_defaults(self.app, self.payload / 'defaults')
        self.assertEqual(json.loads((self.payload / 'defaults' / RUNTIME_CONFIG).read_text()), self.seed)
        self.install('first')
        runtime = self.root / 'user/data' / RUNTIME_CONFIG
        self.assertEqual(json.loads(runtime.read_text()), self.seed)
        self.assertFalse((self.root / 'user/app' / RUNTIME_CONFIG).exists())
        custom = {'configs': [], 'custom': '保留用户设置'}
        runtime.write_text(json.dumps(custom))
        self.install('updated')
        self.assertEqual(json.loads(runtime.read_text()), custom)
        self.assertFalse(any((self.root / 'user').rglob('wps-browser-profile')))

    def test_source_mode_uses_defaults_without_writing_over_user_settings(self):
        from toolbox.service import ToolboxService
        from toolbox_shared.wps_config import normalize_runtime_config

        module = importlib.import_module('toolbox_shared.wps_service')
        data = self.root / 'local'
        runtime = data / RUNTIME_CONFIG
        with mock.patch.dict(os.environ, TOOLBOX_DATA_ROOT=str(data), TOOLBOX_CODE_ROOT=str(ROOT)):
            service = ToolboxService(ROOT)
            with mock.patch.object(module, 'CONFIG_PATH', runtime):
                self.assertEqual(module.load_config(), self.seed)
                self.assertEqual(service.call('wps.get', {}), normalize_runtime_config(self.seed))
                self.assertFalse(runtime.exists())
                runtime.parent.mkdir(parents=True)
                for content, expected in (('{"configs":[]}', {'configs': []}), ('invalid json', {}), ('[]', {})):
                    with self.subTest(content=content):
                        runtime.write_text(content, encoding='utf-8')
                        self.assertEqual(module.load_config(), expected)
                        self.assertEqual(service.call('wps.get', {}), normalize_runtime_config(expected))
                        self.assertEqual(runtime.read_text(encoding='utf-8'), content)

    def test_missing_default_resource_fails_packaging(self):
        (self.app / SEED).unlink()
        with self.assertRaises(FileNotFoundError):
            prepare_payload.prepare_defaults(self.app, self.payload / 'defaults')


if __name__ == '__main__':
    unittest.main()
