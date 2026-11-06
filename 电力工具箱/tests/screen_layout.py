"""Skip fixed-size GUI assertions on displays that cannot show the window.

GitHub's macOS runners ship with different virtual screen sizes (macos-15
uses 1024x768).  Tk clamps the window to the screen there, so assertions
about a 1280x720 (or larger) layout would fail for an environment reason
rather than a layout regression.
"""
from __future__ import annotations

from toolbox.app import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH, SCREEN_EDGE_MARGIN


def skip_if_window_does_not_fit(
    case,
    app,
    width: int = MIN_WINDOW_WIDTH,
    height: int = MIN_WINDOW_HEIGHT,
) -> None:
    screen_width = app.winfo_screenwidth() - SCREEN_EDGE_MARGIN
    screen_height = app.winfo_screenheight() - SCREEN_EDGE_MARGIN
    if screen_width < width or screen_height < height:
        case.skipTest(
            f"屏幕 {app.winfo_screenwidth()}x{app.winfo_screenheight()} "
            f"放不下 {width}x{height} 的窗口，跳过固定尺寸布局断言"
        )
