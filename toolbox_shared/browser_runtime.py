"""Keep source Chrome preference and packaged Playwright browser defaults."""
from __future__ import annotations

import os


def chromium_launch_options(*, prefer_system: bool = False) -> dict[str, str]:
    # Leave the packaged channel unspecified: headless uses headless shell,
    # visible windows use full Chromium. Node retains its own existing policy.
    if os.environ.get("TOOLBOX_PORTABLE") == "1":
        return {}
    return {"channel": "chrome"} if prefer_system else {}
