import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook

from src.generate_500kv_blockage_map import generate_blockage_map


class Generate500kVBlockageMapTest(unittest.TestCase):
    def test_generate_blockage_map_runs_full_pipeline_from_raw_prices(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_prices = tmp_path / "实时节点电价查询.xlsx"
            topology = tmp_path / "topology.md"
            output_dir = tmp_path / "out"

            topology.write_text(
                """# test

## 1. 按 PDF 重划片区

### 佛山片
凤城

### 东莞片
水乡

## 2. 跨片区主通道边

### 佛山片 - 东莞片
凤城-水乡

## 3. next
""",
                encoding="utf-8",
            )

            wb = Workbook()
            ws = wb.active
            ws.title = "实时节点电价查询"
            ws.append(["节点名称", "数据项", "00:00", "00:15", "00:30", "00:45", "01:00", "01:15", "01:30", "01:45", "02:00"])
            ws.append(["其他凤城站500kV", "电价(元/MWh)", 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300])
            ws.append(["其他水乡站500kV", "电价(元/MWh)", 500, -100, -200, -300, -400, -500, -600, -700, -800])
            ws.append(["其他一总降站220kV", "电价(元/MWh)", 1, 1, 1, 1, 1, 1, 1, 1, 1])
            wb.save(raw_prices)

            result = generate_blockage_map(
                topology_path=topology,
                raw_price_path=raw_prices,
                output_dir=output_dir,
                threshold=500,
            )

            self.assertTrue(result.html_path.exists())
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                ["实时节点电价查询_500kV分析.html"],
            )

            html = result.html_path.read_text(encoding="utf-8")
            self.assertIn("广东省电网 500kV 节点断面地图", html)
            self.assertIn('"edge": "凤城-水乡"', html)
            self.assertIn('"is_section": true', html)

    def test_generate_blockage_map_removes_legacy_excel_outputs(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_prices = tmp_path / "实时节点电价查询.xlsx"
            topology = tmp_path / "topology.md"
            output_dir = tmp_path / "out"
            topology.write_text(
                """# test

## 2. 跨片区主通道边

### 佛山片 - 东莞片
凤城-水乡

## 3. next
""",
                encoding="utf-8",
            )

            wb = Workbook()
            ws = wb.active
            ws.title = "实时节点电价查询"
            ws.append(["节点名称", "数据项", "00:00"])
            ws.append(["其他凤城站500kV", "电价(元/MWh)", 100])
            ws.append(["其他水乡站500kV", "电价(元/MWh)", 700])
            wb.save(raw_prices)

            output_dir.mkdir()
            (output_dir / "500kv_section_price_gap_analysis.xlsx").write_bytes(b"legacy")
            (output_dir / "实时节点电价查询_500kV去重.xlsx").write_bytes(b"legacy")
            (output_dir / "guangdong_500kv_section_map.html").write_text("legacy", encoding="utf-8")

            result = generate_blockage_map(
                topology_path=topology,
                raw_price_path=raw_prices,
                output_dir=output_dir,
                threshold=500,
            )

            self.assertTrue(result.html_path.exists())
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                ["实时节点电价查询_500kV分析.html"],
            )


if __name__ == "__main__":
    unittest.main()
