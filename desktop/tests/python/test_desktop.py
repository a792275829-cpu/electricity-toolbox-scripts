from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]
spec = importlib.util.spec_from_file_location(
    "prepare_workspace", ROOT / "packaging/tauri/prepare_workspace.py"
)
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class DesktopTests(unittest.TestCase):
    def test_release_requires_both_signed_targets(self):
        spec = importlib.util.spec_from_file_location(
            "release_index", ROOT / "packaging/tauri/release_index.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assets = [
            {
                "name": "tool.app.tar.gz",
                "browser_download_url": "https://example.com/mac",
            },
            {
                "name": "tool_x64-setup.exe",
                "browser_download_url": "https://example.com/win",
            },
        ]
        signatures = {a["name"] + ".sig": "signature" for a in assets}
        index = module.build_index("v0.1.0", assets, signatures)
        self.assertEqual(set(index["platforms"]), {"darwin-aarch64", "windows-x86_64"})
        with self.assertRaises(ValueError):
            module.build_index("v0.1.0", assets[:1], signatures)
        with self.assertRaises(ValueError):
            module.build_index("v0.1.0", assets, {})


class UpdateWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.payload = self.base / "payload"
        self.root = self.base / "user"
        (self.payload / "workspace").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def release(self, files):
        for name, content in files.items():
            path = self.payload / "workspace" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        hashes = {n: workspace.digest(self.payload / "workspace" / n) for n in files}
        (self.payload / "manifest.json").write_text(
            json.dumps({"files": hashes, "build": str(hashes), "version": "test"})
        )

    def test_update_preserves_data_and_removes_only_managed(self):
        self.release({"tool.py": "v1", "old.py": "old"})
        workspace.install(self.payload, self.root)
        (self.root / "config.json").write_text("private")
        (self.root / "output.xlsx").write_text("report")
        self.release({"tool.py": "v2"})
        workspace.install(self.payload, self.root)
        self.assertEqual((self.root / "tool.py").read_text(), "v2")
        self.assertFalse((self.root / "old.py").exists())
        self.assertEqual((self.root / "config.json").read_text(), "private")
        self.assertTrue((self.root / "output.xlsx").exists())

    def test_local_edit_blocks_whole_update(self):
        self.release({"tool.py": "v1", "second.py": "v1"})
        workspace.install(self.payload, self.root)
        (self.root / "tool.py").write_text("local edit")
        self.release({"tool.py": "v2", "second.py": "v2"})
        with self.assertRaises(RuntimeError):
            workspace.install(self.payload, self.root)
        self.assertEqual((self.root / "tool.py").read_text(), "local edit")
        self.assertEqual((self.root / "second.py").read_text(), "v1")

    def test_bad_hash_and_escape(self):
        self.release({"tool.py": "v1"})
        (self.payload / "workspace/tool.py").write_text("damaged")
        with self.assertRaises(ValueError):
            workspace.install(self.payload, self.root)
        for name in [
            "../escape",
            "/escape",
            "a/../../escape",
            "C:/escape",
            "a\\escape",
        ]:
            with self.assertRaises(ValueError):
                workspace.safe(self.root, name)


if __name__ == "__main__":
    unittest.main()
