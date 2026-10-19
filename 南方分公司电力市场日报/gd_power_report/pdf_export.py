"""Browser ownership for standalone and combined report PDF exports."""
from __future__ import annotations

from contextlib import contextmanager

from playwright.sync_api import Browser, sync_playwright


@contextmanager
def pdf_browser(browser: Browser | None = None):
    if browser is not None:
        yield browser
        return
    with sync_playwright() as playwright:
        owned_browser = playwright.chromium.launch(headless=True)
        try:
            yield owned_browser
        finally:
            owned_browser.close()
