"""Transactional migration of the old mixed desktop workspace to layout v2."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath

DIRECTORIES = ("app", "data", "samples", "state", "backups", "logs", "cache")
PATH_CONFIGS = {"config.json", "upload.config.json", "wps_excel_to_kdocs_config.json", "file_dialog_settings.json"}


def safe(root: Path, name: str) -> Path:
    parts = PurePosixPath(name)
    if parts.is_absolute() or not parts.parts or any(p == ".." or "\\" in p or ":" in p for p in parts.parts):
        raise ValueError("非法目录迁移路径")
    target = root.joinpath(*parts.parts)
    # Inspect parents only: Chromium profile symlinks are moved without following them.
    if not target.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("目录迁移路径超出用户目录")
    return target


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def destination(name: str, managed: set[str]) -> str:
    if name == "测试样例":
        return "samples"
    if name.startswith("测试样例/"):
        return "samples/" + name.removeprefix("测试样例/")
    if name == ".toolbox/desktop/installed.json":
        return "state/installed.json"
    if name.startswith(".toolbox/desktop/backups/"):
        return "backups/source-updates/" + name.removeprefix(".toolbox/desktop/backups/")
    if name.startswith(".toolbox/migrations/"):
        return "backups/migrations/" + name.removeprefix(".toolbox/migrations/")
    if "__pycache__" in PurePosixPath(name).parts:
        return "cache/legacy/" + name
    return ("app/" if name in managed else "data/") + name


def entries(root: Path):
    for folder, directories, files in os.walk(root, followlinks=False):
        parent = Path(folder)
        for name in list(directories):
            path = parent / name
            if path.is_symlink():
                directories.remove(name)
                yield path
        for name in files:
            yield parent / name


def exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def recover(home: Path) -> None:
    journal = home / "state/layout-pending.json"
    if not journal.exists():
        return
    plan = json.loads(journal.read_text(encoding="utf-8"))
    for name in plan.get("rewritten_configs", []):
        backup = safe(home / "backups/layout-v1-configs", name)
        target = safe(home, name)
        if backup.is_file() and target.is_file():
            shutil.copy2(backup, target)
    for source_name, target_name in reversed(plan["moves"]):
        source, target = safe(home, source_name), safe(home, target_name)
        if exists(source) and exists(target):
            raise RuntimeError("目录迁移恢复遇到冲突，已保留两份文件。")
        if exists(target):
            source.parent.mkdir(parents=True, exist_ok=True)
            target.rename(source)
        elif not exists(source):
            raise RuntimeError("目录迁移恢复缺少文件，已停止。")
    journal.unlink()


def initialize(home: Path, *, reference_root: Path | None = None) -> dict:
    home = home.resolve()
    state = home / "state/layout.json"
    if state.exists():
        result = json.loads(state.read_text(encoding="utf-8"))
        if result.get("version") != 2:
            raise ValueError("无法识别桌面目录版本")
        # A crash after committing the marker does not require a rollback.
        (home / "state/layout-pending.json").unlink(missing_ok=True)
        return result
    recover(home)
    legacy = home / "workspace"
    previous = legacy / ".toolbox/desktop/installed.json"
    if legacy.exists() and not previous.is_file():
        raise RuntimeError("旧版工作目录缺少发行清单，无法安全区分源码和用户数据。")
    managed = set(json.loads(previous.read_text(encoding="utf-8"))["files"]) if previous.is_file() else set()
    moves = [("workspace/" + p.relative_to(legacy).as_posix(), destination(p.relative_to(legacy).as_posix(), managed))
             for p in entries(legacy)] if legacy.exists() else []
    targets = set()
    for source, target in moves:
        key = target.casefold()
        if key in targets or exists(safe(home, target)):
            raise FileExistsError("新目录已有文件，已停止迁移以保留用户数据。")
        targets.add(key)
    for name in DIRECTORIES:
        (home / name).mkdir(parents=True, exist_ok=True)
    journal = home / "state/layout-pending.json"
    plan = {"moves": moves, "rewritten_configs": []}
    write_json(journal, plan)
    counts = {name: 0 for name in DIRECTORIES}
    try:
        for source_name, target_name in moves:
            source, target = safe(home, source_name), safe(home, target_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            counts[target_name.split("/", 1)[0]] += 1

        def rewrite(value):
            if isinstance(value, dict):
                return {k: rewrite(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rewrite(v) for v in value]
            if isinstance(value, str) and Path(value).is_absolute():
                for reference in (legacy, reference_root):
                    if reference is None:
                        continue
                    try:
                        relative = Path(value).resolve().relative_to(reference.resolve()).as_posix()
                    except ValueError:
                        continue
                    return str(safe(home, destination(relative, managed)))
            return value

        for _, target_name in moves:
            path = safe(home, target_name)
            if not target_name.startswith("data/") or path.name not in PATH_CONFIGS or path.is_symlink():
                continue
            if {".browser-profile", ".browser-profile-uploader", "wps-browser-profile"}.intersection(path.parts):
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            updated = rewrite(raw)
            if raw == updated:
                continue
            relative = path.relative_to(home).as_posix()
            backup = safe(home / "backups/layout-v1-configs", relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            backup.chmod(0o600)
            plan["rewritten_configs"].append(relative)
            write_json(journal, plan)
            write_json(path, updated)
        result = {"version": 2, "moved_files": counts, "rewritten_configs": len(plan["rewritten_configs"])}
        write_json(state, result)
    except BaseException:
        recover(home)
        raise
    journal.unlink(missing_ok=True)
    # Only empty legacy directories are removed; no recursive deletion of user files.
    if legacy.exists():
        for folder, _, _ in os.walk(legacy, topdown=False):
            Path(folder).rmdir()
    return result


def import_legacy(source: Path, home: Path) -> dict:
    """Make one isolated development copy, without altering installed user data."""
    source, home = source.resolve(), home.resolve()
    if home == source or home.is_relative_to(source) or source.is_relative_to(home):
        raise ValueError("开发目录不能包含旧工作目录，旧工作目录也不能包含开发目录。")
    if home.exists() and any(home.iterdir()):
        raise FileExistsError("开发目录已有数据，已保留；不能重复导入覆盖。")
    if not (source / ".toolbox/desktop/installed.json").is_file():
        raise ValueError("导入来源缺少原安装清单。")
    shutil.copytree(source, home / "workspace", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.lock", "SingletonLock", "SingletonCookie", "SingletonSocket"))
    return initialize(home, reference_root=source)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="初始化或迁移 Tauri 用户目录（不构建安装包）")
    parser.add_argument("home", type=Path)
    parser.add_argument("--import-from", type=Path, help="从旧目录复制一份开发数据，保留来源目录")
    args = parser.parse_args()
    result = import_legacy(args.import_from, args.home) if args.import_from else initialize(args.home)
    print(json.dumps(result, ensure_ascii=False))
