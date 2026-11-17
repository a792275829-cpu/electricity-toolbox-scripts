"""React adapters use the original service and persistence without a Tk interpreter."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱')]
from toolbox.service import ToolboxService
from toolbox_shared.account_verifier import AccountVerifier


class WebUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name).resolve()
        self.environment = patch.dict(os.environ, TOOLBOX_DATA_ROOT=str(self.data), TOOLBOX_CODE_ROOT=str(ROOT))
        self.environment.start()
        self.service = ToolboxService(ROOT)
    def tearDown(self):
        self.service.call('shutdown', {'cancel': True})
        self.environment.stop()
        self.temp.cleanup()
    def test_bootstrap_preserves_catalog_and_original_blank_inputs(self):
        had_tk = 'tkinter' in sys.modules
        result = self.service.call('ui.bootstrap', {})
        self.assertEqual(len(result['catalog']), 16)
        report = next(t for t in result['catalog'] if t['id'] == 'report')
        fields = {f['key']: f['default'] for f in report['fields']}
        self.assertEqual([fields[k] for k in ('online_workbook','day_ahead_workbook','daily_workbook','template_report')], [''] * 4)
        self.assertEqual('tkinter' in sys.modules, had_tk)
    def test_dates_and_pdf_output_share_original_rules(self):
        self.assertEqual(self.service.call('ui.date.normalize', {'value':'2026/11/17'}), '2026-11-17')
        self.assertEqual(self.service.call('ui.daily.output', {'path':'/tmp/广东运行日报2026（11.17）.pdf'}), '/tmp/现货运行日报报价容量区间20261117.xlsx')
        with self.assertRaises(ValueError): self.service.call('ui.date.normalize', {'value':'2026-02-30'})
    def test_credential_save_is_private_and_cancels_old_verification(self):
        self.service.call('ui.credentials.save', {'username':'fixture-user','password':'fixture-secret'})
        ui = self.service._web_ui
        saved = ui.store.load()
        self.assertEqual(ui.store.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(ui.store.path.is_relative_to(self.data))
        self.assertEqual(self.service.call('ui.credentials.load', {}), {'username':'fixture-user','password':'fixture-secret'})
        self.service.call('ui.credentials.save', {'username':'fixture-user','password':'changed'})
        self.assertNotEqual(saved['revision'], ui.store.load()['revision'])
        self.assertFalse(ui.verifier.status()['running'])
    def test_local_sheets_and_dialog_preferences_roundtrip(self):
        import openpyxl
        path = self.data / 'source.xlsx'
        book = openpyxl.Workbook(); book.active.title = '日期'; book.create_sheet('数据'); book.save(path); book.close()
        self.assertEqual(self.service.call('ui.local.sheets', {'path':str(path)}), ['日期','数据'])
        self.service.call('ui.dialog.remember', {'key':'test','path':str(path)})
        self.assertEqual(self.service.call('ui.dialog.directory', {'key':'test'}), str(self.data))
        path.unlink()  # workbook handle was closed
    def test_shared_contracts_and_manual_fields(self):
        self.assertFalse(self.service.call('ui.requires_credentials', {'tool_id':'trade-analysis'}))
        self.assertTrue(self.service.call('ui.requires_credentials', {'tool_id':'online-energy'}))
        self.assertEqual(len(self.service.call('ui.market.fields', {})), 9)
        with self.assertRaisesRegex(ValueError, '不支持'): self.service.call('ui.unknown', {})
    def test_relative_business_path_and_blank_guard(self):
        actual = self.service.call('ui.business.directory', {'path':'data.xlsx','workbook':True})
        self.assertEqual(actual, str(self.service.paths.guangdong_price_dir.resolve()))
        with self.assertRaises(ValueError): self.service.call('ui.business.directory', {'path':'  '})


class MemoryStore:
    def __init__(self): self.value = dict(username='sample',password='sample',revision='first')
    def load(self): return dict(self.value)


class AccountVerifierTests(unittest.TestCase):
    def test_success_and_cooldown_do_not_repeat_login(self):
        verifier = AccountVerifier(MemoryStore())
        with patch('toolbox_shared.account_verifier.verify_credentials') as verify:
            verifier.start(); verifier.thread.join(2)
            self.assertEqual(verifier.status()['kind'], 'valid')
            verifier.start()
            self.assertEqual(verify.call_count, 1)
            self.assertIn('10秒', verifier.status()['text'])
    def test_cancel_discards_late_success(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs): entered.set(); release.wait(2)
        verifier = AccountVerifier(MemoryStore())
        with patch('toolbox_shared.account_verifier.verify_credentials', delayed):
            verifier.start(); self.assertTrue(entered.wait(2)); verifier.cancel()
            release.set(); verifier.thread.join(2)
            self.assertFalse(verifier.status()['running'])
            self.assertNotEqual(verifier.status()['kind'], 'valid')
    def test_revision_change_discards_stale_result(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs): entered.set(); release.wait(2)
        store = MemoryStore(); verifier = AccountVerifier(store)
        with patch('toolbox_shared.account_verifier.verify_credentials', delayed):
            verifier.start(); self.assertTrue(entered.wait(2)); store.value['revision'] = 'second'
            release.set(); verifier.thread.join(2)
            self.assertFalse(verifier.status()['running'])
            self.assertNotEqual(verifier.status()['kind'], 'valid')
    def test_read_failure_is_reported_after_network_result(self):
        store = MemoryStore(); verifier = AccountVerifier(store)
        def verify(*args, **kwargs): store.load = lambda: (_ for _ in ()).throw(ValueError('broken'))
        with patch('toolbox_shared.account_verifier.verify_credentials', verify):
            verifier.start(); verifier.thread.join(2)
            self.assertEqual(verifier.status()['kind'], 'error')
            self.assertFalse(verifier.status()['running'])

if __name__ == '__main__': unittest.main()
