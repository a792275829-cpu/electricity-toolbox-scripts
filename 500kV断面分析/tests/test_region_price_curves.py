import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.classify_500kv_nodes import topology_inventory
from src.deduplicate_500kv_prices import load_topology_nodes
from src.node_city_catalog import CITY_NODES, NODE_CITY, USER_CONFIRMED_CITIES, price_hierarchy

ROOT = Path(__file__).resolve().parents[1]


class RegionPriceCurvesTests(unittest.TestCase):
    def test_reviewed_catalog_covers_every_topology_node_once(self):
        nodes, _ = topology_inventory(ROOT / "500kV节点拓扑.md")
        self.assertEqual(nodes, set(NODE_CITY))
        self.assertEqual(len(nodes), sum(len(n.split("、")) for n in CITY_NODES.values()))
        self.assertEqual(len(nodes), 120)
        self.assertEqual(USER_CONFIRMED_CITIES, {
            "徐闻开关站": "湛江", "玄陆升压站": "汕尾", "铜鼓电厂": "江门", "阳西A厂": "阳江",
        })
        for name, city in USER_CONFIRMED_CITIES.items():
            self.assertEqual(NODE_CITY[name], city)

    def test_custom_topology_keeps_unknown_nodes_separate(self):
        tree = price_hierarchy(["铜鼓电厂", "新节点站"])
        self.assertEqual([len(r["cities"]) for r in tree[:4]], [9, 4, 3, 5])
        self.assertEqual(tree[-1], {"name": "未分类", "cities": [{"name": "待确认", "nodes": ["新节点站"]}]})

    def test_price_whitelist_includes_declared_node_without_adding_edges(self):
        with tempfile.TemporaryDirectory() as temp:
            topology = Path(temp) / "topology.md"
            topology.write_text("## 1. 节点分区\n### 组\n甲站、乙站、丙站\n"
                                "## 2. 接线拓扑\n### 组\n甲站-乙站\n## 3. 约定\n")
            self.assertEqual(load_topology_nodes(topology), ["丙站", "乙站", "甲站"])
            _, connected = topology_inventory(topology)
            self.assertNotIn("丙站", connected)

    @unittest.skipUnless(shutil.which("node"), "Node only needed to test embedded JS")
    def test_js_equal_node_weighting_missing_values_and_disconnected_segments(self):
        script = r"""
const assert = require('node:assert/strict');
require(process.argv[1]);
const {aggregateSeries, linePath} = globalThis.RegionPriceCurves;
const nodes = [
 {name:'A',city:'甲',region:'R',prices:[0,null,10,null]},
 {name:'B',city:'甲',region:'R',prices:[10,20,null,null]},
 {name:'C',city:'乙',region:'R',prices:[100,Infinity,NaN,null]},
];
let out = aggregateSeries(nodes,new Set(['A','B']),'city',4);
assert.deepEqual(out[0].prices,[5,20,10,null]);
assert.deepEqual(out[0].counts,[2,1,1,0]);
assert.equal(out[0].total,2);
out=aggregateSeries(nodes,new Set(['A','B','C']),'region',4);
assert.equal(out[0].prices[0],110/3); // equal nodes, not equal cities
assert.deepEqual(out[0].counts,[3,1,1,0]);
assert.deepEqual(aggregateSeries(nodes,new Set(),'nodes',4),[]);
assert.equal(linePath([1,null,3],x=>x,y=>y),'M0.00,1.00 M2.00,3.00');
assert.equal(linePath([0,-2],x=>x,y=>y),'M0.00,0.00 L1.00,-2.00');
console.log('aggregation and gaps verified');
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "src/region_curves.js")],
                                text=True, capture_output=True, check=True)
        self.assertIn("verified", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node only needed to test embedded JS")
    def test_js_price_sort_and_daily_rankings_preserve_zero_and_missingness(self):
        script = r"""
const assert = require('node:assert/strict');
require(process.argv[1]);
const {meanPrice, sortReadings, rankNodes} = globalThis.RegionPriceCurves;
const items = [
 {name:'A',prices:[0,null,10]},
 {name:'B',prices:[-2,-4,null]},
 {name:'C',prices:[null,null,null]},
 {name:'D',prices:[6,6,6]},
];
assert.equal(meanPrice([0,null,10,Infinity]),5);
assert.equal(meanPrice([null,NaN]),null);
assert.deepEqual(sortReadings(items,0,'time-desc').map(n=>n.name),['D','A','B','C']);
assert.deepEqual(sortReadings(items,0,'time-asc').map(n=>n.name),['B','A','D','C']);
assert.deepEqual(sortReadings(items,1,'mean-asc').map(n=>n.name),['B','A','D','C']);
assert.deepEqual(items.map(n=>n.name),['A','B','C','D']); // sorting cannot mutate legend order
const high = rankNodes(items,new Set(['A','B','C']));
assert.deepEqual(high.map(n=>n.name),['A','B']);
assert.deepEqual(high[0],{name:'A',mean:5,count:2,total:3});
assert.equal(rankNodes(items,new Set(['A','B']),false,1)[0].mean,-3);
assert.deepEqual(rankNodes(items,new Set(['C'])),[]);
console.log('rankings verified');
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "src/region_curves.js")],
                                text=True, capture_output=True, check=True)
        self.assertIn("rankings verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
