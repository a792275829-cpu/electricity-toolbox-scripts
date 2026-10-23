import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.classify_500kv_nodes import REGIONS, classify, node_key


class Sheet:
    def __init__(self, title, rows):
        self.title, self.rows = title, rows

    def iter_rows(self, min_row=1, max_row=None, **kwargs):
        return iter(self.rows[min_row - 1:max_row])


class Book(dict):
    def close(self):
        pass


class Classify500kvNodesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workbook = self.root / "city.xlsx"
        self.workbook.write_bytes(b"unchanged source")
        self.topology = self.root / "topology.md"
        self.topology.write_text("## 1. 节点分区\n### 旧片区\n甲站、乙变、阳西A厂\n"
                                 "## 2. 已确认的接线拓扑\n### 不代表行政地市\n甲站-乙变\n"
                                 "## 3. 程序约定\n", encoding="utf-8")
        self.rows = [["地市", "去重节点数量", "节点名称（顿号分隔）"]]
        self.rows.extend([[city + "市", 1 if city in ("广州", "惠州") else 0,
                           "甲站" if city == "广州" else "乙站" if city == "惠州" else ""]
                          for _, cities in REGIONS for city in cities])
        self.supplement = [["识别状态", "匹配地市", "补充记录统一节点名", "原始节点名称（逐条）"]]

    def run_classify(self, confirmed_cities=None):
        book = Book({"各市节点名单": Sheet("各市节点名单", self.rows),
                     "补充节点待核验": Sheet("补充节点待核验", self.supplement)})
        with patch("src.classify_500kv_nodes.load_workbook", return_value=book):
            return classify(self.workbook, self.topology, confirmed_cities=confirmed_cities or {})

    def test_human_confirmation_resolves_pending_city_with_its_own_evidence(self):
        self.supplement.append(["地市待核验", "江门市", "阳西A厂", "阳西A厂500kV#1M"])
        result = self.run_classify({"阳西A厂": "阳江"})
        node = next(n for n in result["nodes"] if n["name"] == "阳西A厂")
        self.assertEqual(node["city"], "阳江")
        self.assertEqual(node["evidence"][0]["sheet"], "用户确认")
        self.assertEqual(node["status"], "classified")

    def test_city_order_preserves_all_21_cities_and_only_topology_nodes(self):
        result = self.run_classify()
        self.assertEqual([len(r["cities"]) for r in result["regions"]], [9, 4, 3, 5])
        self.assertEqual([r["name"] for r in result["regions"]], [r[0] for r in REGIONS])
        self.assertEqual(result["summary"]["topology_nodes"], 3)
        self.assertEqual(result["summary"]["classified_nodes"], 2)
        by_name = {r["name"]: r for r in result["nodes"]}
        self.assertEqual(by_name["乙变"]["city"], "惠州")
        self.assertFalse(by_name["阳西A厂"]["connected"])
        self.assertEqual(self.workbook.read_bytes(), b"unchanged source")

    def test_pending_city_is_never_promoted_to_confirmed(self):
        self.supplement.append(["地市待核验", "阳江市", "阳西A厂", "阳西A厂500kV#1M"])
        result = self.run_classify()
        node = result["unclassified"][0]
        self.assertIsNone(node["city"])
        self.assertEqual(node["evidence"][0]["city"], "阳江")

    def test_bus_alias_requires_verified_supplement_and_main_agreement(self):
        self.supplement.append(["已匹配地市主表", "惠州市", "乙站", "别名电厂500kV#1M"])
        self.topology.write_text("## 2. 拓扑\n### 组\n甲站-别名电厂\n## 3. 约定\n")
        result = self.run_classify()
        self.assertEqual(result["summary"]["classified_nodes"], 2)
        self.supplement[-1][1] = "东莞市"
        self.assertEqual(self.run_classify()["summary"]["unclassified_nodes"], 1)

    def test_ambiguous_normalized_station_names_stay_unclassified(self):
        self.rows[1][2] = "甲站、乙变"
        result = self.run_classify()
        node = next(n for n in result["unclassified"] if n["name"] == "乙变")
        self.assertIn("多个地市", node["reason"])
        self.assertTrue(result["warnings"])

    def test_only_suffix_variants_collapse(self):
        self.assertEqual(node_key("其他博罗变500kV#1M"), node_key("博罗站"))
        self.assertEqual(node_key("靖海电厂"), node_key("靖海厂"))
        self.assertEqual(node_key("阳江核电厂"), node_key("阳江核电"))
        self.assertNotEqual(node_key("阳西A厂"), node_key("阳西厂"))
        self.assertNotEqual(node_key("铜鼓B厂"), node_key("铜鼓C厂"))
        self.assertNotEqual(node_key("云霄换流站"), node_key("云霄换流站福建"))


if __name__ == "__main__":
    unittest.main()
