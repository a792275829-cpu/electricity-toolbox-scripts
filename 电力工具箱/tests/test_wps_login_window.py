from __future__ import annotations

import unittest
from unittest import mock
from toolbox_shared import wps_browser as browser


class WpsLoginWindowTests(unittest.TestCase):
    def test_background_requests_preserve_visible_login_window_even_if_outdated(self):
        for version in (0, browser.WPS_CHROME_LAUNCH_VERSION):
            with self.subTest(version=version):
                with (
                    mock.patch.object(browser, 'managed_chrome_pid', return_value=123),
                    mock.patch.object(browser, 'managed_chrome_is_headless', return_value=False),
                    mock.patch.object(browser, 'managed_chrome_launch_version', return_value=version),
                    mock.patch.object(browser, 'stop_managed_chrome') as stop,
                ):
                    browser.stop_outdated_managed_chrome(mock.Mock(), 'local', headless=True)
                    stop.assert_not_called()

    def test_explicit_login_can_still_open_headless_browser(self):
        with (
            mock.patch.object(browser, 'managed_chrome_pid', return_value=123),
            mock.patch.object(browser, 'managed_chrome_is_headless', return_value=True),
            mock.patch.object(browser, 'managed_chrome_launch_version', return_value=browser.WPS_CHROME_LAUNCH_VERSION),
            mock.patch.object(browser, 'stop_managed_chrome') as stop,
        ):
            browser.stop_outdated_managed_chrome(mock.Mock(), 'local', headless=False)
        stop.assert_called_once()
        self.assertIn('登录', stop.call_args.args[2])

    def test_unresponsive_visible_browser_is_not_killed_by_background_start(self):
        with (
            mock.patch.object(browser, 'stop_outdated_managed_chrome'),
            mock.patch.object(browser, 'try_resolve_cdp_endpoint', return_value=None),
            mock.patch.object(browser, 'managed_chrome_pid', return_value=123),
            mock.patch.object(browser, 'managed_chrome_is_headless', return_value=False),
            mock.patch.object(browser.time_module, 'monotonic', side_effect=[0, 4]),
            mock.patch.object(browser, 'stop_managed_chrome') as stop,
        ):
            with self.assertRaisesRegex(RuntimeError, '已保留窗口'):
                browser._ensure_wps_dedicated_chrome('local', mock.Mock(), headless=True)
        stop.assert_not_called()

    def test_cdp_timeout_preserves_visible_browser_and_original_error(self):
        playwright = mock.Mock()
        error = browser.PlaywrightTimeoutError('连接超时')
        playwright.chromium.connect_over_cdp.side_effect = error
        with (
            mock.patch.object(browser, 'ensure_wps_dedicated_chrome'),
            mock.patch.object(browser, 'resolve_cdp_endpoint', return_value='local'),
            mock.patch.object(browser, 'managed_chrome_pid', return_value=123),
            mock.patch.object(browser, 'managed_chrome_is_headless', return_value=False),
            mock.patch.object(browser, 'stop_managed_chrome') as stop,
        ):
            with self.assertRaisesRegex(RuntimeError, '已保留窗口') as caught:
                browser.open_browser_context(playwright, 'managed_cdp', 'local', mock.Mock())
        self.assertIs(caught.exception.__cause__, error)
        stop.assert_not_called()
        playwright.chromium.connect_over_cdp.assert_called_once()


if __name__ == '__main__':
    unittest.main()
