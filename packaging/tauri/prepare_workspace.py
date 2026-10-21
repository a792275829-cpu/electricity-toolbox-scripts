"""Install managed application sources, preserving local data and edited files."""

from __future__ import annotations
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe(root: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if (
        name.is_absolute()
        or not name.parts
        or any(p in ("..", ".") or "\\" in p or ":" in p for p in name.parts)
    ):
        raise ValueError(f"非法更新路径：{relative}")
    target = root.joinpath(*name.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("更新路径超出工作目录")
    return target


def install(payload: Path, root: Path, *, data_root: Path | None = None,
            state_root: Path | None = None, backup_root: Path | None = None):
    manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
    state = (state_root / "installed.json") if state_root else root / ".toolbox/desktop/installed.json"
    sources = payload / ("app" if (payload / "app").is_dir() else "workspace")
    data_root = data_root or root
    previous = (
        json.loads(state.read_text(encoding="utf-8"))
        if state.exists()
        else {"files": {}}
    )
    files = manifest["files"]
    old = previous["files"]
    # A content hash also distinguishes two development builds of one version.
    if previous.get("build") == manifest["build"]:
        return
    for name, expected in manifest.get("default_hashes", {}).items():
        source = safe(payload / "defaults", name)
        if not source.is_file() or digest(source) != expected:
            raise ValueError(f"首次安装资源损坏：{name}")
    conflicts = []
    changes = []
    for name in sorted(set(old) | set(files)):
        target = safe(root, name)
        if name in files:
            source = safe(sources, name)
            if not source.is_file() or digest(source) != files[name]:
                raise ValueError(f"发行文件损坏：{name}")
        current = digest(target) if target.is_file() else None
        if current == files.get(name):
            continue
        if current is not None and current != old.get(name):
            conflicts.append(name)
        else:
            changes.append(name)
    if conflicts:
        raise RuntimeError(
            "检测到本地源码修改，已保留原文件。请备份并移走以下文件后重启：\n"
            + "\n".join(conflicts[:20])
        )
    root.mkdir(parents=True, exist_ok=True)
    backup = (backup_root or root / ".toolbox/desktop/backups") / manifest["build"][:16]
    originals = []
    completed = []
    try:
        for name in changes:
            target = safe(root, name)
            if target.exists():
                copy = safe(backup, name)
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, copy)
                originals.append(name)
            if name in files:
                target.parent.mkdir(parents=True, exist_ok=True)
                handle, temporary = tempfile.mkstemp(
                    prefix=".toolbox-update-", dir=target.parent
                )
                os.close(handle)
                try:
                    shutil.copy2(safe(sources, name), temporary)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            else:
                target.unlink(missing_ok=True)
            completed.append(name)
        # Local configs are initialized once and never managed by application updates.
        for name in manifest.get("defaults", []):
            source = safe(payload / "defaults", name)
            target = safe(data_root, name)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        state.parent.mkdir(parents=True, exist_ok=True)
        temporary = state.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, state)
    except BaseException:
        for name in reversed(completed):
            target = safe(root, name)
            if name in originals:
                shutil.copy2(safe(backup, name), target)
            else:
                target.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    payload, root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    if "--managed" in sys.argv[3:]:
        # Python -I omits the script directory; load only our bundled sibling.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from workspace_layout import initialize
        initialize(root)
        install(payload, root / "app", data_root=root / "data", state_root=root / "state",
                backup_root=root / "backups/source-updates")
    else:
        install(payload, root)
