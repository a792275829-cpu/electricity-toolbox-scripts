from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging/tauri")]
import workspace_layout as layout
import prepare_workspace as installer
from toolbox_storage import data_path


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = (Path(self.temp.name) / "用户 data").resolve()
        self.old = self.home / "workspace"
        self.files = {
            "模块/tool.py": "source",
            "模块/config.json": json.dumps({"outputDir": str(self.old / "模块/输出"),
                                         "external": "/external/workbook.xlsx"}),
            "模块/.browser-profile/Default/Cookies": "session",
            "模块/输出/数据.xlsx": "workbook",
            "测试样例/模块/输入.xlsx": "sample",
            "电力工具箱/file_dialog_settings.json": json.dumps({"common_directory": str(self.old / "测试样例")}),
            ".toolbox/qingneng/config.json": '{"username":"fixture","password":"fixture"}',
        }
        for name, content in self.files.items():
            path = self.old / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        layout.write_json(self.old / ".toolbox/desktop/installed.json", {
            "files": {"模块/tool.py": installer.digest(self.old / "模块/tool.py")}, "build": "v1"})

    def test_migration_separates_sources_data_samples_and_rewrites_only_internal_paths(self):
        result = layout.initialize(self.home)
        self.assertEqual(result["version"], 2)
        self.assertEqual((self.home / "app/模块/tool.py").read_text(), "source")
        for name in ("模块/.browser-profile/Default/Cookies", "模块/输出/数据.xlsx", ".toolbox/qingneng/config.json"):
            self.assertEqual((self.home / "data" / name).read_text(), self.files[name])
        self.assertEqual((self.home / "samples/模块/输入.xlsx").read_text(), "sample")
        config = json.loads((self.home / "data/模块/config.json").read_text())
        self.assertEqual(config["outputDir"], str(self.home / "data/模块/输出"))
        self.assertEqual(config["external"], "/external/workbook.xlsx")
        settings = json.loads((self.home / "data/电力工具箱/file_dialog_settings.json").read_text())
        self.assertEqual(settings["common_directory"], str(self.home / "samples"))
        self.assertEqual((self.home / "backups/layout-v1-configs/data/模块/config.json").read_text(), self.files["模块/config.json"])
        self.assertFalse(self.old.exists())
        self.assertEqual(layout.initialize(self.home), result)

    def test_destination_collision_preserves_both_files_before_any_move(self):
        target = self.home / "data/模块/输出/数据.xlsx"
        target.parent.mkdir(parents=True)
        target.write_text("user edit")
        with self.assertRaises(FileExistsError):
            layout.initialize(self.home)
        self.assertEqual(target.read_text(), "user edit")
        self.assertTrue((self.old / "模块/tool.py").exists())

    def test_move_failure_rolls_back_all_files_and_can_retry(self):
        rename = Path.rename
        calls = 0

        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk error")
            return rename(source, target)

        with patch.object(Path, "rename", fail_second), self.assertRaises(OSError):
            layout.initialize(self.home)
        for name, content in self.files.items():
            self.assertEqual((self.old / name).read_text(), content)
        self.assertFalse((self.home / "state/layout.json").exists())
        layout.initialize(self.home)

    def test_config_write_failure_restores_original_paths_and_files(self):
        writer = layout.write_json

        def fail_config(path, value):
            if path == self.home / "data/模块/config.json":
                raise OSError("disk full")
            return writer(path, value)

        with patch.object(layout, "write_json", fail_config), self.assertRaises(OSError):
            layout.initialize(self.home)
        for name, content in self.files.items():
            self.assertEqual((self.old / name).read_text(), content)

    def test_incomplete_previous_migration_is_recovered_before_retry(self):
        source, target = self.old / "模块/tool.py", self.home / "app/模块/tool.py"
        target.parent.mkdir(parents=True)
        source.rename(target)
        layout.write_json(self.home / "state/layout-pending.json", {
            "moves": [["workspace/模块/tool.py", "app/模块/tool.py"]], "rewritten_configs": []})
        layout.initialize(self.home)
        self.assertEqual(target.read_text(), "source")

    def test_missing_manifest_is_rejected_without_guessing(self):
        (self.old / ".toolbox/desktop/installed.json").unlink()
        with self.assertRaises(RuntimeError):
            layout.initialize(self.home)
        self.assertTrue((self.old / "模块/tool.py").is_file())

    def test_development_import_rebases_paths_and_preserves_production_data(self):
        development = self.home / "development"
        layout.import_legacy(self.old, development)
        config = json.loads((development / "data/模块/config.json").read_text())
        self.assertEqual(config["outputDir"], str(development / "data/模块/输出"))
        for name, content in self.files.items():
            self.assertEqual((self.old / name).read_text(), content)
        with self.assertRaises(FileExistsError):
            layout.import_legacy(self.old, development)

    def test_fresh_install_defaults_and_updates_never_write_config_into_app(self):
        payload = self.home / "payload"
        (payload / "app").mkdir(parents=True)
        (payload / "app/tool.py").write_text("v2")
        (payload / "defaults/模块").mkdir(parents=True)
        (payload / "defaults/模块/config.json").write_text('{"new":true}')
        layout.initialize(self.home)
        layout.write_json(payload / "manifest.json", {"build": "v2", "files": {
            "tool.py": installer.digest(payload / "app/tool.py")}, "defaults": ["模块/config.json"]})
        installer.install(payload, self.home / "app", data_root=self.home / "data",
                          state_root=self.home / "state", backup_root=self.home / "backups/source-updates")
        self.assertFalse((self.home / "app/模块/config.json").exists())
        config = json.loads((self.home / "data/模块/config.json").read_text())
        self.assertNotIn("new", config)
        self.assertEqual((self.home / "app/tool.py").read_text(), "v2")

    def test_bundled_installer_migrates_under_isolated_python_and_can_restart(self):
        import shutil
        payload = Path(self.temp.name) / "payload"
        (payload / "app/模块").mkdir(parents=True)
        (payload / "app/模块/tool.py").write_text("v2")
        bootstrap = payload / "bootstrap"
        bootstrap.mkdir()
        for name in ("prepare_workspace.py", "workspace_layout.py"):
            shutil.copy2(ROOT / "packaging/tauri" / name, bootstrap / name)
        layout.write_json(payload / "manifest.json", {"build": "v2", "files": {
            "模块/tool.py": installer.digest(payload / "app/模块/tool.py")}, "defaults": []})
        command = [sys.executable, "-I", "-B", str(bootstrap / "prepare_workspace.py"),
                   str(payload), str(self.home), "--managed"]
        for _ in range(2):
            result = subprocess.run(command, cwd=self.temp.name, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / "app/模块/tool.py").read_text(), "v2")
        self.assertEqual((self.home / "data/模块/.browser-profile/Default/Cookies").read_text(), "session")
        self.assertEqual(json.loads((self.home / "state/installed.json").read_text())["build"], "v2")


