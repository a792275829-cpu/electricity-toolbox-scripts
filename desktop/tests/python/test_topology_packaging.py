from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "packaging/tauri")]
import prepare_payload
import prepare_workspace

TOPOLOGY = Path("500kV断面分析/500kV节点拓扑.md")


class TopologyPackagingTests(unittest.TestCase):
    def test_export_and_install_keep_topology_available_to_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            payload = base / "payload"
            source = payload / "app"
            source.mkdir(parents=True)
            prepare_payload.build_support.export_source(
                source, predicate=prepare_payload.runtime_source,
            )
            prepare_payload.validate_runtime_assets(source)
            files = {p.relative_to(source).as_posix(): prepare_workspace.digest(p)
                     for p in source.rglob("*") if p.is_file()}
            self.assertIn(TOPOLOGY.as_posix(), files)
            (payload / "manifest.json").write_text(json.dumps({
                "layout": 2, "version": "test", "build": "topology-test", "files": files,
            }))
            installed = base / "installed/app"
            prepare_workspace.install(
                payload, installed, data_root=base / "installed/data",
                state_root=base / "installed/state",
            )
            self.assertEqual((installed / TOPOLOGY).read_bytes(), (ROOT / TOPOLOGY).read_bytes())
            # Resolve the same default path as the GUI, using only installed code.
            result = subprocess.run([
                sys.executable, "-B", "-c",
                "import sys; from pathlib import Path; "
                "sys.path[:0] = ['.', '电力工具箱', '500kV断面分析']; "
                "from toolbox.runtime import ToolPaths; "
                "from src.analyze_500kv_section_gaps import parse_topology_edges; "
                "path = ToolPaths(Path.cwd()).section_analysis_topology; "
                "edges, duplicates = parse_topology_edges(path); "
                "assert edges; print(f'{len(edges)} edges, {len(duplicates)} duplicates')",
            ], cwd=installed, capture_output=True, text=True, check=True)
            self.assertIn("edges", result.stdout)

    def test_missing_or_empty_topology_blocks_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            for content in (None, "", " \n"):
                with self.subTest(content=content):
                    if content is not None:
                        path = source / TOPOLOGY
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(FileNotFoundError, "500kV节点拓扑"):
                        prepare_payload.validate_runtime_assets(source)


if __name__ == "__main__":
    unittest.main()
