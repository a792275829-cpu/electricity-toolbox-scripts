from base64 import b64decode
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from toolbox_shared import section_basemap as maps, section_topology as model
import test_topology_web as web

fixture = web.fixture


def image_bytes(size=(1000, 700), color='white'):
    stream = BytesIO()
    Image.new('RGB', size, color).save(stream, format='PNG')
    return stream.getvalue()


def annotated():
    data = fixture()
    data['base_map'] = dict(id='a'*64, name='map.png', width=1000, height=700)
    for n, xy in zip(data['nodes'], ((200, 150), (700, 150), (500, 500))):
        n['map_anchor'] = dict(zip(('x', 'y'), xy))
    return data


class AnnotationModelTests(unittest.TestCase):
    def test_generate_requires_review_and_keeps_separate_coordinates(self):
        data = annotated()
        with self.assertRaisesRegex(ValueError, '核对确认'):
            model.generate_schematic(data)
        data['edges'][0]['reviewed'] = True
        generated = model.generate_schematic(data)
        self.assertNotEqual(generated['nodes'][0]['anchor'], data['nodes'][0]['anchor'])
        self.assertEqual([n['map_anchor'] for n in generated['nodes']], [n['map_anchor'] for n in data['nodes']])
        generated['nodes'][0].update(anchor={'x': 600, 'y': 500}, diagram_manual=True)
        again = model.generate_schematic(generated)
        self.assertEqual(again['nodes'][0]['anchor'], {'x': 600, 'y': 500})
        reset = model.generate_schematic(again, reset_layout=True)
        self.assertNotEqual(reset['nodes'][0]['anchor'], again['nodes'][0]['anchor'])
        self.assertEqual(reset['nodes'][0]['map_anchor'], data['nodes'][0]['map_anchor'])

    def test_analysis_ignores_unreviewed_and_unmarked_edges(self):
        data = annotated()
        a, b, c = data['nodes']
        data['edges'].append(dict(a=b['id'], b=c['id'], kind='line', reviewed=False))
        data['edges'][0]['reviewed'] = True
        with self.assertRaisesRegex(ValueError, '生成简图'):
            model.analysis_selection(data)
        generated = model.generate_schematic(data)
        nodes, edges = model.analysis_selection(generated)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(edges), 1)
        generated['nodes'][0]['map_anchor'] = None
        with self.assertRaises(ValueError):
            model.analysis_selection(generated)

    def test_variant_positions_share_station_without_copying_connections(self):
        data = annotated()
        a = data['nodes'][0]
        variant = deepcopy(a)
        variant.update(id='variant', curve=[99, 100])
        data['nodes'].append(variant)
        data['edges'][0]['reviewed'] = True
        generated = model.generate_schematic(data)
        self.assertEqual(len(generated['nodes']), 4)
        self.assertEqual(len(generated['edges']), 1)
        self.assertNotEqual(generated['nodes'][0]['anchor'], generated['nodes'][-1]['anchor'])
        self.assertEqual(generated['nodes'][0]['map_anchor'], generated['nodes'][-1]['map_anchor'])
        imported = dict(headers=data['headers'], source='new.xlsx', duplicates=0, ignored=0, entries=[
            dict(station=a['station'], voltage=a['voltage'], curve=[200, 300], keys=['new'], curve_headers=data['headers'])])
        synced = model.synchronize(data, imported)
        candidate = next(n for n in synced['nodes'] if n['present'])
        self.assertEqual(candidate['map_anchor'], a['map_anchor'])
        self.assertTrue(candidate['pending'])
        self.assertEqual(synced['edges'], data['edges'])

    def test_daily_price_sync_preserves_annotations_and_materializes_current_values(self):
        from openpyxl import load_workbook
        data = annotated(); data['edges'][0]['reviewed'] = True
        generated = model.generate_schematic(data)
        imported = dict(headers=data['headers'], source='daily.xlsx', duplicates=0, ignored=0, entries=[
            dict(station=n['station'], voltage=n['voltage'], curve=[30+i, 40+i], keys=[], curve_headers=data['headers'])
            for i, n in enumerate(data['nodes'])])
        synced = model.synchronize(generated, imported)
        self.assertEqual(synced['nodes'][0]['map_anchor'], data['nodes'][0]['map_anchor'])
        self.assertEqual(synced['edges'], generated['edges'])
        with tempfile.TemporaryDirectory() as folder:
            topology, prices, positions = model.materialize_analysis(synced, Path(folder))
            book = load_workbook(prices, data_only=True)
            self.assertEqual(book.active.cell(2, 3).value, 30)
            self.assertEqual(book.active.cell(3, 4).value, 41)
            book.close()
            saved = json.loads(positions.read_text())
            self.assertEqual(saved['nodes'][0]['anchor'], synced['nodes'][0]['anchor'])
            self.assertIn(synced['nodes'][0]['name'], topology.read_text())

    def test_invalid_metadata_and_annotation_coordinates_are_rejected(self):
        for change in (lambda d: d['nodes'][0].update(map_anchor={'x': -1, 'y': 3}),
                       lambda d: d['nodes'][0].update(map_anchor={'x': True, 'y': 3}),
                       lambda d: d['base_map'].update(id='../private'),
                       lambda d: d['edges'][0].update(reviewed='yes')):
            data = annotated(); change(data)
            with self.assertRaises(ValueError):
                model.validate_catalog(data)


class BasemapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(model, 'catalog_path', return_value=Path(self.temp.name)/'catalog.json')
        self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()

    def test_pdf_original_image_and_portable_backup_readback(self):
        stream = BytesIO()
        Image.open(BytesIO(image_bytes())).save(stream, format='PDF', resolution=72)
        original = stream.getvalue()
        self.assertEqual(len(PdfReader(BytesIO(original)).pages), 1)
        metadata = maps.store_image(original, '接线图.pdf')
        self.assertEqual((metadata['width'], metadata['height']), (1000, 700))
        data = annotated(); data['base_map'] = metadata
        backup = maps.export_backup(data)
        self.assertEqual(b64decode(backup['base_map_image']), maps.asset_path(metadata['id']).read_bytes())
        maps.asset_path(metadata['id']).unlink()
        restored = maps.import_backup(backup)
        self.assertEqual(restored, data)
        self.assertTrue(maps.asset_path(metadata['id']).exists())
        backup['base_map']['id'] = 'b'*64
        with self.assertRaisesRegex(ValueError, '校验失败'):
            maps.import_backup(backup)

    def test_default_is_local_and_invalid_pdf_does_not_replace_asset(self):
        source = Path(self.temp.name)/'map.png'; source.write_bytes(image_bytes())
        metadata = maps.install_default(source)
        self.assertEqual(maps.default_map(), metadata)
        bad = BytesIO(); pdf = PdfWriter(); pdf.add_blank_page(width=1000, height=700); pdf.write(bad)
        with self.assertRaises(ValueError):
            maps.store_image(bad.getvalue(), 'vector.pdf')
        self.assertEqual(maps.default_map(), metadata)
        with self.assertRaises(ValueError):
            maps.asset_path('../../secret')


