"""Conservative, repeatable pruning of a staged desktop runtime before signing.

Keep licenses, package metadata, browser resources, all business dependencies and
one Node executable per consumer. Never run this against an installed application.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

# NumPy imports numpy._core.tests._natype at runtime; retain its test tree.
TEST_PACKAGES = ("pandas", "scipy", "sklearn", "openpyxl", "PIL", "docx",
                 "greenlet", "joblib")
# The bundled interpreter only runs application code; installing packages is a
# build-machine job, so pip/setuptools and their shims stay out of the payload.
BUILD_TOOLING = ("pip", "setuptools", "_distutils_hack", "pkg_resources")
# _tkinter links Tcl/Tk 8.6 only; the 9.0 tree belongs to Tk 9 interpreters.
UNUSED_TCL = ("lib/tcl9.0", "lib/tk9.0", "lib/libtcl9.0.dylib",
              "lib/libtcl9tk9.0.dylib")
# Playwright's Python wheel already ships a Node build for its own driver, and
# the JavaScript uploader runs on that same interpreter.
DRIVER_NODE = Path("playwright/driver/node")
PYTHON_ENTRYPOINTS = ("python3", "python")


def file_bytes(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*")
               if p.is_file() and not p.is_symlink())


def driver_node(python: Path) -> Path | None:
    """Return the Node executable bundled inside the Playwright Python wheel."""
    for candidate in sorted(python.glob(f"lib/python3.*/site-packages/{DRIVER_NODE}")):
        if candidate.is_file():
            return candidate
    return None


def missing_browsers(runtime: Path) -> list[Path]:
    registry = runtime / "python/lib/python3.11/site-packages/playwright/driver/package/browsers.json"
    browsers = {b["name"]: b for b in json.loads(registry.read_text())["browsers"]}
    required = {"chromium": "chromium", "chromium-headless-shell": "chromium_headless_shell"}
    paths = [runtime / "ms-playwright" / f'{prefix}-{browsers[name]["revision"]}'
             for name, prefix in required.items()]
    return [path for path in paths if not path.is_dir()]


def entrypoint(python: Path) -> Path | None:
    for name in ("python3.11",) + PYTHON_ENTRYPOINTS:
        path = python / "bin" / name
        if path.is_file():
            return path
    return None


def dedupe_entrypoints(python: Path) -> int:
    """Hard-link duplicate interpreter copies so only one copy occupies the disk."""
    base = entrypoint(python)
    if base is None:
        return 0
    saved = 0
    for name in PYTHON_ENTRYPOINTS:
        alias = python / "bin" / name
        if alias == base or not alias.is_file() or alias.is_symlink():
            continue
        try:
            if os.path.samefile(alias, base):
                continue
            size = file_bytes(alias)
            alias.unlink()
            os.link(base, alias)
            saved += size
        except OSError:
            # Keep a working interpreter even when the filesystem refuses links.
            if not alias.exists():
                shutil.copy2(base, alias)
    return saved


def prune(runtime: Path) -> dict:
    """Remove build-only inputs while retaining both browser executables."""
    runtime = runtime.resolve()
    python = runtime / "python"
    if entrypoint(python) is None:
        raise ValueError("待裁剪目录缺少 Python 运行环境")
    bundled_node = driver_node(python)
    if bundled_node is None and not (runtime / "node/bin/node").is_file():
        raise ValueError("待裁剪目录缺少 Node 运行环境")
    if missing_browsers(runtime):
        raise ValueError("缺少锁定版本的 Chromium 或 headless shell，禁止交付不完整运行环境")
    # prepare_tk rebuilds _tkinter when the staging runtime is reused.
    # Keep Python headers (about 1 MiB) so a second build remains possible.
    candidates = [runtime / "node/include", python / "lib/pkgconfig",
                  python / "lib/python3.11/ensurepip"]
    if bundled_node is not None:
        # Playwright's driver Node replaces the separately staged tree.
        candidates.append(runtime / "node")
    site = python / "lib/python3.11/site-packages"
    for package in TEST_PACKAGES:
        root = site / package
        candidates.extend(p for name in ("tests", "test")
                          for p in root.rglob(name) if p.is_dir())
    candidates.extend(site / package for package in BUILD_TOOLING)
    # The distutils shim's .pth breaks interpreter startup once setuptools is gone.
    candidates.append(site / "distutils-precedence.pth")
    candidates.extend(python / relative for relative in UNUSED_TCL)
    candidates.extend(runtime.rglob("__pycache__"))
    # Remove parents before children; repeated runs must be harmless.
    removed = []
    for path in sorted(set(candidates), key=lambda p: (len(p.parts), str(p))):
        if not path.exists() and not path.is_symlink():
            continue
        size = file_bytes(path)
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        removed.append({"path": path.relative_to(runtime).as_posix(), "bytes": size})
    return {"removed_bytes": sum(p["bytes"] for p in removed), "removed": removed,
            "deduped_bytes": dedupe_entrypoints(python)}
