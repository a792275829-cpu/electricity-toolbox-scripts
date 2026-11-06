from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from toolbox_shared import section_prelabel as auto, section_topology as model
from toolbox_shared.section_vision_review import configured_reviewer
import test_topology_annotation as annotation
import test_topology_web as web
from importlib.util import find_spec


def candidate(x=300, y=200):
    return dict(point=dict(x=x,y=y), text='甲站', ocr_engine='macos-vision', vision_status='未配置', match_score=1, circle_score=.8, circle_margin=.1)


class PrelabelTests(unittest.TestCase):
    def test_tiling_covers_edges_and_preserves_overlap(self):
        boxes=auto.tiles(7111,5025)
        self.assertEqual(boxes[0],(0,0,1500,1500))
        self.assertEqual(boxes[-1][2:],(7111,5025))
        for y in range(0,5025,100):
            for x in range(0,7111,100):
                self.assertTrue(any(a<=x<c and b<=y<d for a,b,c,d in boxes))
        self.assertEqual(auto.tiles(100,70),[(0,0,100,70)])

    def test_duplicate_overlap_does_not_erase_distinct_same_name(self):
        def rec(x,confidence):
            return dict(text='甲', confidence=confidence,box=dict(x=x,y=20,width=50,height=20))
        records=auto.deduplicate([rec(20,.5),rec(22,.9),rec(600,.9)])
        self.assertEqual(len(records),2)
        self.assertEqual(records[0]['confidence'],.9)

    def test_sampling_is_bounded_and_includes_unresolved_stratum(self):
        groups=[(str(i),500) for i in range(50)]
        matches={g:[] for g in groups}
        result=auto.sample_groups(groups,matches,30)
        self.assertEqual(len(set(result)),30)
        self.assertEqual(auto.sample_groups([],{},30),[])

    @unittest.skipUnless(find_spec('cv2'), 'optional OpenCV is not installed')
    def test_prelabels_preserve_confirmed_positions_edges_and_original(self):
        data=annotation.annotated(); original=deepcopy(data)
        data['nodes'][0]['map_anchor']=None
        records=[dict(id='1',text='甲站',confidence=.99,category='node',box=dict(x=100,y=100,width=50,height=25))]
        circles=[dict(x=180,y=120,score=.9,radius=30)]
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'map.png';Image.new('RGB',(1000,700),'white').save(image)
            with patch.object(auto.maps,'asset_path',return_value=image), patch.object(auto,'detect_circle_candidates',return_value=circles):
                result=auto.prelabel(data,records=records)
                self.assertEqual(result['report']['candidates'],1)
                self.assertIsNone(result['data']['nodes'][0]['map_anchor'])
                self.assertEqual(result['data']['nodes'][0]['map_candidate']['point'],dict(x=180,y=120))
                self.assertEqual(result['data']['nodes'][1:],original['nodes'][1:])
                self.assertEqual(result['data']['edges'],original['edges'])
                self.assertNotIn('map_candidate',data['nodes'][0])
                reviewed=auto.prelabel(data,records=records,reviewer=lambda *_: None)
                self.assertEqual(reviewed['report']['candidates'],0)
                self.assertEqual(reviewed['report']['rows'][0]['vision_status'],'不一致')

    @unittest.skipUnless(find_spec('cv2'), 'optional OpenCV is not installed')
    def test_ambiguous_cross_voltage_does_not_choose_nearest(self):
        data=annotation.annotated()
        for n in data['nodes']: n.pop('map_anchor',None)
        data['nodes'][1].update(station=data['nodes'][0]['station'],voltage=220)
        data['edges']=[]
        records=[dict(id='1',text='甲站',confidence=.99,category='node',box=dict(x=100,y=100,width=50,height=25))]
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'map.png';Image.new('RGB',(1000,700),'white').save(image)
            with patch.object(auto.maps,'asset_path',return_value=image),patch.object(auto,'detect_circle_candidates',return_value=[dict(x=180,y=120,score=.9,radius=30)]):
                result=auto.prelabel(data,records=records)
                self.assertEqual(result['report']['candidates'],0)

    def test_candidates_validated_and_never_qualify_for_generation(self):
        data=annotation.annotated()
        for n in data['nodes']:
            n['map_anchor']=None;n['map_candidate']=candidate()
        data['edges'][0]['reviewed']=True
        model.validate_catalog(data)
        with self.assertRaises(ValueError):model.generate_schematic(data)
        for value in (dict(x=True,y=20),dict(x=-1,y=20),dict(x=float('nan'),y=20),dict(x=3,y=900)):
            data['nodes'][0]['map_candidate']['point']=value
            with self.assertRaisesRegex(ValueError,'初标坐标'):model.validate_catalog(data)

    def test_configuration_and_model_failure_do_not_expose_secrets(self):
        with patch.dict('os.environ',{},clear=True):
            self.assertIsNone(configured_reviewer('none'))
            with self.assertRaisesRegex(ValueError,'TOPOLOGY_VISION'):configured_reviewer('qwen')
        with patch.dict('os.environ',dict(TOPOLOGY_VISION_MODEL='qwen',TOPOLOGY_VISION_BASE_URL='http://remote.example',TOPOLOGY_VISION_API_KEY='secret'),clear=True):
            with self.assertRaisesRegex(ValueError,'HTTPS'):configured_reviewer('qwen')


