from __future__ import annotations
import hashlib
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
        targets = tuple(module.PLATFORMS)
        index = module.build_index("v0.1.0", assets, signatures, targets=targets)
        self.assertEqual(set(index["platforms"]), {"darwin-aarch64", "windows-x86_64"})
        with self.assertRaises(ValueError):
            module.build_index("v0.1.0", assets[:1], signatures, targets=targets)
        with self.assertRaises(ValueError):
            module.build_index("v0.1.0", assets, {}, targets=targets)


    def test_draft_asset_urls_use_the_final_release_tag(self):
        spec = importlib.util.spec_from_file_location(
            "release_index", ROOT / "packaging/tauri/release_index.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        prefix = "https://github.com/owner/repo/releases/download/"
        names = ["工具_aarch64.app.tar.gz", "tool_x64-setup.exe"]
        assets = [{"name": name, "browser_download_url": prefix + "untagged-temporary/" + name}
                  for name in names]
        index = module.build_index("v0.1.2", assets, {name + ".sig": "signed" for name in names}, targets=tuple(module.PLATFORMS))
        from urllib.parse import unquote
        self.assertEqual({unquote(v["url"]) for v in index["platforms"].values()},
                         {prefix + "v0.1.2/" + name for name in names})

    def test_windows_release_plan_and_index_do_not_require_mac_build(self):
        spec = importlib.util.spec_from_file_location("release_windows_test", ROOT / "packaging/tauri/release_index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        targets, matrix = module.release_plan()
        self.assertEqual(("windows-x86_64",), targets)
        self.assertEqual(["windows-2022"], [row["runner"] for row in matrix["include"]])
        assets = [{"name": "tool_x64-setup.exe", "browser_download_url": "https://example.com/tool_x64-setup.exe"}]
        index = module.build_index("v0.1.4", assets, {"tool_x64-setup.exe.sig": "signed"})
        self.assertEqual({"windows-x86_64"}, set(index["platforms"]))
        with self.assertRaises(ValueError):
            module.build_index("v0.1.4", assets, {})
        with self.assertRaises(ValueError):
            module.build_index("v0.1.4", [], {})

    def test_other_platforms_require_explicit_selection_and_valid_targets(self):
        spec = importlib.util.spec_from_file_location("release_selection_test", ROOT / "packaging/tauri/release_index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        targets, matrix = module.release_plan("macos")
        self.assertEqual(("darwin-aarch64",), targets)
        self.assertEqual("macos-14", matrix["include"][0]["runner"])
        self.assertEqual(2, len(module.release_plan("both")[1]["include"]))
        for selection in ("linux", "", None):
            with self.assertRaises(ValueError):
                module.release_plan(selection)
        for targets in ((), ("linux",), ("windows-x86_64", "windows-x86_64")):
            with self.assertRaises(ValueError):
                module.build_index("v0.1.4", [], {}, targets=targets)
        notes = "| Windows 10 | exe |\n| macOS | dmg |\n当前 Mac 安装包不支持 Intel 芯片。\n"
        self.assertEqual("| Windows 10 | exe |\n", module.selected_notes(notes, ("windows-x86_64",)))


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
            json.dumps({"files": hashes, "build": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), "version": "test"})
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
