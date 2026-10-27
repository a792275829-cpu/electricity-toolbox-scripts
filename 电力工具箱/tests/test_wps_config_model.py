from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = ROOT / "wps自动"
for entry in (str(ROOT), str(TOOL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import config_model
from toolbox_shared.wps_service import DocumentConfig, MappingRow, RegionRow
from toolbox_shared.wps_values import range_shape


class DocumentConfigTests(unittest.TestCase):
    def test_regions_and_name_fallback(self):
        config = config_model.document_config_from_config({
            "local_file": "D:/数据/归湖.xlsx",
            "kdocs_url": "https://example.test/doc",
            "regions": [{
                "source_sheet": "数据", "source_start": "A1", "source_end": "C3",
                "target_sheet": "总表", "target_start": "B2", "target_end": "D4",
            }],
        })
        self.assertIsInstance(config, DocumentConfig)
        self.assertEqual(config.name, "归湖")
        self.assertEqual(len(config.regions), 1)
        self.assertIsInstance(config.regions[0], RegionRow)
        self.assertEqual(config.regions[0].target_start, "B2")
        self.assertEqual(config.source_type, "excel")

    def test_missing_name_and_garbage_regions(self):
        config = config_model.document_config_from_config({"regions": [None, "x", {}]})
        self.assertEqual(config.name, "未命名配置")
        self.assertEqual(len(config.regions), 1)
        self.assertEqual(config.regions[0].source_sheet, "")


class MappingConfigTests(unittest.TestCase):
    def test_legacy_source_range_is_split(self):
        mapping = config_model.mapping_from_config({
            "source_range": "A1:C3", "target_start": "B2",
        })
        self.assertEqual((mapping.source_start, mapping.source_end), ("A1", "C3"))
        self.assertEqual(mapping.target_start, "B2")

    def test_target_end_is_derived_from_source_shape(self):
        mapping = config_model.mapping_from_config({
            "source_range": "A1:C3", "target_start": "B2",
        })
        self.assertNotEqual(mapping.target_end, "")
        self.assertEqual(
            range_shape(mapping.target_start, mapping.target_end),
            range_shape(mapping.source_start, mapping.source_end),
        )

    def test_defaults_apply_to_url_and_file(self):
        mapping = config_model.mapping_from_config({}, "https://default", "D:/默认.xlsx")
        self.assertIsInstance(mapping, MappingRow)
        self.assertEqual(mapping.kdocs_url, "https://default")
        self.assertEqual(mapping.local_file, "D:/默认.xlsx")


class GroupingTests(unittest.TestCase):
    def mapping(self, url: str, file: str, sheet: str) -> MappingRow:
        return MappingRow(kdocs_url=url, local_file=file, source_sheet=sheet,
                          source_start="A1", source_end="B2", target_sheet="T",
                          target_start="A1", target_end="B2", source_type="excel",
                          source_url="")

    def test_groups_by_document_and_names_from_file_stem(self):
        configs = config_model.group_mappings_into_configs([
            self.mapping("u1", "D:/归湖.xlsx", "S1"),
            self.mapping("u1", "D:/归湖.xlsx", "S2"),
            self.mapping("u2", "", "S3"),
        ])
        self.assertEqual([config.name for config in configs], ["归湖", "配置 2"])
        self.assertEqual([len(config.regions) for config in configs], [2, 1])
        self.assertEqual(configs[0].kdocs_url, "u1")

    def test_flatten_round_trip(self):
        configs = [DocumentConfig(name="n", kdocs_url="u", local_file="f",
                                  source_type="excel", source_url="s",
                                  regions=[RegionRow("S1", "A1", "B2", "T1", "A1", "B2")])]
        mappings = config_model.flatten_configs(configs)
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0].kdocs_url, "u")
        self.assertEqual(mappings[0].source_sheet, "S1")
        self.assertEqual(mappings[0].source_type, "excel")


if __name__ == "__main__":
    unittest.main()
