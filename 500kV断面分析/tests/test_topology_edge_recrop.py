"""Bounded edge recrops retain coordinates, provenance and disagreements."""
from copy import deepcopy
import unittest
from PIL import Image
from toolbox_shared.section_prelabel import edge_supplement, deduplicate


class EdgeRecropTests(unittest.TestCase):
    def record(self, text='测试站', x=1220, y=500, width=180, height=20):
        return dict(id='a',text=text,confidence=.99,box=dict(x=x,y=y,width=width,height=height))

    def audit(self, records):
        return next(r['edge_audit'] for r in records if 'edge_audit' in r)

    def test_neighbor_reuse_without_inference(self):
        records=edge_supplement(Image.new('RGB',(3000,2500)),[self.record(x=1430,width=40)],lambda _:self.fail('neighbor covers context'))
        self.assertEqual(self.audit(records)['proposals'][0]['status'],'reused-neighbor')
        self.assertEqual(self.audit(records)['actual_detection_calls'],0)
        self.assertTrue(self.audit(records)['legacy_provenance_inferred'])

    def test_centered_view_global_coordinates_and_conflict(self):
        r=self.record(x=1360,width=180)
        # Source really was the second tile and touches its left edge.
        r['views']=[dict(id='tile:1',bounds=[1200,0,2700,1500],scale=1)]
        # Force a long label whose full context fits neither overlapping tile.
        r['box'].update(x=1100,width=350)
        r['views']=[dict(id='tile:0',bounds=[0,0,1500,1500],scale=1)]
        calls=[]
        def detect(view):
            calls.append(view.size)
            return [dict(text='矛盾站',confidence=.95,box=dict(x=575,y=500,width=350,height=20))]
        records=edge_supplement(Image.new('RGB',(3000,2500)),[r],detect,station_names=['测试站','矛盾站'])
        self.assertEqual(len(calls),1)
        added=next(v for v in records if v['text']=='矛盾站')
        self.assertEqual(added['box']['x'],1100)
        self.assertTrue(added['ocr_conflict'])
        self.assertTrue(next(v for v in records if v['text']=='测试站')['ocr_conflict'])
        self.assertEqual(added['views'][0]['provenance'],'actual-inference')

    def test_zero_budget_retains_unresolved_proposal(self):
        records=edge_supplement(Image.new('RGB',(3000,2500)),[self.record(x=1100,width=350)],lambda _:self.fail(),budget=0)
        self.assertEqual(self.audit(records)['proposals'][0]['status'],'budget-exhausted')

    def test_physical_edge_never_fabricates_context(self):
        records=edge_supplement(Image.new('RGB',(3000,2500)),[self.record(x=1)],lambda _:self.fail())
        self.assertEqual(self.audit(records)['proposals'][0]['status'],'physical-image-edge')

    def test_duplicate_preserves_both_views_and_conflict(self):
        a=self.record();a['views']=[dict(id='one')]
        b=deepcopy(a);b['confidence']=.8;b['views']=[dict(id='two')];b['ocr_conflict']=True
        result=deduplicate([a,b])
        self.assertEqual(len(result),1)
        self.assertEqual([v['id'] for v in result[0]['views']],['one','two'])
        self.assertTrue(result[0]['ocr_conflict'])

    def test_different_names_and_different_locations_retained(self):
        a=self.record();b=self.record('另一站');c=self.record(x=1800)
        self.assertEqual(len(deduplicate([a,b,c])),3)

    def test_original_records_unchanged(self):
        original=[self.record()];before=deepcopy(original)
        edge_supplement(Image.new('RGB',(3000,2500)),original,lambda _:[])
        self.assertEqual(original,before)

    def test_shared_crop_is_inferred_once(self):
        a=self.record(x=1100,width=350);b=self.record('另一站',x=1100,width=350)
        calls=[]
        result=edge_supplement(Image.new('RGB',(3000,2500)),[a,b],lambda view:calls.append(view.size) or [])
        self.assertEqual(len(calls),1)
        self.assertEqual(self.audit(result)['actual_detection_calls'],1)

    def test_fragment_difference_kept_without_rival_station(self):
        r=self.record(x=1100,width=350)
        result=edge_supplement(Image.new('RGB',(3000,2500)),[r],lambda view:[dict(text='测试',confidence=1,box=dict(x=575,y=500,width=350,height=20))],station_names=['测试站'])
        base=next(v for v in result if v['text']=='测试站')
        self.assertFalse(base.get('ocr_conflict',False))
        self.assertTrue(base['view_disagreements'])

    def test_same_name_keeps_existing_geometry_and_raw_views(self):
        r=self.record(x=1100,width=350)
        result=edge_supplement(Image.new('RGB',(3000,2500)),[r],lambda view:[dict(text='测试站',confidence=1,box=dict(x=580,y=500,width=350,height=20))])
        base=next(v for v in result if v['text']=='测试站')
        self.assertEqual(base['box'],r['box'])
        actual=next(v for v in base['views'] if v['provenance']=='actual-inference')
        self.assertEqual(actual['box']['x'],1105)
