from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from toolbox.runtime import ToolPaths, load_module

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "南方分公司电力市场日报"
sys.path.insert(0, str(REPORT_ROOT))
from gd_power_report.output_commit import output_directory_lock


class PortablePackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_module("windows_package_tests", ROOT / "packaging/windows/package_windows.py")

    def test_export_rejects_credentials_profiles_caches_and_outputs(self):
        for path in ("上网电量抓取/auth_state.json", "上网电量抓取/config.json",
                     "x/.browser-profile/Default/Cookies", "x/.qingneng-session/config.json",
                     ".toolbox/qingneng/config.json", "x/输出/report.xlsx", "x/node_modules/package/index.js",
                     "x/.state/cookies", "x/auth_state.json.lock", "测试样例/市场表更新/业务副本.xlsx"):
            with self.subTest(path=path):
                self.assertFalse(self.builder.exportable(Path(path)))
        self.assertTrue(self.builder.exportable(Path("广东电价预测/config.example.json")))
        self.assertTrue(self.builder.exportable(Path("packaging/windows/requirements.lock")))
        self.assertTrue(self.builder.exportable(Path("500kV断面分析/data/500kv_node_positions.json")))

    def test_exe_is_x64_and_uses_launcher_relative_interpreter(self):
        for diagnostic in (False, True):
            with self.subTest(diagnostic=diagnostic), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "中文 portable.exe"
                try:
                    self.builder.launcher(target, diagnostics=diagnostic)
                except ImportError:
                    self.skipTest("build-only pip/distlib unavailable in end-user runtime")
                data = target.read_bytes()
                pe = struct.unpack_from("<I", data, 0x3C)[0]
                self.assertEqual(struct.unpack_from("<H", data, pe + 4)[0], 0x8664)
                interpreter = b"python.exe" if diagnostic else b"pythonw.exe"
                self.assertIn(b"#!<launcher_dir>\\runtime\\python311\\" + interpreter + b" -I\n", data)
                with zipfile.ZipFile(target) as archive:
                    script = archive.read("__main__.py").decode()
                    compile(script, "__main__.py", "exec")
                    self.assertIn("Path(sys.argv[0]).resolve().parent", script)


class WindowsWpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wps = load_module("windows_wps_tests", ToolPaths(ROOT).wps_writer)

    def test_bundled_browser_wins_over_installed_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = Path(folder) / "chromium-1181/chrome-win/chrome.exe"
            browser.parent.mkdir(parents=True)
            browser.touch()
            with mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": folder}):
                self.assertEqual(self.wps.find_windows_chrome(), browser)

    def test_windows_command_verifies_exact_profile_and_main_process(self):
        with mock.patch.object(self.wps.sys, "platform", "win32"):
            command = '"C:\\portable app\\chrome.exe" "--user-data-dir=' + str(self.wps.PROFILE_DIR) + '" --remote-debugging-port=9222'
            self.assertTrue(self.wps.is_managed_chrome_command(command))
            self.assertFalse(self.wps.is_managed_chrome_command(command + " --type=renderer"))
            self.assertFalse(self.wps.is_managed_chrome_command(command.replace(str(self.wps.PROFILE_DIR), str(self.wps.PROFILE_DIR) + "-other")))

    def test_windows_pid_check_never_sends_os_kill_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            pid = Path(folder) / "wps.pid"
            pid.write_text("123")
            with (mock.patch.object(self.wps.sys, "platform", "win32"),
                  mock.patch.object(self.wps, "WPS_CHROME_PID_PATH", pid),
                  mock.patch.object(self.wps, "managed_chrome_is_running", return_value=True),
                  mock.patch.object(self.wps, "managed_chrome_command", return_value="owned"),
                  mock.patch.object(self.wps, "is_managed_chrome_command", return_value=True),
                  mock.patch.object(self.wps.os, "kill", side_effect=AssertionError("unsafe kill"))):
                self.assertEqual(self.wps.managed_chrome_pid(), 123)

    def test_windows_discovery_reads_utf8_json_and_ignores_other_profiles(self):
        command = '"C:\\Chrome\\chrome.exe" "--user-data-dir=' + str(self.wps.PROFILE_DIR) + '"'
        response = mock.Mock(returncode=0, stdout=json.dumps([
            {"ProcessId": 123, "CommandLine": command},
            {"ProcessId": 456, "CommandLine": command + " --type=renderer"},
        ]))
        with (mock.patch.object(self.wps.sys, "platform", "win32"),
              mock.patch.object(self.wps.subprocess, "run", return_value=response)):
            self.assertEqual(self.wps.discover_managed_chrome_pid(), 123)

    def test_windows_stop_refuses_reused_pid(self):
        with (mock.patch.object(self.wps.sys, "platform", "win32"),
              mock.patch.object(self.wps, "managed_chrome_is_running", return_value=True),
              mock.patch.object(self.wps, "managed_chrome_command", return_value="unrelated process"),
              mock.patch.object(self.wps.subprocess, "run") as run):
            with self.assertRaisesRegex(RuntimeError, "进程身份"):
                self.wps.stop_managed_chrome(123, lambda _text: None, "test")
            run.assert_not_called()


class OutputLockTests(unittest.TestCase):
    def child(self, directory):
        return [sys.executable, "-c",
                "from pathlib import Path; import sys; "
                "from gd_power_report.output_commit import output_directory_lock; "
                "lock=output_directory_lock(Path(sys.argv[1])); lock.__enter__(); lock.__exit__(None,None,None)",
                str(directory)]

    def test_same_directory_blocks_other_process_and_releases_without_files(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            environment = {**os.environ, "PYTHONPATH": str(REPORT_ROOT)}
            with output_directory_lock(directory):
                result = subprocess.run(self.child(directory), env=environment,
                                        capture_output=True, text=True, encoding="utf-8", timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("同日期日报正在保存", result.stderr)
            result = subprocess.run(self.child(directory), env=environment,
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(directory.iterdir()), [])

    def test_crashed_process_does_not_leave_stale_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            script = ("from pathlib import Path; import sys,time; "
                      "from gd_power_report.output_commit import output_directory_lock; "
                      "lock=output_directory_lock(Path(sys.argv[1])); lock.__enter__(); "
                      "print('ready',flush=True); time.sleep(30)")
            process = subprocess.Popen([sys.executable, "-c", script, folder],
                env={**os.environ, "PYTHONPATH": str(REPORT_ROOT)}, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
            finally:
                process.terminate()
                process.communicate(timeout=10)
            with output_directory_lock(directory):
                self.assertEqual(list(directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
