import re
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from src.generate_500kv_blockage_map import generate_blockage_map


class MapComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Playwright unavailable')
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f'Chromium unavailable: {exc}')
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        topology, raw = root / 'topology.md', root / 'raw.xlsx'
        topology.write_text('## 2. 接线\n### 测试\n甲-乙、乙-丙、丙-丁\n## 3. 约定\n', encoding='utf-8')
        book = Workbook()
        sheet = book.active
        sheet.append(['节点名称', '数据项', *[f'{i//4:02}:{i%4*15:02}' for i in range(96)]])
        for name, values in [('甲', list(range(96))), ('乙', [200-i for i in range(96)]),
                             ('丙', [None if i == 5 else -i for i in range(96)]), ('丁', [None]*96)]:
            sheet.append([f'其他{name}站500kV', '电价(元/MWh)', *values])
        book.save(raw)
        book.close()
        cls.url = generate_blockage_map(topology, raw, root).html_path.as_uri()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.directory.cleanup()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1440, 'height': 1000})
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.set_default_timeout(5000)
        self.page.goto(self.url)

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def node(self, name):
        self.page.locator(f'.node-group[data-node-name="{name}"]').press('Enter')

    def edge(self, name):
        self.page.get_by_role('button', name=re.compile('^' + re.escape(name) + '，')).press('Enter')

    def series(self):
        return self.page.locator('#priceChart path[data-series]').evaluate_all('(paths) => paths.map(p => p.dataset.series)')

    def test_mixed_selections_deduplicate_endpoints_and_toggle(self):
        self.page.locator('#toggleAnalysis').click()
        self.edge('甲-乙')
        self.edge('乙-丙')
        self.node('甲')
        self.assertEqual(self.series(), ['甲', '乙', '丙'])
        self.assertEqual(self.page.locator('#analysisSelection button').count(), 3)
        self.node('甲')
        self.assertEqual(self.series(), ['甲', '乙', '丙'])
        self.page.get_by_role('button', name='取消选择甲-乙', exact=True).click()
        self.assertEqual(self.series(), ['乙', '丙'])
        self.page.locator('#showRegionCurves').click()
        self.page.locator('#showTopology').click()
        self.assertEqual(self.series(), ['乙', '丙'])
        self.page.locator('#gapThreshold').fill('150')
        self.page.locator('#gapThreshold').press('Tab')
        self.assertEqual(self.series(), ['乙', '丙'])
        self.page.locator('#clearAnalysis').click()
        self.assertEqual(self.series(), [])
        self.node('丁')
        self.assertIn('缺少', self.page.locator('#priceChart').text_content())
        self.assertEqual(self.page.locator('#analysisLegend [data-node="丁"]').inner_text(), '暂无数据')
        self.page.locator('#clearAnalysis').click()
        self.assertTrue(self.page.locator('#clearAnalysis').is_disabled())

    def test_toggle_keeps_selection_then_restores_single_selection(self):
        self.node('甲')
        self.page.locator('#toggleAnalysis').click()
        self.assertEqual(self.series(), ['甲'])
        self.node('乙')
        self.assertEqual(self.series(), ['甲', '乙'])
        self.page.locator('#toggleAnalysis').click()
        self.assertTrue(self.page.locator('#selectionDetails').is_hidden())
        self.edge('乙-丙')
        self.assertEqual(self.series(), ['乙', '丙'])
        self.assertTrue(self.page.locator('#analysisControls').is_hidden())

    def test_missing_points_readout_and_threshold_changes(self):
        self.page.locator('#toggleAnalysis').click()
        self.node('丙')
        self.node('丁')
        self.assertEqual(self.series(), ['丙', '丁'])
        self.assertEqual(self.page.locator('#priceChart path[data-series="丙"]').get_attribute('d').count('M'), 2)
        from playwright.sync_api import expect
        self.page.locator('#topologyTime').fill('5')
        expect(self.page.locator('#sourceText')).to_contain_text('01:15')
        self.assertEqual(self.page.locator('#analysisLegend [data-node="丙"]').inner_text(), '暂无数据')
        self.page.locator('#topologyTime').fill('6')
        expect(self.page.locator('#sourceText')).to_contain_text('01:30')
        self.assertEqual(self.page.locator('#analysisLegend [data-node="丙"]').inner_text(), '-6.00')
        self.page.locator('#gapThreshold').fill('150')
        self.page.locator('#gapThreshold').press('Tab')
        self.assertEqual(self.series(), ['丙', '丁'])
        self.assertEqual(self.page.locator('#analysisLegend [data-node="丁"]').inner_text(), '暂无数据')

    def test_analysis_layout_floats_chart_and_restores_sidebar(self):
        side = self.page.locator('.side')
        original_width = side.bounding_box()['width']
        self.page.locator('#toggleAnalysis').click()
        self.node('甲')
        self.assertTrue(side.is_hidden())
        self.assertGreater(self.page.locator('.map-pane').bounding_box()['width'], 1435)
        panel = self.page.locator('#selectionDetails')
        self.assertEqual(panel.evaluate('(el) => el.parentElement.tagName'), 'BODY')
        self.assertEqual(panel.evaluate('(el) => getComputedStyle(el).position'), 'fixed')
        alpha = panel.evaluate('(el) => Number(getComputedStyle(el).backgroundColor.match(/[\d.]+/g).at(-1))')
        self.assertAlmostEqual(alpha, 0.15, delta=0.001)
        chart = self.page.locator('#priceChart').bounding_box()
        self.assertAlmostEqual(chart['width'], 444, delta=1)
        self.assertAlmostEqual(chart['height'], 345.33, delta=1)
        before = panel.bounding_box()
        handle = self.page.locator('#detailTitle').bounding_box()
        transform = self.page.locator('#mapViewport').get_attribute('transform')
        self.page.mouse.move(handle['x'] + 50, handle['y'] + 10)
        self.page.mouse.down()
        self.page.mouse.move(handle['x'] - 70, handle['y'] + 90, steps=5)
        self.page.mouse.up()
        after = panel.bounding_box()
        self.assertAlmostEqual(after['x'], before['x'] - 120, delta=2)
        self.assertEqual(self.page.locator('#mapViewport').get_attribute('transform'), transform)
        self.node('乙')
        self.node('丙')
        self.assertAlmostEqual(self.page.locator('#priceChart').bounding_box()['height'], chart['height'], delta=1)
        self.assertLess(panel.bounding_box()['height'], 520)
        self.assertEqual(self.page.locator('#analysisLegend').evaluate('(el) => getComputedStyle(el).display'), 'grid')
        self.assertAlmostEqual(panel.bounding_box()['x'], after['x'], delta=2)
        self.page.locator('#detailTitle').press('ArrowLeft')
        self.assertAlmostEqual(panel.bounding_box()['x'], after['x'] - 10, delta=2)
        self.page.locator('#showRegionCurves').click()
        self.assertTrue(panel.is_hidden())
        self.page.locator('#showTopology').click()
        self.assertTrue(panel.is_visible())
        self.assertEqual(self.page.locator('#focusMode').count(), 0)
        self.page.locator('#toggleAnalysis').click()
        self.assertTrue(side.is_visible())
        self.assertEqual(panel.evaluate('(el) => el.parentElement.className'), 'side')
        self.assertAlmostEqual(side.bounding_box()['width'], original_width, delta=1)

    def test_narrow_screen_and_chart_pointer_readout(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.locator('#toggleAnalysis').click()
        self.node('甲')
        self.node('乙')
        panel = self.page.locator('#selectionDetails').bounding_box()
        toolbar = self.page.locator('.map-toolbar').bounding_box()
        self.assertGreaterEqual(panel['y'], toolbar['y'] + toolbar['height'])
        self.assertLessEqual(panel['y'] + panel['height'], 844)
        hit = self.page.locator('#priceChart .price-crosshair-hit')
        hit.scroll_into_view_if_needed()
        hit.hover(position={'x': 100, 'y': 50})
        self.assertRegex(self.page.locator('#analysisLegend [data-node="甲"]').inner_text(), r'^\d+\.00$')
        self.assertEqual(self.series(), ['甲', '乙'])
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 391)
