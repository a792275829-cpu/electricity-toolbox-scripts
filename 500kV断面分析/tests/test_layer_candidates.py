import unittest
from tools.prepare_layer_candidates import enrich, in_tiles

class CandidatesTest(unittest.TestCase):
    def test_evidence_keeps_existing_work_and_excludes_outside_ocr(self):
        data={'nodes':[{'name':'测试', 'tiles':['r01c01'], 'anchor':{'x':3,'y':4}, 'position_status':'已确认'}]}
        old={'nodes':[{'name':'测试','status':'verified','anchor':{'x':10,'y':20}}]}
        ocr={'nodes':[{'text_candidates':[{'record_id':'1','record_text':'测试','box':{'x':100,'y':200,'width':20,'height':10}}, {'record_id':'2','record_text':'测试','box':{'x':3000,'y':3000,'width':20,'height':10}}]}]}
        pins=[{'id':'N1','name':'测试','x':4000,'y':4000}]
        stats=enrich(data,old,ocr,pins);n=data['nodes'][0]
        self.assertEqual(n['anchor'],{'x':3,'y':4});self.assertEqual(n['position_status'],'已确认')
        self.assertEqual(stats['有候选节点'],1);self.assertEqual(len(n['position_candidates']),3)
        self.assertTrue(n['position_candidates'][0]['warning'])
        self.assertEqual(n['position_candidates'][2]['point'],{'x':110,'y':205})
    def test_tile_geometry(self):
        self.assertTrue(in_tiles({'x':1122.2,'y':943.75}, ['r02c02']))
        self.assertFalse(in_tiles({'x':0,'y':0}, ['r02c02']))

if __name__=='__main__': unittest.main()
