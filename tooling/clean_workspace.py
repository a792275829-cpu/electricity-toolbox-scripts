"""Reclaim workspace space without touching anything that cannot be rebuilt.

``dist`` is treated as a pure artifact directory: only the newest ``--keep``
installers, their digests, ``updates/`` (incremental patches), ``tauri-build/``
(the packaging download cache) and migration ``*.zip`` deliverables survive.
Anything else there is release staging left behind by a publish run and belongs
under ``logs/release-<tag>/`` instead.

``--archive-logs`` keeps the top of ``logs/`` readable: one-off verification
reports and older ``release-v*`` copies are moved into
``logs/release-archive/`` (never deleted), keeping the archive itself, the test
reports and the newest ``--keep-release`` release directories in place.

Dry run by default; pass ``--apply`` to delete (or, with ``--archive-logs``,
to move).
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
LOGS = ROOT / "logs"

INSTALLER_RE = re.compile(r"^ElectricityToolbox-(\d+)\.(\d+)\.(\d+)-apple-silicon\.dmg$")
RELEASE_DIR_RE = re.compile(r"^release-v(\d+)\.(\d+)\.(\d+)$")
BUILD_DIRS = ("debug", "release", "aarch64-apple-darwin")
# Products other tooling depends on: patch index, download cache, migration samples.
KEEP_NAMES = {"updates", "tauri-build"}
KEEP_SUFFIXES = (".zip",)
# logs/ entries that stay at the top level when archiving.
LOG_ARCHIVE = "release-archive"
LOG_KEEP_NAMES = {LOG_ARCHIVE, "tests.json", "ci-tests.json"}


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


def kept_installers(names, keep):
    versions = installer_versions(names)
    ordered = sorted(versions, reverse=True)
    return {versions[version] for version in ordered[:keep]}


def plan(keep, *, dist=DIST, target=TARGET, payload=PAYLOAD):
    """List every path that :func:`apply` would delete."""

    items = []
    names = sorted(path.name for path in dist.glob("*.dmg"))
    keep_set = kept_installers(names, keep)
    for name in stale_installers(names, keep):
        items.append(dist / name)
        digest = dist / f"{name}.sha256"
        if digest.exists():
            items.append(digest)

    if dist.is_dir():
        for entry in sorted(dist.iterdir()):
            name = entry.name
            if name in KEEP_NAMES or name.endswith(KEEP_SUFFIXES):
                continue
            if name in keep_set or name in {f"{kept}.sha256" for kept in keep_set}:
                continue
            items.append(entry)

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


def kept_release_dirs(names, keep):
    """Newest ``release-vX.Y.Z`` directories that stay visible in ``logs/``."""

    if keep < 0:
        raise ValueError("--keep-release 不能为负数")
    versions = {}
    for name in names:
        match = RELEASE_DIR_RE.match(name)
        if match:
            versions[tuple(int(part) for part in match.groups())] = name
    ordered = sorted(versions, reverse=True)
    return {versions[version] for version in ordered[:keep]}


def plan_logs(*, logs=LOGS, keep_release=2):
    """Top-level ``logs/`` entries that :func:`archive` would move away."""

    if not logs.is_dir():
        return []
    keep = kept_release_dirs([path.name for path in logs.iterdir() if path.is_dir()], keep_release)
    items = []
    for entry in sorted(logs.iterdir()):
        if entry.name in LOG_KEEP_NAMES or entry.name in keep:
            continue
        if entry == logs / LOG_ARCHIVE:
            continue
        items.append(entry)
    return items


def archive(items, *, logs=LOGS):
    """Move entries into ``logs/release-archive/`` without deleting anything."""

    destination = logs / LOG_ARCHIVE
    destination.mkdir(parents=True, exist_ok=True)
    moved = []
    for path in items:
        path = Path(path)
        if not path.exists() or path.parent != logs:
            continue
        target = destination / path.name
        index = 1
        while target.exists():
            target = destination / f"{path.name}-{index}"
            index += 1
        shutil.move(str(path), str(target))
        moved.append((path.name, target.name))
    return moved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=int, default=2, help="保留最新几个版本的安装包")
    parser.add_argument("--keep-build", action="store_true",
                        help="保留 target 与 payload，只清理安装包与 dist 残留")
    parser.add_argument("--archive-logs", action="store_true",
                        help="只把 logs/ 顶层的一次性报告与旧版释放目录移入 logs/release-archive/")
    parser.add_argument("--keep-release", type=int, default=2,
                        help="logs/ 顶层保留最新几个 release-v* 目录（配合 --archive-logs）")
    parser.add_argument("--apply", action="store_true", help="真正执行；默认只列出")
    args = parser.parse_args(argv)

    if args.archive_logs:
        items = plan_logs(keep_release=args.keep_release)
        total = sum(path_size(item) for item in items)
        for item in items:
            print(f"{human(path_size(item)):>7}  {item.name}")
        print(f"{len(items)} 项，合计 {human(total)}，将移入 logs/{LOG_ARCHIVE}/")
        if not args.apply:
            print("干跑：确认后加 --apply 执行归档（只移动，不删除）")
            return 0
        moved = archive(items)
        print(f"已归档 {len(moved)} 项；logs/ 顶层剩余 {len(list(LOGS.iterdir()))} 项")
        return 0

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
