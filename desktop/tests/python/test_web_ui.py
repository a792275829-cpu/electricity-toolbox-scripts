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
    def test_manual_market_requires_day_workbook_and_uses_workbook_date(self):
        from datetime import date
        import openpyxl
        for path in ("", "  "):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "请先选择 D 日收益表"):
                self.service.call('ui.market.report_date', {'path': path})
        path = self.data / '日期识别.xlsx'
        book = openpyxl.Workbook()
        book.active.title = '新快报'
        book.active['A6'] = date(2026, 11, 17)
        book.save(path)
        book.close()
        self.assertEqual(self.service.call('ui.market.report_date', {'path': str(path)}), '2026-11-17')

    def test_manual_market_preview_validates_all_36_values_and_normalizes_for_save(self):
        day = '2026-11-17'
        fields = self.service.call('ui.market.fields', {})
        data = {'report_date': day, **{
            period: {field['key']: {'value': '100', 'comparison': '90'} for field in fields}
            for period in ('day_ahead', 'realtime')
        }}
        result = self.service.call('ui.market.preview', {'date': day, 'data': data})
        for period in ('day_ahead', 'realtime'):
            self.assertEqual(len(result['data'][period]), 9)
            self.assertTrue(all(pair == {'value': 100, 'comparison': 90}
                                for pair in result['data'][period].values()))
            self.assertEqual(set(result['deltas'][period].values()), {10})
        data['realtime']['coal']['value'] = '1.5'
        with self.assertRaisesRegex(ValueError, '非负整数'):
            self.service.call('ui.market.preview', {'date': day, 'data': data})
        data['realtime']['coal']['value'] = '100'
        with self.assertRaisesRegex(ValueError, '日期'):
            self.service.call('ui.market.preview', {'date': '2026-11-18', 'data': data})
        self.assertEqual(self.service.call('ui.market.preview', {'date': day, 'data': {'report_date': day}})['data'], {})

    def test_relative_business_path_and_blank_guard(self):
        actual = self.service.call('ui.business.directory', {'path':'data.xlsx','workbook':True})
        self.assertEqual(actual, str(self.service.paths.guangdong_price_dir.resolve()))
        with self.assertRaises(ValueError): self.service.call('ui.business.directory', {'path':'  '})

    def test_output_directory_uses_selected_station_and_pdf_default(self):
        selected = self.data/'海风'/'收益.xlsx'
        values = dict(guihu_workbook=str(self.data/'归湖'/'收益.xlsx'),haifeng_workbook=str(selected),haifeng_selected=True)
        self.assertEqual(self.service.call('ui.output.directory',{'tool_id':'power-benefit','values':values}),str(selected.parent))
        self.assertEqual(self.service.call('ui.output.directory',{'tool_id':'daily-pdf-parser','values':{'pdf_path':str(self.data/'日报.pdf')}}),str(self.data))
        with self.assertRaises(ValueError):self.service.call('ui.output.directory',{'tool_id':'trade-analysis','values':{}})
    def test_output_directory_uses_current_online_config(self):
        from toolbox_shared.credentials import write_private_json
        output=self.data/'导出目录'
        write_private_json(self.data/'上网电量抓取/config.json',{'outputDir':str(output)})
        self.assertEqual(self.service.call('ui.output.directory',{'tool_id':'online-energy'}),str(output))
    def test_editor_apply_queue_and_shutdown_are_forwarded(self):
        with patch('webbrowser.open_new_tab',return_value=True):
            self.service.call('ui.section.editor',{})
        browser=self.service._web_ui.browser
        browser.applied.put(self.data/'验证拓扑.json')
        self.assertEqual(self.service.call('ui.section.status',{})['applied'],str(self.data/'验证拓扑.json'))
        self.assertIsNone(self.service.call('ui.section.status',{})['applied'])
        self.service.call('shutdown',{'cancel':True})
        self.assertTrue(browser.closed)
        self.assertFalse(browser.thread.is_alive())


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
