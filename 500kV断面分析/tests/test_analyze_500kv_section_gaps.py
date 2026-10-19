import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.analyze_500kv_section_gaps import analyze_section_gaps, match_price_rows, _price_row_priority


class Analyze500kVSectionGapsTest(unittest.TestCase):
    def test_indexed_matching_preserves_duplicate_priority_and_fallback(self):
        rows = [
            {"raw_name": "其他甲站500kV", "normalized_name": "甲"},
            {"raw_name": "甲站500kV", "normalized_name": "甲"},
            {"raw_name": "包含乙的节点", "normalized_name": "别名"},
        ]
        expected = {}
        for node in ("甲", "乙", "缺失"):
            candidates = [row for row in rows if row["normalized_name"] == node]
            if not candidates:
                candidates = [row for row in rows if node in row["raw_name"]]
            if candidates:
                expected[node] = sorted(candidates, key=lambda row: _price_row_priority(row["raw_name"]))[0]
        self.assertEqual(match_price_rows(iter(("甲", "乙", "缺失")), rows), expected)

    def test_analyze_section_gaps_requires_four_accumulated_intervals_without_continuity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            topology_path = tmp_path / "topology.md"
            workbook_path = tmp_path / "prices.xlsx"
            output_dir = tmp_path / "out"

            topology_path.write_text(
                """# test

## 2. 跨片区主通道边

### A片 - B片
甲-乙、甲-丙、甲-丁

## 3. next
""",
                encoding="utf-8",
            )

            wb = Workbook()
            ws = wb.active
            ws.title = "实时节点电价查询"
            ws.append(["节点名称", "数据项", "00:00", "00:15", "00:30", "00:45", "01:00", "01:15", "01:30", "01:45"])
            ws.append(["其他甲站500kV", "电价(元/MWh)", 100, 110, 110, 120, 120, 130, 130, 140])
            ws.append(["其他乙站500kV", "电价(元/MWh)", 100, 90, 90, 80, 80, 70, 70, 60])
            ws.append(["其他丙站500kV", "电价(元/MWh)", 200, 210, 210, 220, 220, 230, 230, 230])
            ws.append(["其他丁站500kV", "电价(元/MWh)", 100, 90, 90, 80, 80, 70, 70, 70])
            wb.save(workbook_path)

            result = analyze_section_gaps(
                topology_path=topology_path,
                workbook_path=workbook_path,
                output_dir=output_dir,
                threshold=0,
            )

            self.assertEqual(result.flagged_edges, 1)
            self.assertEqual(result.flagged_intervals, 4)

            out_wb = load_workbook(result.output_path, data_only=True)
            summary_rows = list(out_wb["断面汇总"].iter_rows(values_only=True))
            detail_rows = list(out_wb["逐时段明细"].iter_rows(values_only=True))
            edge_index = summary_rows[0].index("edge")
            window_index = summary_rows[0].index("windows")
            windows = {row[edge_index]: row[window_index] for row in summary_rows[1:]}
            self.assertEqual(windows["甲-乙"], "00:15; 00:45; 01:15; 01:45")
            self.assertNotIn("甲-丁", windows)
            detail_edges = [row[detail_rows[0].index("edge")] for row in detail_rows[1:]]
            self.assertEqual(detail_edges.count("甲-乙"), 4)
            self.assertEqual(detail_edges.count("甲-丁"), 0)

    def test_analyze_section_gaps_flags_connected_price_gaps_and_missing_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            topology_path = tmp_path / "topology.md"
            workbook_path = tmp_path / "prices.xlsx"
            output_dir = tmp_path / "out"

            topology_path.write_text(
                """# test

## 2. 跨片区主通道边

### A片 - B片
甲-乙、乙-丙

### 重复片
乙-甲

### 缺失片
甲-丁

## 3. next
""",
                encoding="utf-8",
            )

            wb = Workbook()
            ws = wb.active
            ws.title = "实时节点电价查询"
            ws.append(["节点名称", "数据项", "00:00", "00:15", "00:30", "00:45", "01:00", "01:15"])
            ws.append(["其他甲站500kV", "电价(元/MWh)", 100, 110, 120, 130, 140, 150])
            ws.append(["其他乙站500kV", "电价(元/MWh)", 100, 90, 80, 70, 60, 50])
            ws.append(["其他丙站500kV", "电价(元/MWh)", 100, 110, 120, 130, 140, 150])
            wb.save(workbook_path)

            result = analyze_section_gaps(
                topology_path=topology_path,
                workbook_path=workbook_path,
                output_dir=output_dir,
                threshold=0,
            )

            self.assertEqual(result.unique_edges, 3)
            self.assertEqual(result.skipped_duplicate_edges, [("乙", "甲", "重复片")])
            self.assertEqual(result.missing_nodes, ["丁"])
            self.assertEqual(result.flagged_edges, 2)
            self.assertEqual(result.flagged_intervals, 10)

            self.assertEqual(result.output_path, output_dir / "500kv_section_price_gap_analysis.xlsx")
            self.assertTrue(result.output_path.exists())
            self.assertFalse((output_dir / "500kv_section_price_gap_summary.csv").exists())

            out_wb = load_workbook(result.output_path, data_only=True)
            self.assertEqual(out_wb.sheetnames, ["断面汇总", "逐时段明细", "片区汇总"])

            rows = list(out_wb["断面汇总"].iter_rows(values_only=True))
            header = rows[0]
            edge_index = header.index("edge")
            direction_index = header.index("main_direction")
            windows_index = header.index("windows")
            data_rows = rows[1:]

            self.assertEqual([row[edge_index] for row in data_rows], ["乙-丙", "甲-乙"])
            self.assertEqual(data_rows[0][direction_index], "乙->丙")
            self.assertEqual(data_rows[0][windows_index], "00:15-01:15")
            self.assertEqual(data_rows[1][direction_index], "乙->甲")
            self.assertEqual(data_rows[1][windows_index], "00:15-01:15")


if __name__ == "__main__":
    unittest.main()
