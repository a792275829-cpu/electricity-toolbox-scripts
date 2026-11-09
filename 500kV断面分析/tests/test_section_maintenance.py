import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from openpyxl import Workbook
from toolbox_shared.section_maintenance import (
    API_PATH, attach_maintenance, fetch_snapshot, normalize_snapshot, workbook_trade_date,
)

DAY = date(2026, 11, 10)


def record(name='胪岗站胪盘乙线', kind='LINE', diff='EQUAL', voltage=500):
    return dict(elementName=name, deviceType=kind, diff=diff, voltageLevel=voltage,
                startDate='2026-10-03 00:00:00', endDate='2026-11-10 00:00:00',
                runDate='2026-11-10 00:00:00')


def snapshot(records):
    return dict(run_date='2026-11-10', compare_date='2026-11-09', records=records)


class MaintenanceTests(unittest.TestCase):
    def workbook(self, directory, title, top=None):
        path = Path(directory) / '错误文件日期2026-01-01.xlsx'
        book = Workbook()
        sheet = book.active
        sheet.title = title
        if top:
            sheet.append([top])
        sheet.append(['节点名称', '数据项', '00:00', '00:15'])
        sheet.append(['胪岗站', '电价(元/MWh)', 100, 200])
        sheet.append(['盘龙站', '电价(元/MWh)', 200, 100])
        book.save(path)
        book.close()
        return path

    def test_date_uses_sheet_title_and_not_filename(self):
        with TemporaryDirectory() as d:
            self.assertEqual(workbook_trade_date(self.workbook(d, '日前节点电价查询(2026-11-10)')), DAY)

    def test_date_supports_title_cell_and_rejects_conflicts_or_missing(self):
        with TemporaryDirectory() as d:
            self.assertEqual(workbook_trade_date(self.workbook(d, '电价', '运行日：2026年11月10日')), DAY)
            for title, top in [('电价', None), ('电价2026-11-09', '2026-11-10')]:
                with self.assertRaises(ValueError):
                    workbook_trade_date(self.workbook(d, title, top))

    def test_snapshot_rejects_wrong_day_and_comparison_day(self):
        for key in ('run_date', 'compare_date'):
            raw = snapshot([record()])
            raw[key] = '2026-11-08'
            with self.assertRaises(ValueError):
                normalize_snapshot(raw, DAY)

    def test_interface_capture_validates_http_business_status_and_dates(self):
        capture = dict(status=200, url='https://example.test'+API_PATH+'?runDate=2026-11-10&compareDate=2026-11-09',
                       payload=dict(retCode='T200', data=[record()]))
        self.assertEqual(normalize_snapshot(capture, DAY)['records'], [record()])
        for bad in (dict(capture, status=401), dict(capture, payload=dict(retCode='F500', data=[]))):
            with self.assertRaises(ValueError):
                normalize_snapshot(bad, DAY)

    def test_seven_line_records_map_to_six_edges_and_keep_equipment_separate(self):
        from toolbox_shared.section_maintenance import LINE_ENDPOINTS
        rows = [record(name) for name in ('广蓄A厂蓄荔甲线', '广蓄B厂蓄能AB联线',
                  '胪岗站海胪丙线', '胪岗站胪盘乙线', '陆丰站陆吉甲线')]
        rows += [record(f'盘龙站500kV盘吉{c}线5043开关', 'SWITCH') for c in '甲乙']
        rows += [record('盘龙站500kV第五串联络5052开关', 'SWITCH'),
                 record('广蓄A厂500kV#1主变', 'MAINCHANGE'), record(voltage=220)]
        data = dict(edges=[dict(a=a, b=b) for a,b in LINE_ENDPOINTS.values()])
        attach_maintenance(data, snapshot(rows))
        self.assertEqual(data['maintenance']['matched_records'], 7)
        self.assertEqual(data['maintenance']['matched_edges'], 6)
        self.assertEqual(len(data['maintenance']['separate']), 2)
        self.assertEqual(data['maintenance']['unmatched'], [])
        self.assertTrue(all('2026-10-03 起' in r['label'] for e in data['edges'] for r in e['maintenance']))

    def test_removed_records_do_not_blacken_running_day(self):
        removed = record(diff='DELETE')
        removed['runDate'] = '2026-11-09 00:00:00'
        data = dict(edges=[dict(a='胪岗站', b='盘龙站', is_section=True)])
        attach_maintenance(data, snapshot([removed]))
        self.assertFalse(data['edges'][0]['maintenance'])
        self.assertTrue(data['edges'][0]['is_section'])
        self.assertEqual(len(data['maintenance']['removed']), 1)

    def test_unknown_line_and_line_switch_are_unmatched_not_guessed(self):
        data = dict(edges=[dict(a='甲站', b='乙站')])
        attach_maintenance(data, snapshot([record('陌生甲线'), record('陌生甲线开关', 'SWITCH')]))
        self.assertEqual(len(data['maintenance']['unmatched']), 2)
        self.assertEqual(data['maintenance']['matched_edges'], 0)

    def test_changed_title_or_unknown_diff_fail_instead_of_guessing(self):
        with self.assertRaises(ValueError):
            normalize_snapshot(snapshot([record(diff='UNRECOGNIZED')]), DAY)
        data = dict(edges=[])
        wrong = record()
        wrong['runDate'] = '2026-11-08 00:00:00'
        with self.assertRaises(ValueError):
            attach_maintenance(data, snapshot([wrong]))

    def test_non_midnight_start_time_is_preserved(self):
        row = record()
        row['startDate'] = '2026-10-03 09:15:00'
        data = dict(edges=[dict(a='胪岗站', b='盘龙站')])
        attach_maintenance(data, snapshot([row]))
        self.assertIn('09:15:00', data['edges'][0]['maintenance'][0]['label'])

    def test_guangxu_a_and_b_use_the_named_plant_not_the_shared_line_initials(self):
        data = dict(edges=[dict(a='广蓄A厂', b='荔西站'), dict(a='广蓄B厂', b='荔西站')])
        attach_maintenance(data, snapshot([record('广蓄B厂蓄荔乙线'), record('未知厂蓄荔甲线')]))
        self.assertEqual(data['edges'][0]['maintenance'], [])
        self.assertEqual(len(data['edges'][1]['maintenance']), 1)
        self.assertEqual(len(data['maintenance']['unmatched']), 1)

    def test_fetch_uses_d_and_previous_calendar_day_with_read_only_get(self):
        request = Mock()
        request.get.return_value = SimpleNamespace(status=200, json=lambda: dict(retCode='T200', data=[record()]))
        result = fetch_snapshot(SimpleNamespace(request=request), DAY)
        self.assertEqual(result['compare_date'], '2026-11-09')
        self.assertEqual(request.get.call_args.kwargs['params'],
                         dict(runDate='2026-11-10', compareDate='2026-11-09', runType=1, compareType=1))
        request.post.assert_not_called()

    def test_generation_enforces_snapshot_date_and_embeds_overlay(self):
        from src.generate_500kv_blockage_map import generate_blockage_map
        with TemporaryDirectory() as d:
            raw = self.workbook(d, '节点电价2026-11-10')
            topology = Path(d) / 'topology.md'
            topology.write_text('## 2. 接线\n### 粤东\n胪岗站-盘龙站\n')
            result = generate_blockage_map(topology, raw, Path(d)/'out', maintenance_data=snapshot([record()]))
            html = result.html_path.read_text()
            self.assertIn('胪盘乙线检修 · 2026-10-03 起', html)
            self.assertIn('"matched_edges": 1', html)
            self.assertIn('maintenanceLabels', html)
            with self.assertRaises(ValueError):
                generate_blockage_map(topology, raw, Path(d)/'out', maintenance_data=dict(snapshot([]), run_date='2026-11-09'))

    def test_relative_cli_input_keeps_its_matching_snapshot(self):
        import os
        from run_500kv_analysis import run_many
        with TemporaryDirectory() as d:
            raw = self.workbook(d, '节点电价2026-11-10')
            topology = Path(d)/'topology.md'
            topology.write_text('## 2. 接线\n### 粤东\n胪岗站-盘龙站\n')
            capture = Path(d)/'maintenance.json'
            capture.write_text(json.dumps(snapshot([record()])))
            outputs = run_many([Path(os.path.relpath(raw))], Path(d)/'out', topology_path=topology,
                               maintenance_json=capture)
            self.assertIn('"matched_records": 1',outputs[0].read_text())


class MaintenanceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        from src.generate_500kv_blockage_map import generate_blockage_map
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f'Chromium unavailable: {type(exc).__name__}')
        cls.directory = TemporaryDirectory()
        root = Path(cls.directory.name)
        book = Workbook()
        sheet = book.active
        sheet.title = '电价2026-11-10'
        sheet.append(['节点名称', '数据项', *[f'00:{i:02d}' for i in (0,15,30,45)]])
        sheet.append(['胪岗站', '电价(元/MWh)', 200,210,220,230])
        sheet.append(['盘龙站', '电价(元/MWh)', -100,-100,-100,-100])
        raw = root/'prices.xlsx'
        book.save(raw);book.close()
        topology = root/'topology.md'
        topology.write_text('## 2. 接线\n### 粤东\n胪岗站-盘龙站\n')
        result = generate_blockage_map(topology,raw,root/'out',maintenance_data=snapshot([record()]))
        cls.url = result.html_path.as_uri()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop();cls.directory.cleanup()

    def test_black_survives_section_style_selection_and_threshold_then_toggles_labels(self):
        page = self.browser.new_page(viewport=dict(width=1440,height=1000))
        errors = []
        page.on('pageerror',lambda exc:errors.append(str(exc)))
        try:
            page.goto(self.url)
            line = page.locator('.edge.maintenance')
            self.assertEqual(line.count(),1)
            self.assertEqual(line.evaluate('(e)=>getComputedStyle(e).stroke'),'rgb(0, 0, 0)')
            self.assertNotIn('2026-',page.locator('.maintenance-label').text_content())
            self.assertFalse(page.locator('#nodePriceTooltip').is_visible())
            page.locator('.edge-hit').focus()
            from playwright.sync_api import expect
            expect(page.locator('#nodePriceTooltip')).to_be_visible()
            self.assertIn('2026-10-03',page.locator('#mapPriceMaintenance').text_content())
            self.assertIn('胪岗站：200.0000 元/MWh',page.locator('#nodePriceValue').text_content())
            self.assertIn('盘龙站：-100.0000 元/MWh',page.locator('#nodePriceValue').text_content())
            page.locator('.edge-hit').blur()
            self.assertFalse(page.locator('#nodePriceTooltip').is_visible())
            page.locator('.node-group[data-node-name="盘龙站"]').press('Enter')
            self.assertEqual(line.evaluate('(e)=>getComputedStyle(e).stroke'),'rgb(0, 0, 0)')
            page.locator('#gapThreshold').fill('500')
            page.locator('#gapThreshold').press('Enter')
            self.assertEqual(line.evaluate('(e)=>getComputedStyle(e).stroke'),'rgb(0, 0, 0)')
            self.assertIn('胪盘乙线',page.locator('.edge-hit').get_attribute('aria-label'))
            page.locator('#toggleMaintenance').click()
            self.assertFalse(page.locator('#maintenanceLabels').is_visible())
            self.assertEqual(line.evaluate('(e)=>getComputedStyle(e).stroke'),'rgb(0, 0, 0)')
            page.set_viewport_size(dict(width=390,height=844))
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            self.assertEqual(errors,[])
        finally:
            page.close()

    def test_pointer_hover_refreshes_price_and_start_time_then_hides_on_leave(self):
        from playwright.sync_api import expect
        page = self.browser.new_page(viewport=dict(width=1440,height=1000))
        try:
            page.goto(self.url)
            self.assertNotIn('2026-',page.locator('#maintenanceLabels').text_content())
            page.locator('.edge-hit').hover(force=True)
            expect(page.locator('#nodePriceTooltip')).to_be_visible()
            self.assertIn('开始：2026-10-03 00:00:00',page.locator('#mapPriceMaintenance').text_content())
            self.assertIn('胪岗站：200.0000 元/MWh',page.locator('#nodePriceValue').text_content())
            page.evaluate('setTopologyTime(1,false)')
            self.assertIn('00:15',page.locator('#nodePriceValue').text_content())
            self.assertIn('胪岗站：210.0000 元/MWh',page.locator('#nodePriceValue').text_content())
            page.mouse.move(0,0)
            expect(page.locator('#nodePriceTooltip')).to_be_hidden()
        finally:
            page.close()

    def test_touch_tap_shows_price_and_maintenance_without_hover(self):
        from playwright.sync_api import expect
        page = self.browser.new_page(viewport=dict(width=390,height=844),has_touch=True)
        try:
            page.goto(self.url)
            page.locator('.edge-hit').tap(force=True)
            expect(page.locator('#nodePriceTooltip')).to_be_visible()
            self.assertIn('2026-10-03',page.locator('#mapPriceMaintenance').text_content())
            self.assertIn('元/MWh',page.locator('#nodePriceValue').text_content())
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main()
