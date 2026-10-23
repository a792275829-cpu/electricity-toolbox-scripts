from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from toolbox_workbook_commit import commit_workbooks, workbook_commit_lock, workbook_fingerprint


class WorkbookCommitTests(unittest.TestCase):
    def prepare(self, root, name):
        original, temporary = root / f"{name}.xlsx", root / f".{name}.tmp.xlsx"
        original.write_bytes(b"original")
        temporary.write_bytes(b"prepared")
        return SimpleNamespace(original=original, temporary=temporary,
            source_fingerprint=workbook_fingerprint(original))

    def test_changed_later_file_prevents_entire_batch_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items = (self.prepare(root, "a"), self.prepare(root, "b"))
            before = items[1].original.stat()
            items[1].original.write_bytes(b"external")  # Same byte count, restored mtime.
            os.utime(items[1].original, ns=(before.st_atime_ns, before.st_mtime_ns))
            with self.assertRaisesRegex(RuntimeError, "工作簿被修改"):
                commit_workbooks(items, lambda *_: self.fail("must not commit"), create_backup=False)
            self.assertEqual(items[0].original.read_bytes(), b"original")
            self.assertEqual(items[1].original.read_bytes(), b"external")
            self.assertEqual(set(root.iterdir()), {i.original for i in items})

    def test_edit_between_replacements_rolls_back_only_our_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items = (self.prepare(root, "a"), self.prepare(root, "b"))
            def verify(path, item):
                if item is items[0]:
                    items[1].original.write_bytes(b"external")
            with self.assertRaisesRegex(RuntimeError, "工作簿被修改"):
                commit_workbooks(items, verify, create_backup=False)
            self.assertEqual(items[0].original.read_bytes(), b"original")
            self.assertEqual(items[1].original.read_bytes(), b"external")
            self.assertEqual(set(root.iterdir()), {i.original for i in items})
            # Rollback did not leave a lock or prevent a later transaction.
            with workbook_commit_lock([i.original for i in items]):
                pass

    def test_cross_process_commit_lock_is_released_without_sidecar_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = self.prepare(root, "a")
            script = (
                "from pathlib import Path; from toolbox_workbook_commit import workbook_commit_lock; "
                "import sys; "
                "lock=workbook_commit_lock([Path(sys.argv[1])]); lock.__enter__(); lock.__exit__(None,None,None)"
            )
            command = [sys.executable, "-c", script, str(item.original)]
            workspace = Path(__file__).resolve().parents[2]
            with workbook_commit_lock((item.original,)):
                result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("正在提交", result.stderr)
            result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(root.iterdir()), {item.original, item.temporary})

    def test_rollback_preserves_external_save_and_original_recovery_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items = (self.prepare(root, "a"), self.prepare(root, "b"))
            def verify(path, item):
                if item is items[0]:
                    path.write_bytes(b"external save A")
                    items[1].original.write_bytes(b"external save B")
            with self.assertRaisesRegex(RuntimeError, "工作簿被修改") as raised:
                commit_workbooks(items, verify, create_backup=False)
            self.assertEqual(items[0].original.read_bytes(), b"external save A")
            self.assertEqual(items[1].original.read_bytes(), b"external save B")
            copies = list(root.glob(".a.*.rollback.xlsx"))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_bytes(), b"original")
            self.assertIn(str(copies[0]), " ".join(raised.exception.__notes__))
            self.assertFalse(any(item.temporary.exists() for item in items))
