from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from toolbox_shared import section_topology as m


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def workbook(self, rows, name='prices.xlsx', extra=None):
        book = Workbook()
        sheet = book.active
        sheet.append(['节点名称', '数据项', '00:00', '00:15', '00:30', '00:45', '01:00'])
        for row in rows:
            sheet.append([row[0], '电价', *row[1:]])
        if extra:
            sheet = book.create_sheet('第二页')
            sheet.append(['标题'])
            sheet.append(['节点名称', '00:00', '00:15', '00:30', '00:45', '01:00'])
            for row in extra:
                sheet.append(row)
        path = self.root / name
        book.save(path)
        book.close()
        return path

    def empty(self):
        data = m.seed_catalog()
        data.update(nodes=[], edges=[])
        return data

    def imported(self, rows):
        return m.read_catalog(self.workbook(rows))

    def synced(self, rows):
        return m.synchronize(self.empty(), self.imported(rows))

    def test_voltage_uses_final_bus_and_station_prefix_is_removed(self):
        result = self.imported([['500kV东莞变220kV', 1, 2, 3, 4, 5]])
        self.assertEqual(result['entries'][0]['station'], '东莞')
        self.assertEqual(result['entries'][0]['voltage'], 220)

    def test_dedup_same_station_voltage_curve_across_sheets_without_source_names(self):
        path = self.workbook([['某站500kV#1', 1, 2, 3, 4, 5]],
                             extra=[['某站500kV#2', 1., 2., 3., 4., 5.]])
        imported = m.read_catalog(path)
        self.assertEqual(len(imported['entries']), 1)
        self.assertEqual(imported['duplicates'], 1)
        self.assertNotIn('某站500kV#1', json.dumps(imported))
        self.assertNotIn('某站500kV#2', json.dumps(imported))

    def test_different_prices_voltage_and_missing_values_remain_separate(self):
        imported = self.imported([
            ['某站500kV#1', 1, 2, 3, 4, 5], ['某站500kV#1', 1, 9, 3, 4, 5],
            ['某站500kV#2', 1, None, 3, 4, 5], ['某站220kV', 1, 2, 3, 4, 5],
            ['某站110kV', 1, 2, 3, 4, 5], ['未标电压站', 1, 2, 3, 4, 5],
        ])
        self.assertEqual(len(imported['entries']), 5)
        self.assertEqual(imported['ignored'], 1)
        self.assertEqual(sum(n['voltage'] == 500 for n in imported['entries']), 3)

    def test_daily_changes_preserve_id_position_label_and_connections(self):
        data = self.synced([['甲站500kV', 1, 2, 3, 4, 5], ['乙站500kV', 3, 4, 5, 6, 7]])
        a = next(n for n in data['nodes'] if n['station'] == '甲')
        b = next(n for n in data['nodes'] if n['station'] == '乙')
        a.update(anchor={'x': 200, 'y': 300}, name='自定义名称', city='广州')
        data['edges'] = [dict(a=a['id'], b=b['id'], kind='line')]
        result = m.synchronize(data, self.imported([['甲站500kV', 9, 8, 7, 6, 5], ['乙站500kV', 2, 3, 4, 5, 6]]))
        updated = next(n for n in result['nodes'] if n['id'] == a['id'])
        self.assertEqual(updated['anchor'], a['anchor'])
        self.assertEqual(updated['name'], '自定义名称')
        self.assertEqual(updated['city'], '广州')
        self.assertEqual(updated['curve'], [9, 8, 7, 6, 5])
        self.assertEqual(result['edges'], data['edges'])

    def test_new_missing_and_reappearing_nodes(self):
        data = self.synced([['甲站500kV', 1, 2, 3, 4, 5]])
        node_id = data['nodes'][0]['id']
        missing = m.synchronize(data, self.imported([['乙站220kV', 1, 2, 3, 4, 5]]))
        self.assertFalse(next(n for n in missing['nodes'] if n['id'] == node_id)['present'])
        back = m.synchronize(missing, self.imported([['甲站500kV', 2, 3, 4, 5, 6]]))
        self.assertTrue(next(n for n in back['nodes'] if n['id'] == node_id)['present'])

    def test_distinct_raw_bus_id_matches_distinct_curves_across_days(self):
        data = self.synced([['甲站500kV#1', 1, 2, 3, 4, 5], ['甲站500kV#2', 6, 7, 8, 9, 10]])
        keys = {tuple(n['keys']): n['id'] for n in data['nodes']}
        result = m.synchronize(data, self.imported([
            ['甲站500kV#2', 16, 17, 18, 19, 20], ['甲站500kV#1', 11, 12, 13, 14, 15]]))
        self.assertEqual(len(result['nodes']), 2)
        self.assertEqual(keys, {tuple(n['keys']): n['id'] for n in result['nodes']})
        self.assertFalse(any(n['pending'] for n in result['nodes']))

    def test_identical_names_with_different_new_curves_require_association(self):
        data = self.synced([['甲站500kV', 1, 2, 3, 4, 5], ['甲站500kV', 6, 7, 8, 9, 10]])
        result = m.synchronize(data, self.imported([
            ['甲站500kV', 11, 12, 13, 14, 15], ['甲站500kV', 16, 17, 18, 19, 20]]))
        self.assertEqual(sum(n['pending'] for n in result['nodes']), 2)
        self.assertEqual(sum(not n['present'] for n in result['nodes']), 2)
        with self.assertRaisesRegex(ValueError, '待确认'):
            m.analysis_selection(result)

    def test_explicit_association_keeps_old_position_and_wiring(self):
        data = self.synced([['甲站500kV', 1, 2, 3, 4, 5]])
        old = data['nodes'][0]
        old['anchor'] = {'x': 400, 'y': 500}
        result = m.synchronize(data, self.imported([
            ['甲站500kV', 11, 12, 13, 14, 15], ['甲站500kV', 16, 17, 18, 19, 20]]))
        candidate = next(n for n in result['nodes'] if n['pending'])
        selected = m.resolve_variant(result, candidate['id'], old['id'])
        self.assertEqual(selected, old['id'])
        self.assertEqual(next(n for n in result['nodes'] if n['id'] == selected)['anchor'], old['anchor'])
        self.assertEqual(len(result['nodes']), 2)

    def test_changed_time_headers_preserve_missing_historical_curve(self):
        data = self.synced([['甲站500kV', 1, 2, 3, 4, 5]])
        imported = self.imported([['乙站500kV', 1, 2, 3, 4, 5]])
        imported['headers'] = ['00:00']
        imported['entries'][0].update(curve=[1], curve_headers=['00:00'])
        result = m.synchronize(data, imported)
        m.validate_catalog(result)

    def test_invalid_prices_fail_with_sheet_and_row(self):
        with self.assertRaisesRegex(ValueError, '第 2 行'):
            self.imported([['甲站500kV', 1, '错误', 3, 4, 5]])

    def test_invalid_and_duplicate_edges_are_rejected(self):
        data = self.synced([['甲站500kV', 1, 2, 3, 4, 5]])
        node_id = data['nodes'][0]['id']
        for b in (node_id, 'missing'):
            with self.subTest(b=b):
                data['edges'] = [dict(a=node_id, b=b, kind='line')]
                with self.assertRaises(ValueError):
                    m.validate_catalog(data)

    def test_persistence_readback_and_stale_window_protection(self):
        path = self.root / 'catalog.json'
        data = self.synced([['甲站500kV', 1, None, 3, 4, 5]])
        saved = m.save_catalog(data, path)
        self.assertEqual(m.load_catalog(path), saved)
        self.assertEqual(saved['revision'], 1)
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            m.save_catalog(data, path)
        self.assertEqual(m.load_catalog(path)['revision'], 1)
        self.assertFalse(list(path.parent.glob('tmp*')))

    def test_analysis_excludes_220_missing_and_transformer_edges(self):
        data = self.synced([['甲站500kV', 1, 2, 3, 4, 5], ['乙站500kV', 6, 7, 8, 9, 10],
                            ['丙站220kV', 9, 9, 9, 9, 9]])
        for i, n in enumerate(data['nodes']):
            n['anchor'] = {'x': 100+i*200, 'y': 100}
        ids = {n['station']: n['id'] for n in data['nodes']}
        data['edges'] = [dict(a=ids['甲'], b=ids['乙'], kind='line'),
                         dict(a=ids['甲'], b=ids['丙'], kind='transformer')]
        nodes, edges = m.analysis_selection(data)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(edges), 1)

    def test_materialized_values_read_back_without_averaging(self):
        data = self.synced([['甲站500kV#1', 1, 2, 3, 4, 5], ['甲站500kV#2', 11, 12, 13, 14, 15]])
        for i, n in enumerate(data['nodes']):
            n['anchor'] = {'x': 100+i*300, 'y': 100}
        a, b = data['nodes']
        data['edges'] = [dict(a=a['id'], b=b['id'], kind='line')]
        topology, prices, positions = m.materialize_analysis(data, self.root/'export')
        book = load_workbook(prices, data_only=True)
        self.assertEqual([list(row)[2:] for row in book.active.iter_rows(min_row=2, values_only=True)],
                         [n['curve'] for n in data['nodes']])
        book.close()
        self.assertEqual(len(m.parse_topology_edges(topology)[0]), 1)
        self.assertEqual(len(json.loads(positions.read_text())['nodes']), 2)

    def test_editor_pipeline_uses_new_workbook_not_saved_curves(self):
        from src.generate_500kv_blockage_map import generate_blockage_map
        source = self.workbook([['甲站500kV#1', 0, 20, 40, 60, 80],
                                ['甲站500kV#2', 200, 180, 160, 140, 120]])
        data = m.synchronize(self.empty(), m.read_catalog(source))
        for i, n in enumerate(data['nodes']):
            n['anchor'] = {'x': 100+i*300, 'y': 100}
        a, b = data['nodes']
        data['edges'] = [dict(a=a['id'], b=b['id'], kind='line')]
        applied = self.root/'applied.json'
        m.save_catalog(data, applied)
        baseline = generate_blockage_map(applied, source, self.root/'baseline', threshold=10)
        self.assertEqual(baseline.flagged_edges, 1)
        self.assertEqual(baseline.flagged_intervals, 4)
        # The next day's prices have no opposing movement; cached curves would flag.
        self.workbook([['甲站500kV#1', 0, 20, 40, 60, 80],
                       ['甲站500kV#2', 200, 220, 240, 260, 280]])
        original = source.read_bytes()
        result = generate_blockage_map(applied, source, self.root/'output', threshold=10)
        self.assertTrue(result.html_path.is_file())
        self.assertEqual(result.kept_rows, 2)
        self.assertEqual(result.flagged_edges, 0)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(list((self.root/'output').iterdir())), 1)

    def test_manual_voltage_confirmation_survives_later_import(self):
        data = self.synced([['待确认站', 1, 2, 3, 4, 5]])
        data['nodes'][0].update(voltage=220, voltage_override=220)
        result = m.synchronize(data, self.imported([['待确认站', 6, 7, 8, 9, 10]]))
        self.assertEqual(len(result['nodes']), 1)
        self.assertEqual(result['nodes'][0]['voltage'], 220)
        self.assertTrue(result['nodes'][0]['present'])

    def test_local_data_path_honors_development_root(self):
        with patch.dict(os.environ, {'TOOLBOX_DATA_ROOT': str(self.root), 'TOOLBOX_CODE_ROOT': str(m.WORKSPACE_ROOT)}):
            self.assertTrue(m.catalog_path().is_relative_to(self.root.resolve()))

    def test_documented_cli_starts_without_pythonpath(self):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        for script in ('run_500kv_analysis.py', 'src/analyze_500kv_section_gaps.py',
                       'src/build_500kv_map_html.py', 'src/classify_500kv_nodes.py'):
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, str(m.MODULE / script), '--help'],
                                        cwd=self.root, env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_builtin_seed_has_coordinates_and_valid_connections(self):
        data = m.seed_catalog()
        m.validate_catalog(data)
        self.assertGreater(len(data['nodes']), 100)
        self.assertTrue(all(n['anchor'] for n in data['nodes']))


if __name__ == '__main__':
    unittest.main()
