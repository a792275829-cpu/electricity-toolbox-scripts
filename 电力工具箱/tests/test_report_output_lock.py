from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPORT_ROOT = Path(__file__).resolve().parents[2] / "南方分公司电力市场日报"
sys.path.insert(0, str(REPORT_ROOT))
from gd_power_report.output_commit import output_directory_lock


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
