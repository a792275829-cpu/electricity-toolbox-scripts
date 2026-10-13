"""Restore this local hotfix only when none of its files have changed since."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


def restore(backup: Path, *, check_only: bool = False) -> None:
    home = Path(os.environ["APPDATA"]) / "com.electricity.toolbox"
    backup = backup.resolve()
    if not backup.is_relative_to((home / "backups").resolve()):
        raise ValueError("备份必须位于工具箱 backups 目录")
    report = json.loads((backup / "repair.json").read_text(encoding="utf-8"))
    targets = [(home / "app" / name, backup / name, expected)
               for name, expected in report["patched_hashes"].items()]
    targets.append((home / "state/installed.json", backup / "state/installed.json", report["patched_state_hash"]))
    for target, saved, expected in targets:
        if not target.resolve().is_relative_to(home.resolve()) or not saved.resolve().is_relative_to(backup):
            raise ValueError("非法恢复路径")
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"文件已有后续修改或升级，停止恢复：{target}")
    if check_only:
        print("恢复条件检查通过，未修改文件。")
        return
    # All hashes are checked before any file is replaced; account/business data
    # are outside this list. Close the application before invoking restoration.
    for target, saved, _ in targets:
        if saved.is_file():
            temporary = target.with_suffix(target.suffix + ".restore.tmp")
            shutil.copy2(saved, temporary)
            os.replace(temporary, target)
        else:
            target.unlink()
    print("已恢复修复前的源码和安装记录。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    restore(args.backup, check_only=args.check)
