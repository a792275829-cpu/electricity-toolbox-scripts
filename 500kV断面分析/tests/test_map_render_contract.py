import hashlib
import json
from pathlib import Path
import unittest

from src.build_500kv_map_html import _render_html


class MapRenderContractTests(unittest.TestCase):
    def test_extracted_view_keeps_byte_identical_output_and_script_escaping(self):
        fixtures = json.loads(Path(__file__).with_name('map_render_contract.json').read_text())
        for fixture in fixtures:
            with self.subTest(data=fixture['data']):
                html = _render_html(fixture['data'])
                self.assertEqual(hashlib.sha256(html.encode()).hexdigest(), fixture['sha256'])
                self.assertNotIn('</script><标签>', html)

    def test_non_finite_data_is_rejected(self):
        with self.assertRaises(ValueError):
            _render_html({'bad': float('nan')})


if __name__ == '__main__':
    unittest.main()
