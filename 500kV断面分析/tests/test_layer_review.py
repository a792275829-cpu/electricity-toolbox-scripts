import json
import tempfile
import unittest
from pathlib import Path
from tools.layer_review_server import import_source, validate_node


class LayerReviewTests(unittest.TestCase):
    def test_import_keeps_candidates_unconfirmed_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / 'mapping2.json', root / 'review.json'
            source.write_text(json.dumps([{'disp': '同名站', 'base': '同名', 'tiles': ['r01c01'], 'strong': [{'names': ['站500_KV#1', '站220_KV#1']}], 'weak': []}], ensure_ascii=False))
            data = import_source(source, output)
            node = data['nodes'][0]
            self.assertEqual((node['voltage'], node['mapping_status'], node['anchor']), ('待核验', '待核验', None))
            self.assertEqual(len(node['strong'][0]['names']), 2)
            with self.assertRaises(FileExistsError):
                import_source(source, output)
            self.assertEqual(json.loads(output.read_text()), data)

    def test_validation_requires_voltage_and_mapping_for_confirmation(self):
        patch = {'name': '站', 'voltage': '待核验', 'mapping': '', 'mapping_status': '已确认', 'anchor': None, 'position_status': '待定位', 'note': ''}
        with self.assertRaises(ValueError): validate_node(patch)
        patch.update(voltage='220kV', mapping='原始220_KV#1')
        self.assertEqual(validate_node(patch), patch)
        patch.update(anchor={'x': float('nan'), 'y': 10}, position_status='已确认')
        with self.assertRaises(ValueError): validate_node(patch)
        patch.update(anchor=None)
        with self.assertRaises(ValueError): validate_node(patch)


if __name__ == '__main__': unittest.main()
