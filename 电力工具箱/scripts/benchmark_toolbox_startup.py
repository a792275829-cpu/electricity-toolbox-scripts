from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from toolbox.app import ToolboxApp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eager", action="store_true", help="立即创建全部工具页")
    args = parser.parse_args()
    start = time.perf_counter()
    app = ToolboxApp(lazy_pages=not args.eager)
    app.withdraw()
    app.update_idletasks()
    elapsed = time.perf_counter() - start
    print(f"mode={'eager' if args.eager else 'lazy'} seconds={elapsed:.6f}")
    app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