class PrelabelBrowserTests(unittest.TestCase):
    setUpClass=classmethod(web.BrowserTests.setUpClass.__func__)
    tearDownClass=classmethod(web.BrowserTests.tearDownClass.__func__)
    setUp=web.BrowserTests.setUp
    tearDown=web.BrowserTests.tearDown
    matrix=web.BrowserTests.matrix
    import_map=annotation.AnnotationBrowserTests.import_map
    save=annotation.AnnotationBrowserTests.save

    def install_candidate(self):
        self.import_map()
        data=self.save();data['nodes'][0]['map_candidate']=candidate()
        report=dict(sampled=3,matched=1,candidates=1,vision_reviewed=0)
        with patch.object(auto,'prelabel',return_value=dict(data=data,report=report)):
            self.page.locator('#autoLabel').click()
            self.page.locator('#notice').filter(has_text='小样完成').wait_for()
        self.page.locator('#nextCandidate').click()
        self.assertEqual(self.page.locator('.node.candidate').count(),1)

    def test_pending_save_reload_move_accept_and_undo(self):
        self.install_candidate()
        before=self.save()
        self.assertIsNone(before['nodes'][0].get('map_anchor'))
        self.page.locator('details.more > summary').click()
        self.page.locator('#reload').click()
        self.page.locator('#notice').filter(has_text='先选左侧').wait_for()
        self.page.locator('#nextCandidate').click()
        self.page.evaluate('()=>new Promise(requestAnimationFrame)')
        circle=self.page.locator('.node.candidate circle').bounding_box()
        x,y=circle['x']+circle['width']/2,circle['y']+circle['height']/2
        self.page.mouse.move(x,y);self.page.mouse.down();self.page.mouse.move(x+30,y+20,steps=4);self.page.mouse.up()
        self.page.locator('#undo:not([disabled])').wait_for()
        moved=self.save()
        self.assertNotEqual(moved['nodes'][0]['map_candidate']['point'],before['nodes'][0]['map_candidate']['point'])
        self.assertIsNone(moved['nodes'][0].get('map_anchor'))
        self.page.locator('#acceptCandidate').click()
        self.page.locator('.node.candidate').wait_for(state='detached')
        accepted=self.save()
        self.assertEqual(accepted['nodes'][0]['map_anchor'],moved['nodes'][0]['map_candidate']['point'])
        self.assertNotIn('map_candidate',accepted['nodes'][0])
        self.assertFalse(accepted['edges'][0].get('reviewed',False))
        self.page.locator('#undo').click()
        restored=self.save()
        self.assertIsNone(restored['nodes'][0].get('map_anchor'))
        self.assertIn('map_candidate',restored['nodes'][0])

    def test_reject_and_backup_readback(self):
        self.install_candidate()
        pending=self.save()
        exported=auto.maps.export_backup(pending)
        restored=auto.maps.import_backup(json.loads(json.dumps(exported)))
        self.assertEqual(restored['nodes'][0]['map_candidate'],pending['nodes'][0]['map_candidate'])
        self.page.locator('#rejectCandidate').click()
        self.page.locator('.node.candidate').wait_for(state='detached')
        rejected=self.save()
        self.assertNotIn('map_candidate',rejected['nodes'][0])
        self.assertIsNone(rejected['nodes'][0].get('map_anchor'))
        self.page.locator('#undo').click()
        self.assertIn('map_candidate',self.save()['nodes'][0])

    def test_suspect_candidate_warning_is_visible_and_survives_backup(self):
        warning = candidate(); warning['warning'] = '疑似错配，请重点核对'
        with patch(__name__+'.candidate', return_value=warning):
            self.install_candidate()
        self.assertIn(warning['warning'], self.page.locator('#candidateInfo').inner_text())
        saved = self.save()
        restored = auto.maps.import_backup(auto.maps.export_backup(saved))
        self.assertEqual(restored['nodes'][0]['map_candidate']['warning'], warning['warning'])

    def test_replacing_map_clears_candidates_and_undo_restores(self):
        self.install_candidate()
        self.page.once('dialog',lambda d:d.accept())
        self.import_map('gray')
        self.assertFalse(any(n.get('map_candidate') for n in self.save()['nodes']))
        self.page.locator('#undo').click()
        self.assertIn('map_candidate',self.save()['nodes'][0])
