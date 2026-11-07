"""Precision regressions: preserve abstention and measure geometry independently."""
from copy import deepcopy
from io import BytesIO
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np
from PIL import Image
from toolbox_shared import section_prelabel as auto
from toolbox_shared.section_annotation_geometry import refine_circle, detect_circle_candidates, dark_ring_candidates
from toolbox_shared.section_vision_review import configured_reviewer, review_images, decisions_reviewer, review_key, circle_signature
import test_topology_annotation as annotation


class PrecisionTests(unittest.TestCase):
    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_partial_symbol_conflict_abstains_without_promoting_unfitted_coordinates(self):
        data=annotation.annotated()
        for n in data['nodes']:n.pop('map_anchor',None)
        data['nodes'][0]['voltage']=220;data['edges']=[]
        record=self.record(data['nodes'][0]['station'],.99)
        far=dict(x=200,y=150,radius=20,score=.9,refined=True,label_distance=75)
        partial=dict(x=110,y=100,radius=28,score=.7,refined=False,label_distance=30,edge_support=.8,dark_ring_coverage=.8)
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'map.png';Image.new('RGB',(1000,700),'white').save(image)
            with patch.object(auto.maps,'asset_path',return_value=image),patch.object(auto,'detect_circle_candidates',return_value=[far,partial]):
                result=auto.prelabel(data,records=[record],sample=[(data['nodes'][0]['station'],220)])
        row=result['report']['rows'][0]
        self.assertIsNone(row['point']);self.assertEqual(row['partial_symbol_conflicts'],[partial])
        self.assertNotIn('map_candidate',result['data']['nodes'][0])
        weak=deepcopy(partial);weak['edge_support']=.3
        self.assertEqual(auto.partial_symbol_conflicts(far,[far,weak]),[])

    def test_visual_candidate_filter_keeps_original_identity_and_skips_empty_requests(self):
        env=dict(TOPOLOGY_VISION_MODEL='model',TOPOLOGY_VISION_BASE_URL='https://example.invalid/v1')
        weak=dict(x=100,y=100,radius=30,refined=False,edge_support=.3,dark_ring_coverage=.4)
        partial=dict(x=180,y=120,radius=30,refined=False,edge_support=.8,dark_ring_coverage=.8)
        payload=dict(choices=[dict(message=dict(content='{"index":0}'))])
        with patch.dict('os.environ',env), patch('toolbox_shared.section_vision_review.urlopen',return_value=BytesIO(json.dumps(payload).encode())) as fetch:
            reviewer=configured_reviewer('qwen');image=Image.new('RGB',(300,200))
            self.assertIsNone(reviewer(image,('目标',220),self.record(),[weak]))
            self.assertEqual(reviewer.request_count,0);fetch.assert_not_called()
            self.assertIs(reviewer(image,('目标',220),self.record(),[weak,partial]),partial)
            self.assertEqual(reviewer.request_count,1)

    def test_loopback_inference_bypasses_system_proxy_without_global_changes(self):
        from types import SimpleNamespace
        env=dict(TOPOLOGY_VISION_MODEL='model',TOPOLOGY_VISION_BASE_URL='http://127.0.0.1:19086/v1')
        circles=[dict(x=200,y=80,radius=30)]
        calls=[]
        def direct(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(json.dumps(dict(choices=[dict(message=dict(content='{"index":0}'))])).encode())
        with patch.dict('os.environ',env), patch('toolbox_shared.section_vision_review.build_opener',return_value=SimpleNamespace(open=direct)), patch('toolbox_shared.section_vision_review.urlopen',side_effect=AssertionError('must not use proxy opener')):
            review=configured_reviewer('qwen')
            self.assertIs(review(Image.new('RGB',(1000,700)),('目标',220),self.record(),circles),circles[0])
        self.assertEqual(calls,['http://127.0.0.1:19086/v1/chat/completions'])

    def test_loopback_vision_service_is_reported_as_local_without_credentials(self):
        for endpoint, expected in [('http://127.0.0.1:19086/v1','local-http'),
                                   ('https://example.invalid/v1','remote-api')]:
            with patch.dict('os.environ',dict(TOPOLOGY_VISION_MODEL='model',TOPOLOGY_VISION_BASE_URL=endpoint,TOPOLOGY_VISION_API_KEY='secret')):
                reviewer = configured_reviewer('qwen')
                self.assertEqual(reviewer.inference_source, dict(provider='qwen',model='model',location=expected))

    def test_wrapped_solar_label_keeps_facility_identity(self):
        self.assertEqual(auto.vocabulary_hits('公子岭光伏', ['公子岭光伏电','公子岭']), {'公子岭光伏电'})
        self.assertEqual(auto.vocabulary_hits('公子岭风电', ['公子岭光伏电']), set())
        self.assertEqual(auto.vocabulary_hits('公子岭光伏电站', ['公子岭光伏电']), {'公子岭光伏电'})

    def test_merged_bus_token_requires_actual_two_read_agreement(self):
        record = self.record('88凯旋B', .99)
        original = deepcopy(record)
        views = []
        def read(view):
            views.append(view.size)
            return [('凯旋B', .99)]
        result = auto.recover_merged_labels(Image.new('RGB',(300,200)),[record],['凯旋'],read)
        self.assertEqual(record, original)
        self.assertEqual(len(views), 2)
        self.assertEqual(result[0], original)
        self.assertEqual(result[-1]['text'], '凯旋B')
        self.assertEqual(result[-1]['proposal_engine'], 'ppocr-label-token')
        self.assertEqual(result[-1]['source_record_id'], 'r')
        self.assertLess(result[-1]['box']['width'], original['box']['width'])

    def test_merged_token_cannot_turn_neighbor_text_into_target_name(self):
        record = self.record('双岗B科然电', .99)
        for values in ([('双岗B', .99), ('双安B', .99)], [('科然电', .99)]*2,
                       [('双岗B', .99), ('双岗B', .7)]):
            replies = iter(values)
            result = auto.recover_merged_labels(Image.new('RGB',(300,200)),[record],['双岗','双安','科然电'],lambda _: [next(replies)])
            self.assertEqual(result, [record])

    def test_plain_short_names_and_bus_labels_do_not_create_fuzzy_proposals(self):
        records = [self.record('双安B', .99),self.record('凯旅', .99),self.record('F1810', .99)]
        self.assertEqual(auto.merged_label_proposals(records,['双岗','凯旋']), [])

    def test_numeric_and_paired_names_need_exact_vocabulary_and_two_image_reads(self):
        names=['丽晶','智合','建云','新平安','平安']
        numeric=self.record('丽晶934',.99);paired=self.record('智合建云',.99)
        proposals=auto.merged_label_proposals([numeric,paired,self.record('新平安',.99)],names)
        self.assertEqual({p['text'] for p in proposals},{'丽晶','智合','建云'})
        self.assertEqual({p['proposal_source']['kind'] for p in proposals},{'numeric','paired-names'})
        result=auto.recover_merged_labels(Image.new('RGB',(300,200)),[paired],names,lambda _: [('智合',.99)])
        # A read of the first half cannot manufacture the second station.
        self.assertEqual([p['text'] for p in result],['智合建云','智合'])

    def test_combined_bus_suffix_is_exact_evidence_not_fuzzy_correction(self):
        self.assertEqual(auto.vocabulary_hits('盘允BY', ['盘允','盘龙']), {'盘允'})
        self.assertEqual(auto.vocabulary_hits('盘允XYZ', ['盘允']), set())
        self.assertEqual(auto.vocabulary_hits('盘龙BY', ['盘允']), set())

    def test_competing_station_label_abstains_without_inventing_symbol(self):
        own = dict(id='own', box=dict(x=5982, y=2262, width=95, height=37))
        other = dict(id='other', box=dict(x=6120, y=2220, width=60, height=30))
        circle = dict(x=6099, y=2255, radius=30)
        evidence = auto.competing_labels(circle, own, '目标', [('邻站', other)])
        self.assertEqual([r['station'] for r in evidence], ['邻站'])
        self.assertEqual(circle, dict(x=6099, y=2255, radius=30))
        self.assertEqual(auto.competing_labels(circle, own, '目标', [('目标', other)]), [])

    def test_far_label_is_not_competing_evidence(self):
        own = self.record('目标', .99)
        other = deepcopy(own); other.update(id='other'); other['box']['x'] = 500
        self.assertEqual(auto.competing_labels(dict(x=80,y=100,radius=30),own,'目标',[('远站',other)]), [])

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_protected_neighbor_must_independently_own_circle_before_abstention(self):
        data = annotation.annotated()
        own, neighbor = data['nodes'][:2]
        own.pop('map_anchor', None)
        records = [self.record(own['station'], .99), self.record(neighbor['station'], .99)]
        records[1].update(id='neighbor', box=dict(x=160,y=70,width=60,height=20))
        before = deepcopy(data)
        circle = dict(x=180,y=120,score=.9,radius=30,refined=True)
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'map.png'; Image.new('RGB',(1000,700),'white').save(image)
            for margin, expected in ((.02, 1), (.12, 0)):
                def detect(_, box, *args, **kwargs):
                    return [deepcopy(circle), dict(x=280,y=120,score=.9-margin if box['x']==160 else .7,
                                                   radius=30,refined=True)]
                with patch.object(auto.maps,'asset_path',return_value=image), patch.object(auto,'detect_circle_candidates',side_effect=detect):
                    result = auto.prelabel(data,records=records,sample=[(own['station'],own['voltage'])])
                self.assertEqual(result['report']['candidates'], expected)
                self.assertEqual(result['data']['nodes'][1], before['nodes'][1])
                self.assertEqual(result['data']['edges'], before['edges'])
        self.assertEqual(data, before)

    def record(self, text='长河岭', confidence=.7):
        return dict(id='r', text=text, confidence=confidence, category='node',
                    box=dict(x=30, y=40, width=80, height=20))

    def test_retry_preserves_raw_text_and_requires_two_treatments(self):
        image = Image.new('RGB', (300, 200), 'white')
        record = self.record('长何岭'); original = deepcopy(record)
        sizes = []
        def read(view):
            sizes.append(view.size)
            return [('长河岭', .98)]
        auto.retry_difficult(image, [record], ['长河岭'], read)
        self.assertEqual(record['text'], original['text'])
        self.assertEqual(record['box'], original['box'])
        self.assertTrue(record['retry_agreed'])
        self.assertFalse(record['ocr_conflict'])
        self.assertEqual(sizes, [(184, 64)]*2)

    def test_conflicting_exact_names_abstain_even_with_high_confidence(self):
        record = self.record()
        auto.retry_difficult(Image.new('RGB', (300, 200)), [record], ['长河岭', '长湖岭'], lambda _: [('长湖岭', .99)])
        self.assertTrue(record['ocr_conflict'])
        self.assertFalse(record['retry_agreed'])

    def test_one_retry_disagreement_and_short_names_do_not_force_correction(self):
        record = self.record('长何岭')
        answers = iter([[('长河岭', .98)], [('长何岭', .99)]])
        auto.retry_difficult(Image.new('RGB', (300, 200)), [record], ['长河岭'], lambda _: next(answers))
        self.assertFalse(record['retry_agreed'])
        calls = []
        short = self.record('河山')
        auto.retry_difficult(Image.new('RGB', (300, 200)), [short], ['何山'], lambda _: calls.append(1))
        self.assertEqual(calls, [])

    def test_secondary_boxes_require_two_exact_ppocr_reads_and_preserve_primary(self):
        image = Image.new('RGB', (300, 200))
        original = [self.record('其他', .99)]
        proposals = [self.record('李朗B', .99)]
        before = deepcopy(original)
        result = auto.recover_text_proposals(image, original, ['李朗'], proposals, lambda _: [('李朗B', .98)])
        self.assertEqual(original, before)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[-1]['text'], '李朗B')
        self.assertTrue(result[-1]['ocr_recovered'])
        self.assertEqual(result[-1]['proposal_engine'], 'macos-vision')
        self.assertEqual(result[-1]['box'], proposals[0]['box'])

    def test_secondary_box_disagreement_or_other_station_does_not_force_a_name(self):
        image = Image.new('RGB', (300, 200))
        proposal = self.record('李朗B', .99)
        for answers in ([ [('李朗B', .99)], [('李朗B', .7)] ],
                        [ [('李朗B', .99)], [('塘朗B', .99)] ],
                        [ [('塘朗B', .99)], [('塘朗B', .99)] ]):
            values = iter(answers)
            result = auto.recover_text_proposals(image, [], ['李朗','塘朗'], [proposal], lambda _: next(values))
            self.assertEqual(result, [])

    def test_native_proposals_skip_low_confidence_and_existing_names(self):
        calls = []
        result = auto.recover_text_proposals(Image.new('RGB',(300,200)), [self.record('李朗B', .99)],
                    ['李朗'], [self.record('李朗B', .99), self.record('李朗B', .3)], lambda _: calls.append(1))
        self.assertEqual(len(result), 1)
        self.assertEqual(calls, [])

    def test_native_supplement_failure_keeps_primary_ocr(self):
        records = [self.record()]
        with patch.object(auto, 'recognize', side_effect=ValueError('not installed')):
            with self.assertWarnsRegex(RuntimeWarning, '保留PP-OCRv6'):
                self.assertIs(auto.supplement_native_boxes(Image.new('RGB',(300,200)),records,['长河岭'],lambda _: []),records)

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_black_ring_inner_contour_survives_outgoing_lines(self):
        image = np.full((240, 320, 3),255,np.uint8)
        cv2.circle(image,(130,110),32,(255,255,0),-1)
        cv2.circle(image,(130,110),35,(0,0,0),8)
        cv2.line(image,(0,110),(95,110),(0,0,0),5)
        cv2.line(image,(165,110),(300,20),(0,0,0),5)
        found = dark_ring_candidates(image,cv2)
        self.assertTrue(any(math.hypot(x-130,y-110)<1 for x,y,_,_ in found))

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_black_ring_does_not_turn_open_lines_into_closed_symbols(self):
        image = np.full((240,320,3),255,np.uint8)
        cv2.line(image,(10,100),(300,100),(0,0,0),5)
        cv2.line(image,(130,10),(130,230),(0,0,0),5)
        self.assertEqual(dark_ring_candidates(image,cv2),[])

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_220_ranking_uses_ring_evidence_instead_of_colored_interior(self):
        image = np.full((350,450,3),255,np.uint8)
        cv2.circle(image,(150,120),34,(0,0,0),7)
        cv2.circle(image,(260,120),40,(0,0,255),8)
        candidates = detect_circle_candidates(image,dict(x=190,y=180,width=110,height=25),cv2,voltage=220)
        self.assertTrue(candidates)
        self.assertLess(math.hypot(candidates[0]['x']-150,candidates[0]['y']-120),2)
        self.assertGreater(candidates[0]['dark_ring_coverage'],.8)

    def test_native_paddle_input_limit_is_explicit(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        fake = Mock(return_value=SimpleNamespace(predict=lambda _: []))
        with patch.dict(sys.modules, {'paddleocr': SimpleNamespace(PaddleOCR=fake)}):
            self.assertEqual(auto.recognize(Image.new('RGB', (1500, 1000)), 'ppocrv6', enhanced=False), [])
        self.assertEqual(fake.call_args.kwargs['text_det_limit_side_len'], 1500)
        self.assertEqual(fake.call_args.kwargs['text_det_limit_type'], 'max')

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_marked_price_variant_protects_entire_physical_station(self):
        data = annotation.annotated()
        original = deepcopy(data)
        extra = deepcopy(data['nodes'][0]); extra['id'] = 'extra-price-variant'
        extra.pop('map_anchor', None); data['nodes'].append(extra)
        record = self.record('甲站', .99)
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'map.png'; Image.new('RGB', (1000, 700), 'white').save(image)
            with patch.object(auto.maps, 'asset_path', return_value=image):
                result = auto.prelabel(data, records=[record])
        self.assertEqual(result['report']['sampled'], 0)
        self.assertEqual(result['data']['nodes'][0], original['nodes'][0])
        self.assertNotIn('map_candidate', result['data']['nodes'][-1])

    def test_cached_primary_records_skip_full_image_detection(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        predict = Mock(side_effect=AssertionError('must not redetect the image'))
        fake = Mock(return_value=SimpleNamespace(predict=predict))
        record = self.record(confidence=.99)
        with patch.dict(sys.modules, {'paddleocr': SimpleNamespace(PaddleOCR=fake)}):
            result = auto.recognize(Image.new('RGB', (300, 200)), 'ppocrv6', enhanced=False, base_records=[record])
        predict.assert_not_called()
        self.assertEqual(result[0]['text'], record['text'])

    def test_external_worker_uses_same_code_and_reads_back_records(self):
        import sys
        record = self.record()
        def run(command, **kwargs):
            request = json.loads(Path(command[-2]).read_text())
            self.assertEqual(request['base_records'], [record])
            self.assertNotIn('TOPOLOGY_OCR_PYTHON', kwargs['env'])
            self.assertEqual(command[1:3], ['-m', 'toolbox_shared.section_prelabel'])
            Path(command[-1]).write_text(json.dumps([record]))
        with patch.dict('os.environ', {'TOPOLOGY_OCR_PYTHON': sys.executable}), patch.object(auto.subprocess, 'run', side_effect=run):
            self.assertEqual(auto.external_recognize(Image.new('RGB', (300, 200)), ['长河岭'], True, [record]), [record])

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_radial_fit_improves_shifted_circle_despite_crossing_lines(self):
        gray = np.full((240, 280), 255, np.uint8)
        cv2.circle(gray, (130, 110), 36, 0, 5, lineType=cv2.LINE_AA)
        cv2.line(gray, (20, 110), (250, 110), 0, 4)
        cv2.line(gray, (130, 10), (130, 210), 0, 3)
        x, y, _, refined, support = refine_circle(gray, 133, 108, 38, cv2)
        self.assertTrue(refined)
        self.assertGreaterEqual(support, .55)
        self.assertLess(math.hypot(x-130, y-110), 1)
        self.assertLess(math.hypot(x-130, y-110), math.hypot(3, 2))

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_crossing_lines_alone_are_not_a_circle(self):
        gray = np.full((240, 280), 255, np.uint8)
        cv2.line(gray, (20, 110), (250, 110), 0, 5)
        cv2.line(gray, (130, 10), (130, 210), 0, 5)
        self.assertFalse(refine_circle(gray, 130, 110, 36, cv2)[3])

    def test_numbered_images_include_candidates_without_modifying_source(self):
        image = Image.new('RGB', (1800, 1400), 'white'); original = image.tobytes()
        circles = [dict(x=200, y=80, radius=30), dict(x=500, y=180, radius=35)]
        local, context, options = review_images(image, self.record(), circles)
        self.assertEqual(image.tobytes(), original)
        self.assertNotEqual(local.tobytes(), Image.new('RGB', local.size, 'white').tobytes())
        self.assertEqual([o['index'] for o in options], [0, 1])
        self.assertGreater(context.width, local.width)
        self.assertLessEqual(max(context.size), 1400)
        self.assertTrue(all(0 <= o['x'] < local.width and 0 <= o['y'] < local.height for o in options))

    def test_local_visual_decisions_are_bound_to_image_recipe_and_candidate_geometry(self):
        circles = [dict(x=200, y=80, radius=30, score=.9)]
        record = self.record(); group = ('长河岭', 500)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'review.json'
            data = dict(image_sha256='image', recipe='v1', choices={
                review_key(group, record): dict(circle_signature=circle_signature(circles), index=0)})
            path.write_text(json.dumps(data))
            reviewer = decisions_reviewer(path, 'image', 'v1')
            self.assertIs(reviewer(None, group, record, circles), circles[0])
            self.assertIsNone(reviewer(None, ('其他', 500), record, circles))
            with self.assertRaisesRegex(ValueError, '已变化'):
                reviewer(None, group, record, [dict(x=201, y=80, radius=30, score=.9)])
            with self.assertRaisesRegex(ValueError, '不匹配'):
                decisions_reviewer(path, 'another-image', 'v1')
            data['choices'][review_key(group, record)]['index'] = True
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, '编号无效'):
                decisions_reviewer(path, 'image', 'v1')(None, group, record, circles)

    def test_both_providers_send_two_images_and_only_accept_candidate_index(self):
        env = dict(TOPOLOGY_VISION_MODEL='model', TOPOLOGY_VISION_BASE_URL='https://example.invalid', TOPOLOGY_VISION_API_KEY='test')
        circles = [dict(x=200, y=80, radius=30)]
        for provider in ('qwen', 'gemini'):
            for index in (0, None, True, 5):
                content = json.dumps(dict(index=index))
                response = dict(choices=[dict(message=dict(content=content))]) if provider == 'qwen' else dict(candidates=[dict(content=dict(parts=[dict(text=content)]))])
                def fetch(request, **_):
                    body = json.loads(request.data)
                    parts = body['messages'][0]['content'] if provider == 'qwen' else body['contents'][0]['parts']
                    self.assertEqual(len(parts), 3)
                    return BytesIO(json.dumps(response).encode())
                with self.subTest(provider=provider, index=index), patch.dict('os.environ', env), patch('toolbox_shared.section_vision_review.urlopen', side_effect=fetch):
                    review = configured_reviewer(provider)
                    args = (Image.new('RGB', (1000, 700)), ('长河岭', 500), self.record(), circles)
                    if index is True or index == 5:
                        with self.assertRaisesRegex(ValueError, '响应失败'): review(*args)
                    else:
                        self.assertIs(review(*args), circles[0] if index == 0 else None)

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_fixed_sample_keeps_missing_station_and_conflict_abstains(self):
        data = annotation.annotated()
        for n in data['nodes']: n.pop('map_anchor', None)
        available = list(dict.fromkeys((n['station'], n['voltage']) for n in data['nodes']))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'map.png'; Image.new('RGB', (1000, 700), 'white').save(image)
            with patch.object(auto.maps, 'asset_path', return_value=image):
                result = auto.prelabel(data, records=[], sample=available[:2])
                self.assertEqual([(r['station'], r['voltage']) for r in result['report']['rows']], available[:2])
                self.assertEqual(result['report']['matched'], 0)
                record = self.record('甲站', .99); record['ocr_conflict'] = True
                with patch.object(auto, 'detect_circle_candidates', return_value=[dict(x=180,y=120,score=.9,radius=30)]):
                    result = auto.prelabel(data, records=[record])
                self.assertEqual(result['report']['candidates'], 0)
                self.assertTrue(any('冲突' in r['reason'] for r in result['report']['rows']))

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_failed_retry_does_not_promote_low_confidence_exact_name(self):
        data = annotation.annotated()
        for n in data['nodes']: n.pop('map_anchor', None)
        record = self.record('甲站', .7)
        record.update(ocr_retried=True, retry_agreed=False)
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'map.png'; Image.new('RGB', (1000, 700), 'white').save(image)
            with patch.object(auto.maps, 'asset_path', return_value=image), patch.object(auto, 'detect_circle_candidates', return_value=[dict(x=180,y=120,score=.9,radius=30)]):
                self.assertEqual(auto.prelabel(data, records=[record])['report']['candidates'], 0)
                record['retry_agreed'] = True
                self.assertEqual(auto.prelabel(data, records=[record])['report']['candidates'], 1)

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_visual_reviewer_can_resolve_cross_voltage_without_assuming_nearest(self):
        data = annotation.annotated()
        for n in data['nodes']: n.pop('map_anchor', None)
        data['nodes'][1].update(station=data['nodes'][0]['station'], voltage=220)
        data['edges'] = []
        record = self.record('甲站', .99)
        circles = [dict(x=180,y=120,score=.9,radius=30), dict(x=220,y=140,score=.8,radius=45)]
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'map.png'; Image.new('RGB', (1000, 700), 'white').save(image)
            with patch.object(auto.maps, 'asset_path', return_value=image), patch.object(auto, 'detect_circle_candidates', return_value=circles):
                result = auto.prelabel(data, records=[record], reviewer=lambda image, group, *_: circles[1] if group[1] == 500 else None)
        self.assertEqual(result['report']['candidates'], 1)
        self.assertEqual(result['data']['nodes'][0]['map_candidate']['point'], dict(x=220,y=140))
        self.assertNotIn('map_candidate', result['data']['nodes'][1])

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_visual_disagreement_between_two_label_locations_stays_unresolved(self):
        data = annotation.annotated()
        for n in data['nodes']: n.pop('map_anchor', None)
        records = [self.record('甲站', .99), self.record('甲站', .99)]
        records[1]['id'] = 'other'; records[1]['box']['x'] = 500
        def circles(_, box, *args, **kwargs):
            return [dict(x=box['x']+50,y=120,score=.9,radius=30)]
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'map.png'; Image.new('RGB', (1000, 700), 'white').save(image)
            with patch.object(auto.maps, 'asset_path', return_value=image), patch.object(auto, 'detect_circle_candidates', side_effect=circles):
                result = auto.prelabel(data, records=records, reviewer=lambda image, group, record, options: options[0])
        self.assertEqual(result['report']['candidates'], 0)

    def test_facility_suffix_survives_catalog_station_normalization(self):
        self.assertEqual(auto.vocabulary_hits('海路', ['海路陆上集控']), {'海路陆上集控'})

    @unittest.skipUnless(cv2, 'optional OpenCV is not installed')
    def test_close_circle_scores_abstain_without_vision(self):
        data = annotation.annotated()
        for n in data['nodes']: n.pop('map_anchor', None)
        record = self.record('甲站', .99)
        circles = [dict(x=180,y=120,score=.9,radius=30,refined=True),
                   dict(x=280,y=120,score=.88,radius=30,refined=True)]
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'map.png'; Image.new('RGB', (1000, 700), 'white').save(image)
            with patch.object(auto.maps, 'asset_path', return_value=image), patch.object(auto, 'detect_circle_candidates', return_value=circles):
                result = auto.prelabel(data, records=[record])
                self.assertEqual(result['report']['candidates'], 0)
                result = auto.prelabel(data, records=[record], reviewer=lambda *_: circles[1])
                self.assertEqual(result['report']['candidates'], 1)
                row = next(r for r in result['report']['rows'] if r['point'])
                self.assertEqual(row['point']['x'], 280)
                self.assertAlmostEqual(row['circle_margin'], -.02)


if __name__ == '__main__':
    unittest.main()
