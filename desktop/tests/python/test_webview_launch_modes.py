"""Launch mode selection must not inherit an offline fixture into live testing."""
from pathlib import Path
import os
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]

class WebViewLaunchModeTests(unittest.TestCase):
    def smoke(self, flag=None, entry='源码调试版.command'):
        env = {
            **os.environ,
            'TOOLBOX_SMOKE': '1',
            'TOOLBOX_WEBVIEW_UI': '1',
            'TOOLBOX_WEBVIEW_UI_SAMPLE': '1',
            'TOOLBOX_WEBVIEW_PILOT': '1',
            'TOOLBOX_WEBVIEW_PILOT_FIXTURE': '1',
        }
        if entry == '源码调试版.command':
            env.update(TOOLBOX_WORKSPACE='/old/workspace', TOOLBOX_CODE_ROOT='/old/code',
                       TOOLBOX_APP_HOME='/old/home', TOOLBOX_DATA_ROOT='/old/data')
        result=subprocess.run([str(ROOT/'00_启动'/entry), *([flag] if flag else [])],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        return dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)

    def assert_data_root(self, values, mode):
        app_home = Path.home() / 'Library/Application Support/com.electricity.toolbox/development/data' / mode
        self.assertEqual(Path(values['WEBVIEW_DATA_HOME']), app_home)
        self.assertEqual(Path(values['WEBVIEW_DATA_ROOT']), app_home / 'data')
        self.assertEqual(Path(values['WEBVIEW_WORKSPACE']).resolve(), ROOT)

    def test_live_mode_clears_all_inherited_fixture_flags(self):
        values=self.smoke()
        self.assertEqual(values['WEBVIEW_UI_MODE'],'live')
        self.assertEqual(values['WEBVIEW_UI_SAMPLE'],'0')
        self.assertEqual(values['WEBVIEW_PILOT_FIXTURE'],'0')
        self.assert_data_root(values, 'webview-ui')

    def test_standalone_live_launcher_is_removed(self):
        self.assertFalse((ROOT / '00_启动/真实业务测试版.command').exists())

    def test_sample_mode_stays_isolated(self):
        values=self.smoke('--webview-ui-sample')
        self.assertEqual(values['WEBVIEW_UI_MODE'],'offline')
        self.assertEqual(values['WEBVIEW_UI_SAMPLE'],'1')
        self.assertEqual(values['WEBVIEW_PILOT_FIXTURE'],'0')
        self.assert_data_root(values, 'webview-ui-sample')

    def test_tk_verification_tools_remain_available(self):
        for flag, path in (
            ('--ui-reference', 'desktop/python/source_debug.py'),
            ('--accessibility-probe', 'tooling/accessibility_probe.py'),
        ):
            with self.subTest(flag=flag):
                values = self.smoke(flag)
                self.assertEqual(Path(values['DEBUG_PYTHON_ENTRY']).resolve(), ROOT / path)
                self.assertNotIn('WEBVIEW_UI_MODE', values)

    def test_pilot_does_not_inherit_full_ui(self):
        values=self.smoke('--webview-pilot', entry='Tauri开发版.command')
        self.assertEqual(values['WEBVIEW_PILOT_MODE'],'offline')
        self.assertNotIn('WEBVIEW_UI_MODE',values)
    def test_default_tk_host_does_not_inherit_a_webview_mode(self):
        values=self.smoke(entry='Tauri开发版.command')
        self.assertNotIn('WEBVIEW_UI_MODE',values)
        self.assertNotIn('WEBVIEW_PILOT_MODE',values)

if __name__=='__main__':unittest.main()
