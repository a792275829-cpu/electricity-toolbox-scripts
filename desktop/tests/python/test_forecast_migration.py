from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("migrate_forecast_data", ROOT / "packaging/tauri/migrate_forecast_data.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class ForecastMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "original"
        self.target = Path(self.temporary.name) / "native"
        self.source_module = self.source / "广东电价预测"
        self.target_module = self.target / "广东电价预测"
        for module in (self.source_module, self.target_module):
            (module / "gd_price_forecast").mkdir(parents=True)
            shutil.copyfile(ROOT / "广东电价预测/gd_price_forecast/paths.py", module / "gd_price_forecast/paths.py")
        self.master = self.source_module / "自选历史/业务总表.xlsx"
        self.master.parent.mkdir()
        self.master.write_bytes(b"original-workbook-byte-content")
        (self.source_module / "config.json").write_text(json.dumps({"masterWorkbook": str(self.master)}))
        self.config = self.target_module / "config.json"
        self.config.write_text(json.dumps({"username": "saved-account", "initialDate": "2021-01-01", "custom": False}))

    def test_configured_workbook_is_copied_with_portable_paths_and_existing_settings(self):
        original_config = self.config.read_bytes()
        report = migration.migrate(self.source, self.target)
        actual = migration.resolve_paths(self.target_module)
        self.assertEqual(actual["masterWorkbook"].read_bytes(), self.master.read_bytes())
        self.assertTrue(actual["predictionOutputDir"].is_dir())
        config = json.loads(self.config.read_text())
        self.assertEqual(config["masterWorkbook"], "输出/业务总表.xlsx")
        self.assertEqual(config["username"], "saved-account")
        self.assertEqual(config["initialDate"], "2021-01-01")
        self.assertIs(config["custom"], False)
        self.assertEqual((Path(report["backup"]) / "config.json").read_bytes(), original_config)
        self.assertEqual(migration.migrate(self.source, self.target)["copied_workbooks"], 0)

    def test_existing_different_data_is_preserved_without_config_change(self):
        dest = self.target_module / "输出/业务总表.xlsx"
        dest.parent.mkdir()
        dest.write_bytes(b"new-user-data")
        previous = self.config.read_bytes()
        with self.assertRaises(FileExistsError):
            migration.migrate(self.source, self.target)
        self.assertEqual(dest.read_bytes(), b"new-user-data")
        self.assertEqual(self.config.read_bytes(), previous)

    def test_optional_initial_workbook_is_copied(self):
        initial = self.source_module / "输入/广东数据初始总表.xlsx"
        initial.parent.mkdir()
        initial.write_bytes(b"initial-data")
        report = migration.migrate(self.source, self.target)
        self.assertTrue(report["initial_workbook_available"])
        self.assertEqual(migration.resolve_paths(self.target_module)["initialWorkbook"].read_bytes(), initial.read_bytes())

    def test_failed_config_write_removes_new_copy_and_preserves_originals(self):
        previous = self.config.read_bytes()
        with mock.patch.object(migration, "write_private_json", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                migration.migrate(self.source, self.target)
        self.assertFalse((self.target_module / "输出/业务总表.xlsx").exists())
        self.assertEqual(self.config.read_bytes(), previous)
        self.assertEqual(self.master.read_bytes(), b"original-workbook-byte-content")

    def test_failed_path_verification_restores_previous_config(self):
        previous = self.config.read_bytes()
        resolver = migration.resolve_paths

        def fail_target(module):
            if module == self.target_module.resolve():
                raise RuntimeError("verification failed")
            return resolver(module)

        with mock.patch.object(migration, "resolve_paths", side_effect=fail_target):
            with self.assertRaises(RuntimeError):
                migration.migrate(self.source, self.target)
        self.assertFalse((self.target_module / "输出/业务总表.xlsx").exists())
        self.assertEqual(self.config.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
