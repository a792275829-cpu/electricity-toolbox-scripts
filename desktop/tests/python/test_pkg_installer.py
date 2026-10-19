from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "packaging/tauri/pkg-scripts"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "home"
        self.canonical = self.base / "Applications/电力工具箱.app"
        self.marker = self.home / "Library/Application Support/com.electricity.toolbox/update-source.txt"
        self.scripts = self.base / "scripts"
        shutil.copytree(SCRIPTS, self.scripts)
        common = (self.scripts / "common.sh").read_text()
        # Run the exact cleanup functions against isolated paths; only installation
        # locations and console-user discovery are replaced in the test copy.
        start = common.index('CONSOLE_USER=')
        end = common.index('plist_value()')
        common = common[:start] + f'''CANONICAL='{self.canonical}'
CONSOLE_USER='{os.environ.get("USER", "auren")}'
USER_HOME='{self.home}'
MARKER='{self.marker}'
''' + common[end:]
        (self.scripts / "common.sh").write_text(common)
        self.bundle(self.canonical, "0.2.0")

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, path, version="0.1.3", identity="com.electricity.toolbox"):
        contents = path / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identity,
            "CFBundleShortVersionString": version, "CFBundleVersion": version,
            "CFBundleExecutable": "toolbox", "CFBundleName": "电力工具箱", "CFBundlePackageType": "APPL"}))
        binary = contents / "MacOS/toolbox"
        binary.parent.mkdir()
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)

    def run_script(self, name):
        return subprocess.run(["/bin/sh", str(self.scripts / name)], capture_output=True, text=True, timeout=10)

    def test_cleanup_replaces_desktop_entry_removes_duplicates_and_keeps_data(self):
        desktop = self.home / "Desktop/电力工具箱.app"
        downloads = self.home / "Downloads/电力工具箱.app"
        moved = self.home / "custom/旧工具箱.app"
        for path in (desktop, downloads, moved):
            self.bundle(path)
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text(str(moved) + "\n")
        account = self.marker.parent / "data/account.json"
        account.parent.mkdir()
        account.write_text("private-test-data")
        result = self.run_script("postinstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.canonical.is_dir())
        self.assertTrue(desktop.is_symlink())
        self.assertEqual(desktop.resolve(), self.canonical)
        self.assertFalse(downloads.exists())
        self.assertFalse(moved.exists())
        self.assertEqual(account.read_text(), "private-test-data")
        self.assertFalse(self.marker.exists())

    def test_newer_unrelated_and_external_symlink_bundles_are_preserved(self):
        newer = self.home / "Desktop/电力工具箱.app"
        unrelated = self.home / "Downloads/电力工具箱.app"
        external = self.base / "outside/电力工具箱.app"
        self.bundle(newer, "9.0.0")
        self.bundle(unrelated, identity="example.other")
        self.bundle(external)
        (self.home / "Applications").symlink_to(external.parent, target_is_directory=True)
        result = self.run_script("postinstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (newer, unrelated, external):
            self.assertTrue(path.is_dir())

    def test_preinstall_stops_when_running_app_rejects_exit(self):
        # A simulated still-running app must cause installation to fail, with no
        # forced kill or deletion. Polling is accelerated only in this test copy.
        fake_ps = self.base / "ps"
        fake_ps.write_text(f'#!/bin/sh\nprintf "%s\\n" "{self.canonical}/Contents/MacOS/toolbox"\n')
        fake_ps.chmod(0o755)
        pre = self.scripts / "preinstall"
        text = pre.read_text().replace('/bin/ps -axo command=', str(fake_ps))
        text = text.replace('/bin/sleep 1', '/usr/bin/true')
        pre.write_text(text)
        result = self.run_script("preinstall")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("仍有任务运行", result.stderr)
        self.assertTrue(self.canonical.exists())

    @unittest.skipUnless(shutil.which("pkgbuild"), "macOS pkgbuild required")
    def test_real_pkg_build_and_expand_contains_application_and_scripts(self):
        sys.path.insert(0, str(ROOT / "packaging/tauri"))
        from build_pkg import build
        resource = self.canonical / "Contents/Resources/模板/日报.docx"
        resource.parent.mkdir(parents=True)
        resource.write_bytes(b"template")
        resource.chmod(0o600)
        resource.parent.chmod(0o700)
        binary = self.canonical / "Contents/MacOS/toolbox"
        binary.chmod(0o700)
        external = self.base / "private.txt"
        external.write_text("private")
        external.chmod(0o600)
        (resource.parent / "link").symlink_to(external)
        path = build(self.canonical, self.base / "output")
        self.assertTrue(path.is_file())
        self.assertTrue(path.with_suffix(".pkg.sha256").is_file())
        expanded = self.base / "expanded"
        subprocess.run(["/usr/sbin/pkgutil", "--expand-full", str(path), str(expanded)], check=True, capture_output=True)
        self.assertTrue(list(expanded.rglob("preinstall")))
        self.assertTrue(list(expanded.rglob("postinstall")))
        self.assertTrue(list(expanded.rglob("common.sh")))
        info = next(expanded.rglob("PackageInfo")).read_text()
        self.assertIn('relocatable="false"', info)
        self.assertIn('overwrite-permissions="true"', info)
        installed = next(expanded.rglob("日报.docx"))
        self.assertEqual(installed.read_bytes(), b"template")
        self.assertEqual(installed.stat().st_mode & 0o777, 0o644)
        self.assertEqual(installed.parent.stat().st_mode & 0o777, 0o755)
        installed_binary = next(expanded.rglob("MacOS/toolbox"))
        self.assertEqual(installed_binary.stat().st_mode & 0o777, 0o755)
        self.assertEqual(resource.stat().st_mode & 0o777, 0o600)
        self.assertEqual(external.stat().st_mode & 0o777, 0o600)
        self.assertTrue((installed.parent / "link").is_symlink())


if __name__ == "__main__":
    unittest.main()
