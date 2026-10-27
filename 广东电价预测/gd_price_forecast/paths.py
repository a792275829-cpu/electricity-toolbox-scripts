"""Shared business paths for the updater, prediction CLI and toolbox page."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from toolbox_shared.storage import data_path

MODULE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MASTER_WORKBOOK = data_path(MODULE_DIR) / "输出" / "广东电价数据总表.xlsx"
DEFAULT_OUTPUT_DIR = data_path(MODULE_DIR) / "输出" / "D+1预测"
DEFAULT_BASE_WORKBOOK = data_path(MODULE_DIR) / "输入" / "广东数据初始总表.xlsx"


def resolve_module_path(value: str | Path, module_dir: Path = MODULE_DIR) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else data_path(module_dir) / path).resolve()


def resolve_paths(
    module_dir: Path = MODULE_DIR, config_path: Path | None = None
) -> dict[str, Path]:
    config_path = config_path or data_path(module_dir / "config.json")
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError(f"模块配置应为 JSON 对象：{config_path}")
    defaults = {
        "masterWorkbook": "输出/广东电价数据总表.xlsx",
        "predictionOutputDir": "输出/D+1预测",
        "initialWorkbook": "输入/广东数据初始总表.xlsx",
    }
    return {
        key: resolve_module_path(config.get(key) or default, module_dir)
        for key, default in defaults.items()
    }
