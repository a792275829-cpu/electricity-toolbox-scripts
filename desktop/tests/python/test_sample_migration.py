from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("sample_migration", ROOT / "packaging/tauri/migrate_samples.py")
samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samples)


class SampleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.source, self.destination = base / "source", base / "native"
        self.source.mkdir()
        self.manifest = self.source / "plan.json"
        self.plan = {"version": 1, "tools": []}
        for item in samples.default_catalog():
            tool = {"id": item.tool_id, "name": item.name, "instructions": "选择本目录内的副本。", "files": []}
            if item.tool_id not in samples.ONLINE_ONLY:
                path = self.source / f"{item.tool_id}.xlsx"
                path.write_bytes(item.tool_id.encode())
                tool["files"].append({"source": path.name, "path": path.name, "role": "输入样例", "sha256": samples.digest(path)})
            self.plan["tools"].append(tool)
        self.save_plan()

    def save_plan(self):
        self.manifest.write_text(json.dumps(self.plan, ensure_ascii=False))

    def test_complete_idempotent_pack_keeps_relative_paths_and_excludes_unlisted_files(self):
        result = samples.migrate(self.source, self.destination, self.manifest)
        self.assertEqual((result["modules"], result["local_file_modules"], result["sample_files"]), (15, 13, 13))
        self.assertEqual(samples.migrate(self.source, self.destination, self.manifest)["copied_files"], 0)
        folder = Path(result["samples"])
        (folder / "auth_state.json").write_text('private')
        archive = self.destination / "samples.zip"
        samples.export_pack(folder, archive)
        with zipfile.ZipFile(archive) as pack:
            self.assertNotIn("测试样例/auth_state.json", pack.namelist())
            index = json.loads(pack.read("测试样例/样例清单.json"))
            self.assertTrue(all("source" not in f for t in index["tools"] for f in t["files"]))
            self.assertTrue(all(not n.startswith("/") and ".." not in n for n in pack.namelist()))

    def test_missing_module_or_missing_local_input_blocks_migration(self):
        self.plan["tools"].pop()
        self.save_plan()
        with self.assertRaises(ValueError):
            samples.migrate(self.source, self.destination, self.manifest)
        self.assertFalse(self.destination.exists())

    def test_escape_secret_and_changed_source_are_rejected(self):
        tool = next(t for t in self.plan["tools"] if t["files"])
        item = tool["files"][0]
        previous = dict(item)
        for change in ({"path": "../escape.xlsx"}, {"source": "/outside.xlsx"}, {"sha256": "wrong"}):
            with self.subTest(change=change):
                item.update(change)
                self.save_plan()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    samples.migrate(self.source, self.destination, self.manifest)
                item.clear()
                item.update(previous)
        private = self.source / "auth_state.json"
        private.write_text("private")
        item.update(source=private.name, sha256=samples.digest(private))
        self.save_plan()
        with self.assertRaises(ValueError):
            samples.migrate(self.source, self.destination, self.manifest)

    def test_edited_destination_is_preserved_and_cannot_be_silently_exported(self):
        result = samples.migrate(self.source, self.destination, self.manifest)
        folder = Path(result["samples"])
        changed = next(folder.rglob("*.xlsx"))
        changed.write_bytes(b"user-edited")
        with self.assertRaises(FileExistsError):
            samples.migrate(self.source, self.destination, self.manifest)
        self.assertEqual(changed.read_bytes(), b"user-edited")
        with self.assertRaises(ValueError):
            samples.export_pack(folder, self.destination / "output.zip")

    def test_copy_failure_removes_only_newly_created_samples(self):
        copier = samples.shutil.copyfile
        count = 0

        def fail_second(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk full")
            return copier(source, target)

        with mock.patch.object(samples.shutil, "copyfile", side_effect=fail_second):
            with self.assertRaises(OSError):
                samples.migrate(self.source, self.destination, self.manifest)
        self.assertFalse(list(self.destination.rglob("*.xlsx")))
        self.assertEqual(len(list(self.source.glob("*.xlsx"))), 13)


if __name__ == "__main__":
    unittest.main()
