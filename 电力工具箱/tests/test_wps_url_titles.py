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
from toolbox_wps_config import build_config_export, runtime_config_from_export_payload
from toolbox_wps_urls import url_choices

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
                  mock.patch.object(importlib.import_module('toolbox_wps_service'), 'CONFIG_PATH', config_path)):
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
