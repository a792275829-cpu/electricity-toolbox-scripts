"""Reclaim workspace space without touching anything that cannot be rebuilt.

Keeps the newest ``--keep`` installers in ``dist`` together with the incremental
patch assets and the download cache the packaging scripts reuse. Everything else
it reports is either a duplicate copy or a build output that a rebuild recreates.

Dry run by default; pass ``--apply`` to delete.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TARGET = ROOT / "desktop/src-tauri/target"
PAYLOAD = ROOT / "desktop/src-tauri/payload"

INSTALLER_RE = re.compile(r"^ElectricityToolbox-(\d+)\.(\d+)\.(\d+)-apple-silicon\.dmg$")
BUILD_DIRS = ("debug", "release", "aarch64-apple-darwin")


def installer_versions(names):
    """Map version tuples to installer file names, ignoring unrelated files."""

    found = {}
    for name in names:
        match = INSTALLER_RE.match(name)
        if match:
            found[tuple(int(part) for part in match.groups())] = name
    return found


def stale_installers(names, keep):
    """Return the installers older than the newest ``keep`` versions."""

    if keep < 1:
        raise ValueError("--keep 至少为 1，否则会删掉所有安装包")
    versions = installer_versions(names)
    ordered = sorted(versions, reverse=True)
    return [versions[version] for version in ordered[keep:]]


def plan(keep, *, dist=DIST, target=TARGET, payload=PAYLOAD):
    """List every path that :func:`apply` would delete, keeping newest installers."""

    items = []
    installers = sorted(path.name for path in dist.glob("*.dmg"))
    for name in stale_installers(installers, keep):
        items.append(dist / name)
        digest = dist / f"{name}.sha256"
        if digest.exists():
            items.append(digest)

    for release in sorted(dist.glob("release-v*")):
        if not release.is_dir():
            continue
        # The versioned installer at the dist root is the single copy worth keeping.
        for copy in sorted(release.glob("*.dmg")):
            items.append(copy)
            digest = release / f"{copy.name}.sha256"
            if digest.exists():
                items.append(digest)
        transfer = release / "transfer"
        if transfer.is_dir():
            items.append(transfer)

    for name in BUILD_DIRS:
        candidate = target / name
        if candidate.exists():
            items.append(candidate)
    if payload.exists():
        items.append(payload)
    return items


def path_size(path):
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size
        total = 0
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        return total
    except OSError:
        return 0


def human(size):
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def apply(items):
    removed = 0
    for path in items:
        if not path.exists() and not path.is_symlink():
            continue
        removed += path_size(path)
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=int, default=2, help="保留最新几个版本的安装包")
    parser.add_argument("--keep-build", action="store_true",
                        help="保留 target 与 payload，只清理安装包副本")
    parser.add_argument("--apply", action="store_true", help="真正删除；默认只列出")
    args = parser.parse_args(argv)

    items = plan(args.keep)
    if args.keep_build:
        items = [item for item in items
                 if item != PAYLOAD and TARGET not in item.parents]
    total = sum(path_size(item) for item in items)
    for item in items:
        print(f"{human(path_size(item)):>7}  {item.relative_to(ROOT)}")
    print(f"{len(items)} 项，合计 {human(total)}")
    if not args.apply:
        print("干跑：确认后加 --apply 执行删除")
        return 0
    removed = apply(items)
    print(f"已删除 {human(removed)}；当前工作区 {human(path_size(ROOT))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
