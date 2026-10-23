"""Real user-level copy and rollback, without touching the user's installation."""
import importlib.util
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("user_install", ROOT / "packaging/tauri/install_user_app.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@unittest.skipUnless(shutil.which("codesign") and shutil.which("ditto"), "macOS required")
class DmgInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / "Applications/电力工具箱.app"
        self.source = self.base / "image/电力工具箱.app"
        self.bundle(self.source, "0.1.7")
        self.guard = patch.object(installer, "ensure_stopped")
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def bundle(self, app, version, identity="com.electricity.toolbox"):
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir()
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": identity, "CFBundleShortVersionString": version,
            "CFBundleExecutable": "electricity-toolbox", "CFBundlePackageType": "APPL",
        }))
        shutil.copyfile("/usr/bin/true", contents / "MacOS/electricity-toolbox")
        (contents / "MacOS/electricity-toolbox").chmod(0o755)
        (contents / "Resources/template.txt").write_text(version)
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)

    def test_real_signed_copy_replaces_old_app_and_keeps_business_data(self):
        self.bundle(self.target, "0.1.6")
        data = self.base / "data/config.json"
        data.parent.mkdir(); data.write_text("local settings")
        self.assertEqual(installer.install(self.source, self.target), self.target)
        self.assertEqual((self.target / "Contents/Resources/template.txt").read_text(), "0.1.7")
        self.assertEqual(data.read_text(), "local settings")
        self.assertEqual((self.source / "Contents/Resources/template.txt").read_text(), "0.1.7")
        self.assertEqual(list(self.target.parent.glob(".toolbox-install-*")), [])

    def test_failed_swap_restores_previous_app(self):
        self.bundle(self.target, "0.1.6")
        original = Path.rename
        def rename(path, destination):
            if path.name == "电力工具箱.app" and path.parent.name.startswith(".toolbox-install-"):
                raise OSError("simulated replacement failure")
            return original(path, destination)
        with patch.object(Path, "rename", rename), self.assertRaises(OSError):
            installer.install(self.source, self.target)
        self.assertEqual(installer.app_info(self.target), (0, 1, 6))

    def test_launch_failure_restores_old_signed_app_before_returning(self):
        self.bundle(self.target, "0.1.6")
        def launch(app):
            self.assertEqual(installer.app_info(app), (0, 1, 7))
            self.assertEqual(len(list(app.parent.glob(".toolbox-install-*/previous.app"))), 1)
            raise OSError("launch failed")
        with self.assertRaisesRegex(OSError, "launch failed"):
            installer.install(self.source, self.target, after_install=launch)
        self.assertEqual(installer.app_info(self.target), (0, 1, 6))
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(self.target)], check=True, capture_output=True)
        self.assertEqual(list(self.target.parent.glob(".toolbox-install-*")), [])

    def test_first_install_launch_failure_removes_failed_application(self):
        with self.assertRaisesRegex(OSError, "launch failed"):
            installer.install(self.source, self.target, after_install=lambda _: (_ for _ in ()).throw(OSError("launch failed")))
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob(".toolbox-install-*")), [])

    def test_failed_rollback_keeps_recoverable_backup(self):
        self.bundle(self.target, "0.1.6")
        original = Path.rename
        def rename(path, destination):
            if path.parent.name.startswith(".toolbox-install-"):
                raise OSError("simulated disk failure")
            return original(path, destination)
        with patch.object(Path, "rename", rename), self.assertRaises(OSError):
            installer.install(self.source, self.target)
        backups = list(self.target.parent.glob(".toolbox-install-*/previous.app"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(installer.app_info(backups[0]), (0, 1, 6))

    def test_rejects_running_app_newer_app_and_unrelated_destination(self):
        with patch.object(installer, "ensure_stopped", side_effect=ValueError("请先退出")):
            with self.assertRaisesRegex(ValueError, "请先退出"):
                installer.install(self.source, self.target)
        self.bundle(self.target, "9.0.0")
        with self.assertRaisesRegex(ValueError, "更新版本"):
            installer.install(self.source, self.target)
        shutil.rmtree(self.target)
        self.bundle(self.target, "0.1.0", identity="other.app")
        with self.assertRaisesRegex(ValueError, "标识不匹配"):
            installer.install(self.source, self.target)

    def test_rejects_symlink_target(self):
        self.target.parent.mkdir()
        self.target.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.install(self.source, self.target)