class AnnotationServiceTests(unittest.TestCase):
    setUp = web.ServiceTests.setUp
    tearDown = web.ServiceTests.tearDown
    request = web.ServiceTests.request

    def test_image_upload_authentication_and_backup_roundtrip(self):
        raw = image_bytes()
        request = web.Request(self.service.origin+'/api/read-map', data=raw,
                              headers={'X-Editor-Token': self.service.token, 'Origin': self.service.origin})
        with web.urlopen(request) as response:
            metadata = json.load(response)
        headers = {'X-Editor-Token': self.service.token}
        with web.urlopen(web.Request(self.service.origin+'/api/base-map/'+metadata['id'], headers=headers)) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.read(), raw)
        with self.assertRaises(web.HTTPError):
            web.urlopen(self.service.origin+'/api/base-map/'+metadata['id'])
        with self.assertRaises(web.HTTPError):
            web.urlopen(web.Request(self.service.origin+'/api/base-map/../../secret', headers=headers))
        data = annotated(); data['base_map'] = metadata; data['edges'][0]['reviewed'] = True
        generated = self.request('generate', {'data': data})['data']
        self.assertTrue(generated['schematic_generated'])
        backup = self.request('backup', {'data': generated})['data']
        maps.asset_path(metadata['id']).unlink()
        restored = self.request('import-backup', {'data': backup})['data']
        self.assertEqual(restored, generated)
        self.request('apply', {'data': restored})
        self.assertEqual(model.load_catalog(model.applied_path())['nodes'], generated['nodes'])

    def test_default_attachment_preserves_legacy_snapshot_and_requires_review(self):
        path = Path(self.temp.name)/'map.png'; path.write_bytes(image_bytes())
        metadata = maps.install_default(path)
        state = self.request('state')
        self.assertEqual(state['data']['base_map'], metadata)
        self.assertNotIn('base_map', self.data)
        self.assertTrue(state['dirty'])
        with self.assertRaises(web.HTTPError):
            self.request('apply', {'data': state['data']})
        self.assertFalse(self.path.exists())


