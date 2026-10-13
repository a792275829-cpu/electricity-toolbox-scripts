from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from toolbox.runtime import load_module

ROOT = Path(__file__).resolve().parents[2]
builder = load_module("maintenance_package_tests", ROOT / "packaging/windows/package_maintenance.py")
maintenance = load_module("maintenance_command_tests", ROOT / "packaging/windows/maintenance.py")


class WindowsMaintenanceTests(unittest.TestCase):
    def test_source_export_keeps_tests_locks_and_required_template_but_no_local_state(self):
        for name in ("desktop/src-tauri/Cargo.lock", "packaging/windows/requirements.lock",
                     "packaging/tauri/requirements-macos.lock", "电力工具箱/tests/test_catalog_loading.py", builder.TEMPLATE):
            with self.subTest(name=name):
                self.assertTrue(builder.source_file(Path(name)))
        for name in ("desktop/.env", "desktop/updater.key", "desktop/signing.pem", "desktop/src-tauri/payload/app.py",
                     "development/data/credentials.json", "runtime/python.exe", "wps自动/auth_state.json",
                     "市场表更新/config.json", "集团每日上传/真实业务.xlsx", "电力工具箱/测试样例/report.pdf",
                     "desktop/src-tauri/target/build.rs", ".git/config"):
            with self.subTest(name=name):
                self.assertFalse(builder.source_file(Path(name)))

    def test_environment_isolates_data_and_clears_foreign_python_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.dict(os.environ, {"PYTHONHOME": "/foreign", "TOOLBOX_WORKSPACE": "/production"}):
                env = maintenance.environment(root)
            self.assertNotIn("PYTHONHOME", env)
            self.assertNotIn("TOOLBOX_WORKSPACE", env)
            self.assertEqual(env["TOOLBOX_DATA_ROOT"], str(root / "development/data"))
            self.assertEqual(env["PYTHONPATH"], str(root))

    def test_integrity_readback_detects_modified_and_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "中文.py"
            target.write_bytes(b"original")
            (root / "维护包清单.json").write_text(json.dumps({"files": {
                target.name: {"sha256": hashlib.sha256(b"original").hexdigest()}}}), encoding="utf-8")
            self.assertTrue(maintenance.verify(root)["passed"])
            target.write_bytes(b"edited")
            self.assertEqual(maintenance.verify(root)["changed"], [target.name])
            target.unlink()
            self.assertEqual(maintenance.verify(root)["missing"], [target.name])

    def test_diagnostic_export_excludes_business_data_and_module_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("logs/maintenance/tests.json", "logs/maintenance/error.log", "logs/business.log",
                         "development/data/auth_state.json", "wps自动/logs/write.log", "logs/maintenance/private.zip"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test", encoding="utf-8")
            result = maintenance.export_diagnostics(root)
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(set(archive.namelist()), {"maintenance/tests.json", "maintenance/error.log"})


if __name__ == "__main__":
    unittest.main()
