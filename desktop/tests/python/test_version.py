"""Version changes must carry release notes and retain previous history."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "release_version", Path(__file__).resolve().parents[3] / "packaging/tauri/version.py"
)
version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version)


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.override = patch.object(version, "ROOT", self.root)
        self.override.start()
        self.addCleanup(self.override.stop)
        for name in ("desktop/package.json", "desktop/src-tauri/tauri.conf.json", "desktop/package-lock.json"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"version": "0.1.3"}
            if "lock" in name:
                data["packages"] = {"": {"version": "0.1.3"}}
            path.write_text(json.dumps(data))
        (self.root / "desktop/src-tauri/Cargo.toml").write_text('version = "0.1.3"\n')
        (self.root / "desktop/src-tauri/Cargo.lock").write_text('name = "electricity-toolbox"\nversion = "0.1.3"\n')
        self.history = "# 更新日志\n\n## [0.1.3] - 2026-10-13\n\n- 保留原版本说明。\n"
        (self.root / "CHANGELOG.md").write_text(self.history)

    def test_set_syncs_versions_and_preserves_history(self):
        version.set_version("0.1.4", ["修复 Mac 安装与更新。", "验证安装包摘要。"], "2026-10-19")
        self.assertEqual(version.check("v0.1.4"), "v0.1.4")
        text = (self.root / "CHANGELOG.md").read_text()
        self.assertIn(self.history.split("\n\n", 1)[1], text)
        notes = version.release_changes("0.1.4")
        self.assertIn("2026-10-19", notes)
        self.assertIn("验证安装包摘要", notes)
        self.assertNotIn("保留原版本说明", notes)
        self.assertIn('version = "0.1.4"', (self.root / "desktop/src-tauri/Cargo.lock").read_text())

    def test_missing_notes_or_invalid_date_do_not_change_versions(self):
        for notes, date in [([], None), ([" "], None), (["变更\n## 注入标题"], None), (["具体变更"], "2026-02-30")]:
            with self.subTest(notes=notes, date=date), self.assertRaises(ValueError):
                version.set_version("0.1.4", notes, date)
            self.assertEqual(version.check(), "v0.1.3")
            self.assertEqual((self.root / "CHANGELOG.md").read_text(), self.history)

    def test_absent_empty_and_duplicate_entries_block_release(self):
        for content in [self.history, "# 更新日志\n\n## [0.1.4] - 2026-10-19\n", "## [0.1.4] - 2026-10-19\n\n- 内容一\n\n## [0.1.4] - 2026-10-19\n\n- 内容二\n"]:
            (self.root / "CHANGELOG.md").write_text(content)
            with self.assertRaises(ValueError):
                version.release_changes("0.1.4")

    def test_existing_version_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            version.set_version("0.1.3", ["不应覆盖"], "2026-10-19")
        self.assertEqual((self.root / "CHANGELOG.md").read_text(), self.history)


if __name__ == "__main__":
    unittest.main()
