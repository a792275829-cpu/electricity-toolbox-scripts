from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

# Loopback requests must not use machine-wide HTTP proxies.
urlopen = build_opener(ProxyHandler({})).open

from openpyxl import Workbook
from toolbox_shared import section_topology as model
from toolbox_shared.topology_web import BrowserEditor


def fixture():
    data = model.seed_catalog()
    data['nodes'] = data['nodes'][:3]
    data.update(headers=['00:00', '00:15'], edges=[], source='浏览器测试')
    for n, name, x, y in zip(data['nodes'], ('甲站', '乙站', '丙站'), (1000, 4000, 2500), (1000, 1000, 3500)):
        n.update(name=name, station=name[:-1], anchor={'x': x, 'y': y}, curve=[10, 20])
    a, b, _ = data['nodes']
    data['edges'] = [dict(a=a['id'], b=b['id'], kind='line')]
    return data


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'catalog.json'
        self.path_patch = patch.object(model, 'catalog_path', return_value=self.path)
        self.path_patch.start()
        self.data = fixture()
        self.service = BrowserEditor(initial_data=self.data)

    def tearDown(self):
        self.service.close()
        self.assertFalse(self.service.thread.is_alive())
        self.path_patch.stop()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        values = {'X-Editor-Token': self.service.token, 'Origin': self.service.origin}
        values.update(headers or {})
        payload = None if body is None else json.dumps(body).encode()
        request = Request(self.service.origin+'/api/'+path, data=payload, headers=values)
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_initial_snapshot_and_save_readback_share_native_store(self):
        self.assertEqual(self.request('state')['data'], self.data)
        saved = self.request('save', {'data': self.data})['data']
        self.assertEqual(model.load_catalog(), saved)
        self.assertEqual(saved['revision'], 1)
        self.assertFalse(self.service.dirty)

    def test_apply_updates_analysis_snapshot_and_notifies_module(self):
        result = self.request('apply', {'data': self.data})
        self.assertEqual(result['nodes'], 2)
        self.assertEqual(result['edges'], 1)
        self.assertEqual(model.load_catalog(model.applied_path())['edges'], self.data['edges'])
        self.assertEqual(self.service.applied.get_nowait(), model.applied_path())

    def test_cross_site_and_missing_token_cannot_read_or_write(self):
        for headers, body in (({'X-Editor-Token': ''}, None),
                              ({'Origin': 'https://example.com'}, {'data': self.data})):
            with self.subTest(headers=headers):
                with self.assertRaises(HTTPError) as error:
                    self.request('state' if body is None else 'save', body, headers)
                self.assertEqual(error.exception.code, 403)
        self.assertFalse(self.path.exists())

    def test_invalid_edge_and_stale_revision_do_not_overwrite(self):
        saved = self.request('save', {'data': self.data})['data']
        proposed = deepcopy(saved)
        proposed['edges'].append(proposed['edges'][0])
        for value in (proposed, self.data):
            with self.assertRaises(HTTPError) as error:
                self.request('save', {'data': value})
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(model.load_catalog(), saved)

    def test_excel_upload_uses_existing_dedup_and_sync_rules(self):
        book = Workbook(); sheet = book.active
        sheet.append(['节点名称', '00:00', '00:15'])
        sheet.append(['甲站500kV#1', 10, 20]); sheet.append(['甲站500kV#2', 10, 20])
        sheet.append(['甲站500kV#3', 30, 40]); sheet.append(['甲站220kV', 10, 20])
        stream = BytesIO(); book.save(stream); book.close()
        request = Request(self.service.origin+'/api/read-prices', data=stream.getvalue(),
                          headers={'X-Editor-Token': self.service.token, 'Origin': self.service.origin})
        with urlopen(request, timeout=5) as response:
            imported = json.load(response)
        self.assertEqual(imported['duplicates'], 1)
        self.assertEqual(len(imported['entries']), 3)
        result = self.request('sync', {'data': self.data, 'imported': imported})['data']
        self.assertEqual(result['edges'], self.data['edges'])
        self.assertEqual(sum(n['present'] for n in result['nodes']), 3)


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(model, 'catalog_path', return_value=Path(self.temp.name)/'catalog.json')
        self.path_patch.start()
        self.data = fixture()
        self.service = BrowserEditor(initial_data=self.data)
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 900})
        self.errors = []
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.set_default_timeout(5000)
        self.page.goto(self.service.url)
        self.page.wait_for_selector('.node')

    def tearDown(self):
        self.page.close(); self.service.close()
        self.path_patch.stop(); self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def matrix(self):
        return self.page.locator('#view').evaluate('(g)=>{const m=g.transform.baseVal.consolidate().matrix;return {k:m.a,x:m.e,y:m.f};}')

    def wheel(self, dx, dy, ctrl=False):
        prevented = self.page.locator('#canvas').evaluate('''(e,payload)=>{const v=new WheelEvent('wheel',{bubbles:true,cancelable:true,...payload});e.dispatchEvent(v);return v.defaultPrevented;}''',
             {'clientX':650,'clientY':430,'deltaX':dx,'deltaY':dy,'ctrlKey':ctrl})
        self.assertTrue(prevented)
        self.page.evaluate('()=>new Promise(requestAnimationFrame)')

    def choose(self, index):
        self.page.locator(f'.node[data-node="{self.data["nodes"][index]["id"]}"]').press('Enter')

    def test_trackpad_pinch_changes_canvas_scale_around_pointer(self):
        before = self.matrix()
        bounds = self.page.locator('#canvas').bounding_box()
        self.wheel(0, -35, True)
        after = self.matrix()
        self.assertGreater(after['k'], before['k'])
        px, py = 650-bounds['x'], 430-bounds['y']
        self.assertAlmostEqual((px-before['x'])/before['k'], (px-after['x'])/after['k'], places=2)
        self.assertAlmostEqual((py-before['y'])/before['k'], (py-after['y'])/after['k'], places=2)
        self.assertEqual(self.page.evaluate('window.devicePixelRatio'), 1)
        self.assertEqual(self.page.locator('body').evaluate('(e)=>getComputedStyle(e).zoom'), '1')

    def test_two_finger_scroll_pans_without_zoom_and_full_map_resets(self):
        before = self.matrix(); self.wheel(37, 55); after = self.matrix()
        self.assertAlmostEqual(after['k'], before['k'])
        self.assertAlmostEqual(after['x'], before['x']-37)
        self.assertAlmostEqual(after['y'], before['y']-55)
        self.page.get_by_role('button', name='全图', exact=True).click()
        reset = self.matrix()
        self.assertAlmostEqual(reset['k'], before['k'])
        self.assertAlmostEqual(reset['x'], before['x'])

    def test_safari_gesture_events_and_toolbar_zoom(self):
        before = self.matrix()
        self.page.locator('#canvas').dispatch_event('gesturestart', {'scale': 1})
        self.page.locator('#canvas').evaluate("e=>{const v=new Event('gesturechange',{cancelable:true});v.scale=1.5;e.dispatchEvent(v);}")
        self.page.evaluate('()=>new Promise(requestAnimationFrame)')
        self.page.locator('#canvas').dispatch_event('gestureend')
        self.assertAlmostEqual(self.matrix()['k'], before['k']*1.5, places=4)
        self.page.get_by_role('button', name='放大', exact=True).click()
        self.page.evaluate('()=>new Promise(requestAnimationFrame)')
        self.assertGreater(self.matrix()['k'], before['k']*1.5)

    def test_connection_requires_confirmation_and_cancel_is_clean(self):
        self.choose(0)
        self.page.get_by_role('button', name='从此节点连线', exact=True).click()
        self.choose(2)
        self.page.wait_for_selector('#connectionDialog[open]')
        self.assertEqual(self.page.locator('.edgeHit').count(), 1)
        self.page.get_by_role('button', name='取消', exact=True).click()
        self.assertEqual(self.page.locator('.edgeHit').count(), 1)
        self.page.get_by_role('button', name='从此节点连线', exact=True).click()
        self.choose(2)
        self.page.get_by_role('button', name='确认连接', exact=True).click()
        self.page.locator('#edges > g').nth(1).wait_for(state='attached')
        self.page.get_by_role('button', name='撤销', exact=True).click()
        self.assertEqual(self.page.locator('.edgeHit').count(), 1)

    def test_name_save_apply_and_reload_readback(self):
        self.choose(0)
        self.page.locator('#name').fill('甲站新名称')
        self.page.get_by_role('button', name='保存草稿', exact=True).click()
        self.page.locator('#notice').filter(has_text='草稿已保存').wait_for()
        saved = model.load_catalog()
        self.assertEqual(saved['nodes'][0]['name'], '甲站新名称')
        self.assertEqual(saved['nodes'][0]['anchor'], self.data['nodes'][0]['anchor'])
        self.page.get_by_role('button', name='应用到分析', exact=True).click()
        self.page.locator('#notice').filter(has_text='已应用到分析').wait_for()
        self.assertEqual(model.load_catalog(model.applied_path())['nodes'][0]['name'], '甲站新名称')
        self.page.reload(); self.page.wait_for_selector('.node')
        self.choose(0)
        self.assertEqual(self.page.locator('#name').input_value(), '甲站新名称')

    def test_excel_upload_save_and_json_backup_roundtrip(self):
        book = Workbook(); sheet = book.active
        sheet.append(['节点名称', '00:00', '00:15'])
        sheet.append(['甲站500kV#1', 30, 40]); sheet.append(['甲站500kV#2', 30, 40])
        sheet.append(['乙站500kV', 90, 70]); sheet.append(['新站220kV', 20, 20])
        source = Path(self.temp.name)/'新节点电价.xlsx'
        book.save(source); book.close()
        original = source.read_bytes()
        self.page.locator('#pricesFile').set_input_files(str(source))
        self.page.locator('#notice').filter(has_text='同步完成').wait_for()
        self.page.get_by_role('button', name='保存草稿', exact=True).click()
        self.page.locator('#notice').filter(has_text='草稿已保存').wait_for()
        saved = model.load_catalog()
        self.assertEqual(sum(n['present'] for n in saved['nodes']), 3)
        self.assertEqual(saved['summary']['duplicates'], 1)
        self.assertEqual(saved['edges'], self.data['edges'])
        self.assertEqual(source.read_bytes(), original)
        with self.page.expect_download() as pending_download:
            self.page.get_by_role('button', name='导出备份', exact=True).click()
        backup = Path(self.temp.name)/'backup.json'
        pending_download.value.save_as(str(backup))
        self.assertEqual(json.loads(backup.read_text())['nodes'], saved['nodes'])
        self.page.locator('#jsonFile').set_input_files(str(backup))
        self.page.locator('#notice').filter(has_text='备份已导入').wait_for()
        self.assertEqual(self.page.locator('.edgeHit').count(), 1)
        self.service.close()
        with self.page.expect_download() as offline_download:
            self.page.get_by_role('button', name='导出备份', exact=True).click()
        self.assertEqual(offline_download.value.suggested_filename, '500和220kV拓扑.json')

    def test_node_drag_can_be_undone_without_changing_connections(self):
        target = self.page.locator(f'.node[data-node="{self.data["nodes"][2]["id"]}"] circle').bounding_box()
        x, y = target['x']+target['width']/2, target['y']+target['height']/2
        self.page.mouse.move(x,y); self.page.mouse.down(); self.page.mouse.move(x+40,y+30,steps=3);self.page.mouse.up()
        self.page.locator('#undo:not([disabled])').wait_for()
        self.page.get_by_role('button', name='保存草稿', exact=True).click()
        self.page.locator('#notice').filter(has_text='草稿已保存').wait_for()
        self.assertNotEqual(model.load_catalog()['nodes'][2]['anchor'], self.data['nodes'][2]['anchor'])
        self.assertEqual(model.load_catalog()['edges'], self.data['edges'])
        self.page.get_by_role('button', name='撤销', exact=True).click()
        self.page.get_by_role('button', name='保存草稿', exact=True).click()
        self.page.locator('#notice').filter(has_text='草稿已保存').wait_for()
        self.assertEqual(model.load_catalog()['nodes'][2]['anchor'], self.data['nodes'][2]['anchor'])
