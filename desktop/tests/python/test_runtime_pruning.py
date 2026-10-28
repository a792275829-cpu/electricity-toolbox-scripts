"""Packaging must keep runtime data while removing rebuild-only files."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packaging/tauri"))
from prune_runtime import prune
from toolbox_shared.browser_runtime import chromium_launch_options


class RuntimePruningTests(unittest.TestCase):
    def test_only_allowlisted_resources_removed_and_repeat_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = "python/lib/python3.11/site-packages"
            keep = ["python/bin/python3", "node/bin/node", "node/LICENSE", "python/include/Python.h",
                    f"{site}/pandas/data/example.csv", f"{site}/numpy/testing/helper.py", f"{site}/numpy/_core/tests/_natype.py",
                    f"{site}/scipy/special/_ufuncs.so", f"{site}/pandas-1.dist-info/LICENSE",
                    "python/share/licenses/tcl-tk/tcl.txt", "ms-playwright/chromium-1187/browser"]
            remove = ["node/include/node.h",
                      "python/lib/pkgconfig/python.pc", f"{site}/pandas/tests/test_frame.py",
                      f"{site}/scipy/special/tests/test_basic.py",
                      "ms-playwright/chromium_headless_shell-1187/browser"]
            for name in keep + remove:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            registry = root / site / "playwright/driver/package/browsers.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({"browsers": [{"name": "chromium", "revision": "1187"}]}))
            report = prune(root)
            self.assertEqual(report["removed_bytes"], len(remove) * len("fixture"))
            for name in keep:
                self.assertTrue((root / name).is_file(), name)
            for name in remove:
                self.assertFalse((root / name).exists(), name)
            self.assertEqual(prune(root)["removed_bytes"], 0)
            (root / "ms-playwright/chromium-1187/browser").unlink()
            (root / "ms-playwright/chromium-1187").rmdir()
            with self.assertRaisesRegex(ValueError, "Chromium"):
                prune(root)

    def test_portable_launch_and_fallback_both_use_full_chromium(self):
        with patch.dict(os.environ, {"TOOLBOX_PORTABLE": "1"}):
            self.assertEqual(chromium_launch_options(), {"channel": "chromium"})
            self.assertEqual(chromium_launch_options(prefer_system=True), {"channel": "chromium"})
        with patch.dict(os.environ, {"TOOLBOX_PORTABLE": "0"}):
            self.assertEqual(chromium_launch_options(), {})
            self.assertEqual(chromium_launch_options(prefer_system=True), {"channel": "chrome"})

    def test_all_python_browser_consumers_select_full_browser_on_fallback(self):
        from unittest.mock import Mock, patch
        from toolbox.runtime import load_module
        from playwright.sync_api import Error
        from toolbox_shared import wps_browser
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"TOOLBOX_PORTABLE": "1"}):
            for name, source in (("prune_online", "上网电量抓取/export_online_energy.py"),
                                 ("prune_group", "集团每日上传/upload_daily_report.py")):
                module = load_module(name, ROOT / source)
                sdk = Mock()
                context = Mock()
                sdk.chromium.launch_persistent_context.side_effect = [Error("first launch failed"), context]
                with patch.object(module, "PROFILE_DIR", Path(directory) / name), \
                     patch.object(module, "prepare_browser_session", return_value=True):
                    self.assertIs(module.launch_context(sdk, headless=True), context)
                self.assertEqual(len(sdk.chromium.launch_persistent_context.call_args_list), 2)
                for call in sdk.chromium.launch_persistent_context.call_args_list:
                    self.assertEqual(call.kwargs["channel"], "chromium")
            sdk = Mock()
            sdk.chromium.launch_persistent_context.side_effect = [Error("first launch failed"), Mock()]
            with patch.object(wps_browser, "PROFILE_DIR", Path(directory) / "wps"):
                wps_browser.open_browser_context(sdk, "temporary", "", lambda _: None, for_login_check=True)
            for call in sdk.chromium.launch_persistent_context.call_args_list:
                self.assertEqual(call.kwargs["channel"], "chromium")
