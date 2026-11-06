from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from run_500kv_analysis import run_many


class BatchOutputTests(unittest.TestCase):
    def test_colliding_names_reject_entire_batch_before_writing(self):
        for names in (("实时节点.xlsx", "实时节点.xlsx"), ("Price.xlsx", "price.xlsx"),
                      ("café.xlsx", "cafe\u0301.xlsx")):
            with self.subTest(names=names), TemporaryDirectory() as directory:
                root = Path(directory)
                topology = root / "topology.md"
                topology.touch()
                sources = []
                for index, name in enumerate(names):
                    parent = root / str(index)
                    parent.mkdir()
                    source = parent / name
                    source.touch()
                    sources.append(source)
                output = root / "out"
                output.mkdir()
                previous = output / f"{sources[0].stem}_500kV分析.html"
                previous.write_text("previous report")
                with patch("run_500kv_analysis.generate_blockage_map") as generate:
                    with self.assertRaisesRegex(ValueError, "输出文件名冲突"):
                        run_many(sources, output, topology_path=topology)
                generate.assert_not_called()
                from toolbox.runtime import ToolPaths
                from toolbox.workflows import validate

                with self.assertRaisesRegex(ValueError, "输出文件名冲突"):
                    validate(ToolPaths(Path(__file__).resolve().parents[2]), "section-analysis", "run", {
                        "files": [str(source) for source in sources],
                        "topology_path": str(topology), "output_root": str(output),
                    })
                self.assertEqual(previous.read_text(), "previous report")
                self.assertEqual(len(list(output.iterdir())), 1)

    def test_later_invalid_input_does_not_generate_earlier_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            topology = root / "topology.md"
            topology.touch()
            source = root / "valid.xlsx"
            source.touch()
            with patch("run_500kv_analysis.generate_blockage_map") as generate:
                with self.assertRaises(FileNotFoundError):
                    run_many([source, root / "missing.xlsx"], root / "out", topology_path=topology)
            generate.assert_not_called()
            self.assertFalse((root / "out").exists())
