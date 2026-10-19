"""Copy the configured forecast workbook into a native app's local workspace."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from toolbox_credentials import read_json, write_private_json


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_paths(module_dir: Path) -> dict[str, Path]:
    spec = importlib.util.spec_from_file_location(
        "forecast_migration_paths", module_dir / "gd_price_forecast/paths.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.resolve_paths(module_dir)


def copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".forecast-migration-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        if digest(temporary) != digest(source):
            raise RuntimeError("总表复制校验失败，原文件未改动。")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def migrate(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or destination.is_relative_to(source):
        raise ValueError("迁移目标必须是原版工作目录之外的独立目录。")
    source_module = source / "广东电价预测"
    target_module = destination / "广东电价预测"
    paths = resolve_paths(source_module)
    if not paths["masterWorkbook"].is_file():
        raise FileNotFoundError("原版配置的数据总表不存在，停止迁移。")
    target_config = target_module / "config.json"
    previous = read_json(target_config)
    config = dict(previous)
    config["masterWorkbook"] = (Path("输出") / paths["masterWorkbook"].name).as_posix()
    config["predictionOutputDir"] = "输出/D+1预测"
    copies = [(paths["masterWorkbook"], target_module / config["masterWorkbook"])]
    if paths["initialWorkbook"].is_file():
        config["initialWorkbook"] = (Path("输入") / paths["initialWorkbook"].name).as_posix()
        copies.append((paths["initialWorkbook"], target_module / config["initialWorkbook"]))
    for path in (target_config, target_module / config["predictionOutputDir"], *(p for _, p in copies)):
        if not path.resolve().is_relative_to(destination):
            raise ValueError("新版数据路径超出目标工作目录，停止迁移。")
    # Repeated imports may verify the same workbook, but never replace different data.
    for original, target in copies:
        if target.exists() and (not target.is_file() or digest(target) != digest(original)):
            raise FileExistsError(f"新版已有不同数据，已保留，请先核对：{target}")
        if target.resolve().is_relative_to(source):
            raise ValueError("新版数据路径指向原版目录，停止迁移。")
    backup = (
        destination / ".toolbox/migrations"
        / datetime.now().strftime("forecast-%Y%m%d-%H%M%S-%f")
    )
    backup.mkdir(parents=True, mode=0o700)
    if target_config.exists():
        shutil.copyfile(target_config, backup / "config.json")
        (backup / "config.json").chmod(0o600)
    created = []
    config_written = False
    try:
        for original, target in copies:
            if not target.exists():
                copy_atomic(original, target)
                created.append(target)
        (target_module / config["predictionOutputDir"]).mkdir(parents=True, exist_ok=True)
        write_private_json(target_config, config)
        config_written = True
        actual = resolve_paths(target_module)
        if (
            actual["masterWorkbook"] != copies[0][1]
            or actual["predictionOutputDir"] != target_module / config["predictionOutputDir"]
        ):
            raise RuntimeError("新版预测配置路径回读不一致。")
        if read_json(target_config) != config:
            raise RuntimeError("新版预测配置内容回读不一致。")
        for original, target in copies:
            if digest(original) != digest(target):
                raise RuntimeError("迁移后总表回读校验失败。")
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        if config_written:
            if (backup / "config.json").exists():
                copy_atomic(backup / "config.json", target_config)
            else:
                target_config.unlink(missing_ok=True)
        raise
    report = {
        "master_workbook": str(copies[0][1]),
        "prediction_output_dir": str(target_module / config["predictionOutputDir"]),
        "copied_workbooks": len(created),
        "initial_workbook_available": paths["initialWorkbook"].is_file(),
        "byte_verification_passed": True,
        "backup": str(backup),
    }
    write_private_json(backup / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="迁移预测总表并设置新版业务路径（请先退出新版工具箱）"
    )
    parser.add_argument("--source", type=Path, required=True, help="原版工作目录")
    parser.add_argument("--destination", type=Path, required=True, help="新版用户数据工作目录")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination), ensure_ascii=False))
