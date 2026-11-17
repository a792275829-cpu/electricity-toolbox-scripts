"""Launch mode selection must not inherit an offline fixture into live testing."""
from pathlib import Path
import os
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]

class WebViewLaunchModeTests(unittest.TestCase):
    def smoke(self, flag=None, entry='Tauri开发版.command'):
        env={**os.environ,'TOOLBOX_SMOKE':'1','TOOLBOX_WEBVIEW_UI':'1','TOOLBOX_WEBVIEW_UI_SAMPLE':'1','TOOLBOX_WEBVIEW_PILOT':'1','TOOLBOX_WEBVIEW_PILOT_FIXTURE':'1'}
        result=subprocess.run([str(ROOT/'00_启动'/entry), *([flag] if flag else [])],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        return dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    def test_live_mode_clears_all_inherited_fixture_flags(self):
        values=self.smoke('--webview-ui')
        self.assertEqual(values['WEBVIEW_UI_MODE'],'live')
        self.assertEqual(values['WEBVIEW_UI_SAMPLE'],'0')
        self.assertEqual(values['WEBVIEW_PILOT_FIXTURE'],'0')
        self.assertTrue(values['WEBVIEW_DATA_HOME'].endswith('/webview-ui'))
    def test_double_click_entry_always_selects_live_mode(self):
        values=self.smoke(entry='真实业务测试版.command')
        self.assertEqual(values['WEBVIEW_UI_MODE'],'live')
        self.assertEqual(values['WEBVIEW_UI_SAMPLE'],'0')
        self.assertEqual(values['WEBVIEW_PILOT_FIXTURE'],'0')
    def test_sample_mode_stays_isolated(self):
        values=self.smoke('--webview-ui-sample')
        self.assertEqual(values['WEBVIEW_UI_MODE'],'offline')
        self.assertEqual(values['WEBVIEW_UI_SAMPLE'],'1')
        self.assertEqual(values['WEBVIEW_PILOT_FIXTURE'],'0')
        self.assertTrue(values['WEBVIEW_DATA_HOME'].endswith('/webview-ui-sample'))
    def test_pilot_does_not_inherit_full_ui(self):
        values=self.smoke('--webview-pilot')
        self.assertEqual(values['WEBVIEW_PILOT_MODE'],'offline')
        self.assertNotIn('WEBVIEW_UI_MODE',values)
    def test_default_tk_host_does_not_inherit_a_webview_mode(self):
        values=self.smoke()
        self.assertNotIn('WEBVIEW_UI_MODE',values)
        self.assertNotIn('WEBVIEW_PILOT_MODE',values)

if __name__=='__main__':unittest.main()