class StoragePathTests(unittest.TestCase):
    def test_source_mode_and_external_paths_are_unchanged(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(data_path(ROOT / "模块/config.json"), ROOT / "模块/config.json")
        with patch.dict(os.environ, {"TOOLBOX_CODE_ROOT": str(ROOT), "TOOLBOX_DATA_ROOT": "/tmp/data"}):
            self.assertEqual(data_path(ROOT / "模块/config.json"), Path("/tmp/data/模块/config.json").resolve())
            self.assertEqual(data_path("/external/workbook.xlsx"), Path("/external/workbook.xlsx"))
            self.assertEqual(data_path("/tmp/data/模块/config.json"), Path("/tmp/data/模块/config.json"))

    def test_node_and_python_resolve_the_same_shared_account_path(self):
        import shutil
        if not shutil.which("node"):
            self.skipTest("Node unavailable")
        module = ROOT / "电力工具脚本/private-data-uploader-tool/scripts/storage-paths.mjs"
        env = {**os.environ, "TOOLBOX_CODE_ROOT": str(ROOT), "TOOLBOX_DATA_ROOT": str(ROOT / ".toolbox/test-data")}
        path = ROOT / ".toolbox/qingneng/config.json"
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e",
            f"import {{dataPath}} from {json.dumps(module.as_uri())};console.log(dataPath({json.dumps(str(path))}));"],
            env=env, capture_output=True, text=True, check=True)
        with patch.dict(os.environ, env):
            self.assertEqual(result.stdout.strip(), str(data_path(path)))

    def test_payload_only_contains_runtime_sources_and_required_assets(self):
        spec = importlib.util.spec_from_file_location("payload_layout_test", ROOT / "packaging/tauri/prepare_payload.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ("desktop/python/native_bridge.py", "toolbox_storage.py", "风力预测截图/fonts/NotoSerifSC.ttf",
                     "每日生产经营情况汇报自动生成工具/模板/每日生产经营情况汇报（自动）.docx", "500kV断面分析/500kV节点拓扑.md"):
            self.assertTrue(module.runtime_source(Path(name)), name)
        for name in ("desktop/tests/python/test_desktop.py", "desktop/src/App.tsx", "packaging/tauri/prepare_payload.py",
                     ".github/workflows/desktop-ci.yml", "电力工具箱/tests/test_toolbox.py", "上网电量抓取/config.json",
                     "上网电量抓取/auth_state.json", "测试样例/输入.xlsx", "00_启动/电力工具箱.command"):
            self.assertFalse(module.runtime_source(Path(name)), name)


if __name__ == "__main__":
    unittest.main()
