from __future__ import annotations

import sys
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "南方分公司电力市场日报"


class MarketReportIntegrationTests(unittest.TestCase):
    def test_report_uses_toolbox_login_code_with_separate_state(self) -> None:
        sys.path.insert(0, str(REPORT_ROOT))
        try:
            from gd_power_report.collector.auth import load_toolbox_auth_module

            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                login_dir = workspace / "上网电量抓取"
                login_dir.mkdir()
                shutil.copy2(ROOT / "上网电量抓取" / "export_online_energy.py", login_dir)
                (login_dir / "auth_state.json").write_text('{"cookies": []}', encoding="utf-8")
                state_dir = workspace / "南方分公司电力市场日报" / ".state"
                login = load_toolbox_auth_module(workspace, state_dir)
                self.assertEqual(login.PROFILE_DIR, state_dir / ".browser-profile")
                self.assertEqual(login.AUTH_STATE_PATH, state_dir / "auth_state.json")
                self.assertEqual(login.AUTH_LOCK_PATH, state_dir / "auth_state.json.lock")
                self.assertEqual(login.CONFIG_PATH, (login_dir / "config.json").resolve())
                self.assertNotEqual(login.PROFILE_DIR, login_dir / ".browser-profile")
                self.assertEqual(login.AUTH_STATE_PATH.read_text(encoding="utf-8"), '{"cookies": []}')
                self.assertFalse(login.AUTH_LOCK_PATH.exists())
        finally:
            sys.path.remove(str(REPORT_ROOT))


if __name__ == "__main__":
    unittest.main()