class AnnotationBrowserTests(unittest.TestCase):
    setUpClass = classmethod(web.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(web.BrowserTests.tearDownClass.__func__)
    setUp = web.BrowserTests.setUp
    tearDown = web.BrowserTests.tearDown
    matrix = web.BrowserTests.matrix

    def import_map(self, color='white', canceled=False):
        path = Path(self.temp.name)/'map.png'; path.write_bytes(image_bytes(color=color))
        self.page.locator('#mapFile').set_input_files(str(path))
        self.page.locator('#notice').filter(has_text='已取消更换' if canceled else '底图已载入').wait_for()
        self.page.locator('#baseImage[href^="blob:"]').wait_for(state='attached')

    def mark(self, index, x, y):
        identity = self.data['nodes'][index]['id']
        self.page.locator(f'#nodeList button[data-node-id="{identity}"]').click()
        self.page.evaluate('()=>new Promise(requestAnimationFrame)')
        bounds = self.page.locator('#canvas').bounding_box(); m = self.matrix()
        self.page.mouse.click(bounds['x']+m['x']+x*m['k'], bounds['y']+m['y']+y*m['k'])
        self.page.locator(f'.node[data-node="{identity}"]').wait_for(state='attached')

    def save(self):
        self.page.locator('#save').click()
        self.page.locator('#notice').filter(has_text='草稿已保存').wait_for()
        return model.load_catalog()

    def test_annotation_review_generation_and_independent_layout_end_to_end(self):
        self.import_map()
        self.assertEqual(self.page.locator('.node').count(), 0)
        self.mark(0, 200, 150)
        self.assertEqual(self.page.locator('#name').input_value(), '乙站')
        self.mark(1, 700, 150)
        marked = self.save()
        self.assertAlmostEqual(marked['nodes'][0]['map_anchor']['x'], 200, delta=3)
        self.assertEqual(marked['nodes'][0]['anchor'], self.data['nodes'][0]['anchor'])
        self.assertEqual(self.page.locator('.edgeHit').count(), 0)
        self.page.locator('#stepDiagram').click()
        self.page.locator('#notice').filter(has_text='核对确认').wait_for()
        self.page.locator('#stepReview').click()
        self.page.locator('#nextReview').click()
        self.page.locator('#reviewEdge').click()
        self.page.get_by_role('button', name='取消', exact=True).click()
        self.assertFalse(self.save()['edges'][0].get('reviewed', False))
        self.page.locator('#reviewEdge').click()
        self.page.locator('#confirmConnection').click()
        self.page.locator('#edgeReviewStatus').filter(has_text='已核对确认').wait_for()
        self.page.locator('#stepDiagram').click()
        self.page.locator('#notice').filter(has_text='简图已生成').wait_for()
        self.assertEqual(self.page.locator('.edgeHit').count(), 1)
        self.assertEqual(self.page.locator('#baseImage').evaluate('(e)=>getComputedStyle(e).display'), 'none')
        generated = self.save()
        self.assertTrue(generated['schematic_generated'])
        self.assertEqual(generated['nodes'][0]['map_anchor'], marked['nodes'][0]['map_anchor'])
        # Move the simple diagram; annotations must be unchanged.
        circle = self.page.locator('.node circle').first.bounding_box()
        x, y = circle['x']+circle['width']/2, circle['y']+circle['height']/2
        self.page.mouse.move(x,y); self.page.mouse.down(); self.page.mouse.move(x+25,y+20,steps=4); self.page.mouse.up()
        self.page.locator('#undo:not([disabled])').wait_for()
        moved = self.save()
        self.assertEqual(moved['nodes'][0]['map_anchor'], marked['nodes'][0]['map_anchor'])
        self.assertNotEqual(moved['nodes'][0]['anchor'], generated['nodes'][0]['anchor'])
        self.page.locator('#apply').click()
        self.page.locator('#notice').filter(has_text='已应用到分析').wait_for()
        self.assertEqual(len(model.analysis_selection(model.load_catalog(model.applied_path()))[1]), 1)

    def test_same_station_variant_marker_and_portable_browser_backup(self):
        self.data['nodes'][2].update(station=self.data['nodes'][0]['station'], curve=[40, 50])
        backup = Path(self.temp.name)/'variants.json'; backup.write_text(json.dumps(self.data))
        self.page.once('dialog', lambda d: d.accept())
        self.page.locator('#jsonFile').set_input_files(str(backup))
        self.page.locator('#notice').filter(has_text='备份已导入').wait_for()
        self.import_map(); self.page.locator('#continuous').uncheck()
        self.mark(0, 200, 150)
        saved = self.save()
        self.assertEqual(saved['nodes'][0]['map_anchor'], saved['nodes'][2]['map_anchor'])
        self.assertNotEqual(saved['nodes'][0]['curve'], saved['nodes'][2]['curve'])
        self.assertEqual(self.page.locator('.node').count(), 1)
        self.page.locator('details.more > summary').click()
        with self.page.expect_download() as download:
            self.page.locator('#export').click()
        target = Path(self.temp.name)/'portable.json'; download.value.save_as(str(target))
        exported = json.loads(target.read_text())
        self.assertTrue(exported['base_map_image'])
        maps.asset_path(exported['base_map']['id']).unlink()
        self.page.locator('#jsonFile').set_input_files(str(target))
        self.page.locator('#notice').filter(has_text='备份已导入').wait_for()
        self.assertTrue(maps.asset_path(exported['base_map']['id']).exists())
        self.assertEqual(self.page.locator('.node').count(), 1)

    def test_replacing_map_requires_confirmation_and_can_be_undone(self):
        self.import_map(); self.page.locator('#continuous').uncheck(); self.mark(0, 200, 150)
        saved = self.save()
        self.page.once('dialog', lambda d: d.dismiss())
        self.import_map('gray', canceled=True)
        self.assertEqual(self.page.locator('.node').count(), 1)
        self.page.once('dialog', lambda d: d.accept())
        self.import_map('gray')
        self.assertEqual(self.page.locator('.node').count(), 0)
        self.page.locator('#undo').click()
        restored = self.save()
        self.assertEqual(restored['base_map'], saved['base_map'])
        self.assertEqual(restored['nodes'][0]['map_anchor'], saved['nodes'][0]['map_anchor'])
