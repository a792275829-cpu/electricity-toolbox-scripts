"""Conservative, repeatable pruning of a staged desktop runtime before signing.

Keep licenses, package metadata, browser resources, all business dependencies and
Node/Python executables. Never run this against an installed application.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

# NumPy imports numpy._core.tests._natype at runtime; retain its test tree.
TEST_PACKAGES = ("pandas", "scipy", "sklearn", "openpyxl", "PIL", "docx")


def file_bytes(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*")
               if p.is_file() and not p.is_symlink())


def missing_browsers(runtime: Path) -> list[Path]:
    registry = runtime / "python/lib/python3.11/site-packages/playwright/driver/package/browsers.json"
    browsers = {b["name"]: b for b in json.loads(registry.read_text())["browsers"]}
    required = {"chromium": "chromium", "chromium-headless-shell": "chromium_headless_shell"}
    paths = [runtime / "ms-playwright" / f'{prefix}-{browsers[name]["revision"]}'
             for name, prefix in required.items()]
    return [path for path in paths if not path.is_dir()]


def prune(runtime: Path) -> dict:
    """Remove development inputs while retaining both browser executables."""
    runtime = runtime.resolve()
    if not (runtime / "python/bin/python3").is_file() or not (runtime / "node/bin/node").is_file():
        raise ValueError("待裁剪目录缺少 Python / Node 运行环境")
    if missing_browsers(runtime):
        raise ValueError("缺少锁定版本的 Chromium 或 headless shell，禁止交付不完整运行环境")
    # prepare_tk rebuilds _tkinter when the staging runtime is reused.
    # Keep Python headers (about 1 MiB) so a second build remains possible.
    candidates = [runtime / "node/include", runtime / "python/lib/pkgconfig"]
    site = runtime / "python/lib/python3.11/site-packages"
    for package in TEST_PACKAGES:
        candidates.extend(p for p in (site / package).rglob("tests") if p.is_dir())
    candidates.extend(runtime.rglob("__pycache__"))
    # Remove parents before children; repeated runs must be harmless.
    removed = []
    for path in sorted(set(candidates), key=lambda p: (len(p.parts), str(p))):
        if not path.exists() and not path.is_symlink():
            continue
        size = file_bytes(path)
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)
        removed.append({"path": path.relative_to(runtime).as_posix(), "bytes": size})
    return {"removed_bytes": sum(p["bytes"] for p in removed), "removed": removed}
