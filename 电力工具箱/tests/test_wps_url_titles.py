from __future__ import annotations
import importlib

import copy
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

from toolbox.runtime import load_module
from toolbox_shared.wps_config import build_config_export, runtime_config_from_export_payload
from toolbox_shared.wps_urls import url_choices

ROOT = Path(__file__).resolve().parents[2]
FIRST = 'https://www.kdocs.cn/l/first'
SECOND = 'https://www.kdocs.cn/l/second'
TITLE = '出清情况（分公司2026.10.12）'


class WpsTitleFormatTests(unittest.TestCase):
    def test_rust_binding_formats_and_resolves_label(self):
        label = FIRST + '（' + TITLE + '）'
        choices = url_choices([FIRST, SECOND, FIRST], {FIRST: TITLE}, label)
        self.assertEqual(choices['url'], FIRST)
        self.assertEqual(choices['options'], [
            {'url': FIRST, 'label': label}, {'url': SECOND, 'label': SECOND},
        ])
        self.assertEqual(url_choices([], {}, 'https://example.test/（原始）')['url'],
                         'https://example.test/（原始）')

    def test_title_cache_survives_export_import_without_changing_mappings(self):
        config = json.loads((ROOT / 'wps自动/wps_excel_to_kdocs_config.defaults.json').read_text())
        original = copy.deepcopy(config['configs'])
        config['document_titles'] = {FIRST: TITLE, SECOND: '另一份表格'}
        restored = runtime_config_from_export_payload(build_config_export(config))
        self.assertEqual(restored['document_titles'], config['document_titles'])
        self.assertEqual(restored['configs'], original)


class WpsTitleDialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.module = load_module('wps_url_title_dialog_test', ROOT / 'wps自动/wps_excel_to_kdocs_gui.py')
        with (mock.patch.object(self.module.DocumentConfigDialog, 'wait_visibility'),
              mock.patch.object(self.module.DocumentConfigDialog, 'wait_window')):
            self.dialog = self.module.DocumentConfigDialog(
                self.root, '编辑配置', recent_urls=[FIRST, SECOND],
                document_titles={FIRST: TITLE, SECOND: '另一份表格'},
            )

    def tearDown(self):
        self.dialog.destroy()
        self.root.destroy()

    def test_selected_label_keeps_raw_url_for_read_and_save(self):
        dialog = self.dialog
        self.assertEqual(dialog.target_url_display.get(), FIRST + '（' + TITLE + '）')
        self.assertEqual(dialog.kdocs_url.get(), FIRST)
        self.assertEqual(list(dialog.target_url_combo['values']), [
            FIRST + '（' + TITLE + '）', SECOND + '（另一份表格）',
        ])
        dialog.target_url_display.set(SECOND + '（另一份表格）')
        self.assertEqual(dialog.kdocs_url.get(), SECOND)
        with mock.patch.object(dialog, '_start_online_read') as read:
            dialog.refresh_online_sheets()
        read.assert_called_once_with('target', SECOND, True)
        dialog.local_file.set('/tmp/source.xlsx')
        dialog.regions = [self.module.RegionRow('来源', 'A1', 'A2', '目标', 'B1', 'B2')]
        self.assertEqual(dialog.build_current_config().kdocs_url, SECOND)
        dialog.target_url_display.set('https://example.test/（原始）')
        self.assertEqual(dialog.kdocs_url.get(), 'https://example.test/（原始）')

    def test_source_refresh_reads_link_even_when_local_source_was_selected(self):
        dialog = self.dialog
        dialog.source_url_display.set(SECOND + '（另一份表格）')
        self.assertEqual(dialog.source_url.get(), SECOND)
        self.assertEqual(dialog.source_type.get(), 'excel')
        dialog.local_sheets = ['本地旧工作表']
        with mock.patch.object(self.module, 'read_online_document_info', return_value={
            'title': '来源文档', 'sheets': ['来源一', '来源二'],
        }) as read:
            dialog.refresh_source_button.invoke()
            self.assertEqual(dialog.local_sheets, [])
            deadline = time.monotonic() + 2
            while dialog._pending_online_reads and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertFalse(dialog._pending_online_reads)
        self.assertEqual(read.call_args.args[0], SECOND)
        self.assertEqual(dialog.source_type.get(), 'kdocs')
        self.assertEqual(dialog.local_sheets, ['来源一', '来源二'])
        self.assertEqual(dialog.source_url_display.get(), SECOND + '（来源文档）')
        dialog.regions = [self.module.RegionRow('来源一', 'A1', 'A2', '目标', 'B1', 'B2')]
        self.assertEqual(dialog.build_current_config().source_url, SECOND)
        with mock.patch.object(self.module, 'RegionDialog', return_value=mock.Mock(result=None)) as region:
            dialog.add_region()
        self.assertEqual(region.call_args.kwargs['local_sheets'], ['来源一', '来源二'])

    def test_source_read_ignores_old_link_and_local_source_results(self):
        dialog = self.dialog
        dialog.source_type.set('kdocs')
        dialog.source_url.set(FIRST)
        dialog.local_sheets = ['旧工作表']
        request_id = dialog._request_ids['source']
        dialog.source_url_display.set(SECOND)
        self.assertEqual(dialog.local_sheets, [])
        dialog._online_results.put(('source', request_id, FIRST, False,
                                   {'title': '过时名称', 'sheets': ['旧工作表']}, None))
        dialog._drain_online_results()
        self.assertEqual(dialog.source_doc_title.get(), '另一份表格')
        self.assertEqual(dialog.local_sheets, [])
        dialog.source_type.set('excel')
        dialog.local_sheets = ['本地工作表']
        dialog._online_results.put(('source', dialog._request_ids['source'], SECOND, False,
                                   {'title': '在线文档', 'sheets': ['在线工作表']}, None))
        dialog._drain_online_results()
        self.assertEqual(dialog.local_sheets, ['本地工作表'])

    def test_source_failure_restores_button_and_retains_sheets(self):
        dialog = self.dialog
        dialog.source_type.set('kdocs')
        dialog.source_url.set(SECOND)
        dialog.local_sheets = ['已读工作表']
        with (mock.patch.object(self.module, 'read_online_document_info', side_effect=RuntimeError('来源连接失败')),
              mock.patch.object(self.module.messagebox, 'showerror') as error):
            dialog.refresh_source_button.invoke()
            deadline = time.monotonic() + 2
            while dialog._pending_online_reads and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertEqual(dialog.local_sheets, ['已读工作表'])
        self.assertEqual(str(dialog.refresh_source_button.cget('state')), 'normal')
        error.assert_called_once_with('读取来源工作表失败', '来源连接失败', parent=dialog)

    def test_selecting_local_file_switches_back_from_online_source(self):
        dialog = self.dialog
        dialog.source_type.set('kdocs')
        with (mock.patch.object(self.module.filedialog, 'askopenfilename', return_value='/tmp/local.xlsx'),
              mock.patch.object(self.module, 'read_excel_sheet_names', return_value=['本地工作表']) as read,
              mock.patch.object(dialog, '_start_online_read') as online):
            dialog.browse_local_file()
        read.assert_called_once_with(Path('/tmp/local.xlsx'))
        online.assert_not_called()
        self.assertEqual(dialog.source_type.get(), 'excel')
        self.assertEqual(dialog.local_sheets, ['本地工作表'])

    def test_open_region_dropdown_uses_latest_refreshed_sheets(self):
        dialog = self.dialog
        dialog.local_sheets = ['刷新前']
        with (mock.patch.object(self.module.RegionDialog, 'wait_visibility'),
              mock.patch.object(self.module.RegionDialog, 'wait_window')):
            region = self.module.RegionDialog(dialog, '添加区域', local_sheets=dialog.local_sheets)
        try:
            dialog.local_sheets = ['刷新后一', '刷新后二']
            combos = [widget for widget in region.winfo_children() if isinstance(widget, ttk.Combobox)]
            source, target = combos
            self.root.tk.call(source.cget('postcommand'))
            self.assertEqual(list(source['values']), ['刷新后一', '刷新后二'])
            dialog.online_sheets = ['目标新工作表']
            self.root.tk.call(target.cget('postcommand'))
            self.assertEqual(list(target['values']), ['目标新工作表'])
        finally:
            region.destroy()

    def test_edit_online_source_automatically_loads_source_and_target(self):
        initial = self.module.DocumentConfig(name='配置', kdocs_url=FIRST, source_type='kdocs',
            source_url=SECOND, local_file='', regions=[])
        def info(url, *_args):
            return {'title': '来源' if url == SECOND else '目标', 'sheets': [url]}
        with (mock.patch.object(self.module.DocumentConfigDialog, 'wait_visibility'),
              mock.patch.object(self.module.DocumentConfigDialog, 'wait_window'),
              mock.patch.object(self.module, 'read_online_document_info', side_effect=info) as read):
            other = self.module.DocumentConfigDialog(self.root, '编辑配置', initial)
            try:
                deadline = time.monotonic() + 2
                while other._pending_online_reads and time.monotonic() < deadline:
                    self.root.update()
                    time.sleep(0.01)
                self.assertEqual(read.call_count, 2)
                self.assertEqual(other.local_sheets, [SECOND])
                self.assertEqual(other.online_sheets, [FIRST])
                self.assertEqual(other.source_url_display.get(), SECOND + '（来源）')
            finally:
                other.destroy()

    def test_metadata_read_updates_inline_label_and_cache(self):
        dialog = self.dialog
        callback = mock.Mock()
        dialog.on_document_title = callback
        with mock.patch.object(self.module, 'read_online_document_info', return_value={
            'title': '更新后的名称（含括号）', 'sheets': ['工作表'],
        }) as read:
            dialog.refresh_online_sheets()
            deadline = time.monotonic() + 2
            while dialog._pending_online_reads and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertFalse(dialog._pending_online_reads)
        self.assertEqual(read.call_args.args[0], FIRST)
        self.assertEqual(dialog.target_url_display.get(), FIRST + '（更新后的名称（含括号））')
        callback.assert_called_once_with(FIRST, '更新后的名称（含括号）')
        labels = []
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Label): labels.append(str(child.cget('text')))
                walk(child)
        walk(dialog)
        self.assertNotIn('目标表格名称', labels)

    def test_outdated_read_does_not_attach_name_to_different_url(self):
        dialog = self.dialog
        dialog._request_ids['target'] = 1
        dialog._pending_online_reads['target'] = (1, FIRST)
        dialog.kdocs_url.set(SECOND)
        dialog._online_results.put(('target', 1, FIRST, False,
                                   {'title': '过时名称', 'sheets': ['旧工作表']}, None))
        dialog._drain_online_results()
        self.assertEqual(dialog.target_url_display.get(), SECOND + '（另一份表格）')
        self.assertEqual(dialog.online_sheets, [])
        self.assertEqual(dialog.document_titles[FIRST], TITLE)

    def test_parent_saves_only_raw_urls_and_reuses_cached_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            with (mock.patch.object(self.module, 'load_config', return_value={}),
                  mock.patch.object(importlib.import_module('toolbox_shared.wps_service'), 'CONFIG_PATH', config_path)):
                frame = self.module.WpsWriterFrame(self.root)
                try:
                    frame.remember_document_title(FIRST, TITLE)
                    saved = json.loads(config_path.read_text())
                    self.assertEqual(saved['document_titles'], {FIRST: TITLE})
                    self.assertNotIn('（', ''.join(saved['recent_urls']))
                    frame.apply_runtime_config(saved)
                    self.assertEqual(frame.document_titles, {FIRST: TITLE})
                finally:
                    frame.destroy()


if __name__ == '__main__':
    unittest.main()
