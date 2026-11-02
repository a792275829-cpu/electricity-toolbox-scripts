"""Measure click-to-paint cost for every toolbox module.

Two modes matter for a "module opens slowly" report:

* cold  — the first navigation to a module in this process: imports its page
  module, builds the widget tree and paints it.
* warm  — navigating back to an already cached page: unpaint the previous page
  and paint this one.

Both include Tk's own drawing because the script calls ``update()`` until the
page is on screen; Python-side construction alone is a few milliseconds and
does not explain a user-visible hesitation.

The measurement runs in an isolated data directory and never starts a business
task, so no workbook is read or written.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE_ROOT), str(PACKAGE_ROOT.parent)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=PACKAGE_ROOT.parent,
                        help="包含 电力工具箱/ 的工作区；安装版请指向 payload/app")
    parser.add_argument("--geometry", default="", help="窗口尺寸，例如 1480x900；默认用应用自身尺寸")
    parser.add_argument("--modules", default="", help="只测指定模块，逗号分隔")
    parser.add_argument("--rounds", type=int, default=3, help="每个模块的再次打开次数")
    parser.add_argument("--output", type=Path, help="把结果写成 JSON")
    return parser.parse_args()


def measure(app, name: str, rounds: int, anchor: str) -> dict:
    started = time.perf_counter()
    app.show_page(name)
    _pump(app)
    cold = time.perf_counter() - started
    page = app.pages[name]
    warm = []
    for _ in range(rounds):
        if anchor != name and anchor in app.nav_buttons:
            app.show_page(anchor)
            _pump(app, 6)
        started = time.perf_counter()
        app.show_page(name)
        _pump(app)
        warm.append(time.perf_counter() - started)
    return {
        "module": name,
        "cold_ms": round(cold * 1000, 1),
        "warm_ms": round(statistics.median(warm) * 1000, 1) if warm else None,
        "page_height": page.winfo_reqheight(),
        "viewport_height": app.page_viewport.canvas.winfo_height(),
        "scrollbar": bool(app.page_viewport.vertical.winfo_manager()),
    }


def _pump(app, passes: int = 15) -> None:
    for _ in range(passes):
        app.update()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    os.environ["TOOLBOX_DATA_ROOT"] = tempfile.mkdtemp(prefix="toolbox-page-open-")
    from toolbox.app import ToolboxApp

    with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}):
        app = ToolboxApp(workspace=workspace)
    try:
        if args.geometry:
            app.geometry(args.geometry)
        app.update()
        names = list(app.nav_buttons)
        if args.modules:
            requested = [item.strip() for item in args.modules.split(",") if item.strip()]
            unknown = [item for item in requested if item not in names]
            if unknown:
                raise SystemExit(f"未知模块：{'、'.join(unknown)}")
            names = requested
        anchor = names[0]
        rows = [measure(app, name, max(1, args.rounds), anchor) for name in names]
        width, height = app.winfo_width(), app.winfo_height()
    finally:
        app.destroy()

    header = f"{'模块':<14}{'首次(ms)':>10}{'再次(ms)':>10}{'页面高':>8}{'视口高':>8}  滚动条"
    print(f"工作区：{workspace}\n窗口：{width}x{height}  Tk：{sys.version.split()[0]}")
    print(header)
    print("-" * len(header))
    for row in rows:
        warm = "-" if row["warm_ms"] is None else f"{row['warm_ms']:g}"
        print(f"{row['module']:<14}{row['cold_ms']:>10.1f}{warm:>10}"
              f"{row['page_height']:>8}{row['viewport_height']:>8}"
              f"  {'是' if row['scrollbar'] else '否'}")
    slow = sorted(rows, key=lambda row: row["cold_ms"], reverse=True)[:5]
    print("\n首次打开最慢：" + "、".join(f"{row['module']} {row['cold_ms']:g}ms" for row in slow))
    if args.output:
        args.output.write_text(json.dumps({
            "workspace": str(workspace), "window": f"{width}x{height}", "rows": rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
