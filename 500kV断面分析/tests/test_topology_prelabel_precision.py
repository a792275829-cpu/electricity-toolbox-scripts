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
from toolbox_shared.section_annotation_geometry import refine_circle, detect_circle_candidates
from toolbox_shared.section_vision_review import configured_reviewer, review_images, decisions_reviewer, review_key, circle_signature
import test_topology_annotation as annotation


class PrecisionTests(unittest.TestCase):
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
